"""Trace analysis and comparison tools.

Provides methods for:
- Comparing paired traces (benign vs malignant)
- Computing Jaccard distance between event sets
- Analyzing graph structure differences
- Generating analysis reports
"""

import hashlib
import json
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from agentgraph import (
    EntityGraphBuilder,
    JSONLTraceParser,
    Trace,
    TraceEvent,
    TraceEventType,
)
from agentgraph.graph_builder import EntityGraph

logger = logging.getLogger(__name__)


@dataclass
class TraceDiff:
    """Difference between two traces."""

    execution_id: str
    benign_trace_id: str
    malignant_trace_id: str

    # Structural differences
    benign_num_events: int = 0
    malignant_num_events: int = 0
    event_count_diff: int = 0

    # Content differences
    content_differences: int = 0
    total_content_fields: int = 0
    content_change_pct: float = 0.0

    # LEP fields
    lep_events_malignant: int = 0
    lep_codes: List[str] = field(default_factory=list)

    # Tool differences
    benign_tool_calls: Counter = field(default_factory=Counter)
    malignant_tool_calls: Counter = field(default_factory=Counter)
    tool_call_diffs: Dict[str, int] = field(default_factory=dict)

    # Agent differences
    benign_agent_sequence: List[str] = field(default_factory=list)
    malignant_agent_sequence: List[str] = field(default_factory=list)

    # Structural hash
    benign_structure_hash: str = ""
    malignant_structure_hash: str = ""

    @property
    def structural_similarity(self) -> float:
        """How similar the traces are structurally (0-1)."""
        if self.benign_num_events == 0 and self.malignant_num_events == 0:
            return 1.0
        min_events = min(self.benign_num_events, self.malignant_num_events)
        max_events = max(self.benign_num_events, self.malignant_num_events)
        return min_events / max_events if max_events > 0 else 1.0

    @property
    def is_highly_different(self) -> bool:
        """Whether the traces are meaningfully different."""
        return self.content_change_pct > 0.05 or len(self.lep_codes) > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "structural_similarity": self.structural_similarity,
            "content_change_pct": self.content_change_pct,
            "lep_codes": self.lep_codes,
            "is_highly_different": self.is_highly_different,
        }


class JaccardDistance:
    """Compute Jaccard distance between sets of events.

    Jaccard distance = 1 - (|intersection| / |union|)
    Used to measure dissimilarity between event sequences.
    """

    @staticmethod
    def compute(set_a: Set[Any], set_b: Set[Any]) -> float:
        """Compute Jaccard distance between two sets.

        Args:
            set_a: First set.
            set_b: Second set.

        Returns:
            Jaccard distance (0 = identical, 1 = completely different).
        """
        intersection = len(set_a & set_b)
        union = len(set_a | set_b)
        if union == 0:
            return 0.0
        return 1.0 - (intersection / union)

    @staticmethod
    def compute_tool_sequences(
        benign_events: List[TraceEvent],
        malignant_events: List[TraceEvent],
    ) -> float:
        """Compute Jaccard distance between tool call sequences.

        Args:
            benign_events: Events from benign trace.
            malignant_events: Events from malignant trace.

        Returns:
            Jaccard distance between tool call sets.
        """
        benign_tools = {
            (e.tool_name, e.agent_name)
            for e in benign_events
            if e.event_type == TraceEventType.TOOL_CALL
        }
        malignant_tools = {
            (e.tool_name, e.agent_name)
            for e in malignant_events
            if e.event_type == TraceEventType.TOOL_CALL
        }
        return JaccardDistance.compute(benign_tools, malignant_tools)

    @staticmethod
    def compute_event_type_sequences(
        benign_events: List[TraceEvent],
        malignant_events: List[TraceEvent],
    ) -> float:
        """Compute Jaccard distance between event type sequences.

        Args:
            benign_events: Events from benign trace.
            malignant_events: Events from malignant trace.

        Returns:
            Jaccard distance between event type sets.
        """
        benign_types = {e.event_type.value for e in benign_events}
        malignant_types = {e.event_type.value for e in malignant_events}
        return JaccardDistance.compute(benign_types, malignant_types)


class TraceAnalyzer:
    """Analyze and compare paired traces.

    Provides methods for:
    - Computing differences between benign/malignant traces
    - Identifying LEP injection points
    - Analyzing graph-level differences
    """

    # Content fields to compare (exclude metadata fields)
    CONTENT_FIELDS = [
        "source", "target", "event_type", "input_summary", "output_summary",
        "agent_id", "agent_name", "agent_role", "tool_id", "tool_name",
        "expected_behavior", "observed_behavior",
    ]

    def __init__(self) -> None:
        self._diffs: List[TraceDiff] = []

    def compare_traces(self, benign: Trace, malignant: Trace) -> TraceDiff:
        """Compare a pair of traces and compute differences.

        Args:
            benign: Benign trace.
            malignant: Malignant trace.

        Returns:
            TraceDiff object with all differences.
        """
        assert benign.execution_id == malignant.execution_id, \
            "Traces must share the same execution_id"

        diff = TraceDiff(
            execution_id=benign.execution_id,
            benign_trace_id=benign.trace_id,
            malignant_trace_id=malignant.trace_id,
        )

        # Structural differences
        diff.benign_num_events = benign.num_events
        diff.malignant_num_events = malignant.num_events
        diff.event_count_diff = abs(benign.num_events - malignant.num_events)

        # Content differences
        content_diffs = 0
        content_total = 0

        for be, me in zip(benign.events, malignant.events):
            for field in self.CONTENT_FIELDS:
                bv = str(be.to_dict().get(field, ""))
                mv = str(me.to_dict().get(field, ""))
                content_total += 1
                if bv != mv:
                    content_diffs += 1

        diff.content_differences = content_diffs
        diff.total_content_fields = content_total
        diff.content_change_pct = (
            content_diffs / content_total if content_total > 0 else 0.0
        )

        # LEP analysis
        lep_events = [e for e in malignant.events if e.lep_injected]
        diff.lep_events_malignant = len(lep_events)
        diff.lep_codes = list({e.lep_type.split()[0] for e in lep_events if e.lep_type})

        # Tool call differences
        diff.benign_tool_calls = Counter(
            e.tool_name for e in benign.events
            if e.event_type == TraceEventType.TOOL_CALL
        )
        diff.malignant_tool_calls = Counter(
            e.tool_name for e in malignant.events
            if e.event_type == TraceEventType.TOOL_CALL
        )

        all_tools = set(diff.benign_tool_calls.keys()) | set(diff.malignant_tool_calls.keys())
        diff.tool_call_diffs = {
            tool: diff.malignant_tool_calls.get(tool, 0) - diff.benign_tool_calls.get(tool, 0)
            for tool in all_tools
        }

        # Agent sequences
        diff.benign_agent_sequence = [
            e.agent_name for e in benign.events
            if e.event_type == TraceEventType.REASONING
        ]
        diff.malignant_agent_sequence = [
            e.agent_name for e in malignant.events
            if e.event_type == TraceEventType.REASONING
        ]

        # Structural hash (content-independent)
        diff.benign_structure_hash = self._compute_structure_hash(benign)
        diff.malignant_structure_hash = self._compute_structure_hash(malignant)

        self._diffs.append(diff)
        return diff

    def compare_pairs(self, pairs: List[Tuple[Trace, Trace]]) -> List[TraceDiff]:
        """Compare multiple trace pairs.

        Args:
            pairs: List of (benign, malignant) trace pairs.

        Returns:
            List of TraceDiff objects.
        """
        diffs = []
        for benign, malignant in pairs:
            diffs.append(self.compare_traces(benign, malignant))
        return diffs

    def analyze_graphs(
        self,
        benign_graphs: List[EntityGraph],
        malignant_graphs: List[EntityGraph],
    ) -> Dict[str, Any]:
        """Analyze differences between graph pairs.

        Args:
            benign_graphs: List of benign entity graphs.
            malignant_graphs: List of malignant entity graphs.

        Returns:
            Analysis summary dictionary.
        """
        analysis = {
            "num_pairs": min(len(benign_graphs), len(malignant_graphs)),
            "benign_avg_nodes": sum(g.num_nodes for g in benign_graphs) / max(len(benign_graphs), 1),
            "malignant_avg_nodes": sum(g.num_nodes for g in malignant_graphs) / max(len(malignant_graphs), 1),
            "benign_avg_edges": sum(g.num_edges for g in benign_graphs) / max(len(benign_graphs), 1),
            "malignant_avg_edges": sum(g.num_edges for g in malignant_graphs) / max(len(malignant_graphs), 1),
        }
        return analysis

    def _compute_structure_hash(self, trace: Trace) -> str:
        """Compute a hash of the trace structure (ignoring content).

        This captures the event type sequence and agent/tool patterns
        without including the actual content.
        """
        elements = []
        for e in trace.events:
            elements.append(f"{e.event_type.value}:{e.source}:{e.target}")
        raw = "|".join(elements)
        return hashlib.md5(raw.encode()).hexdigest()[:16]

    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all comparisons performed.

        Returns:
            Summary dictionary with aggregated statistics.
        """
        if not self._diffs:
            return {}

        return {
            "num_comparisons": len(self._diffs),
            "avg_content_change_pct": sum(d.content_change_pct for d in self._diffs) / len(self._diffs),
            "max_content_change_pct": max(d.content_change_pct for d in self._diffs),
            "traces_with_lep": sum(1 for d in self._diffs if d.lep_codes),
            "avg_structural_similarity": sum(d.structural_similarity for d in self._diffs) / len(self._diffs),
            "lep_codes_found": list(set(
                code for d in self._diffs for code in d.lep_codes
            )),
        }


# Convenience functions

def analyze_pairs(
    pairs: List[Tuple[Trace, Trace]],
) -> Tuple[List[TraceDiff], Dict[str, Any]]:
    """Analyze trace pairs and return diffs + summary.

    Args:
        pairs: List of (benign, malignant) trace pairs.

    Returns:
        Tuple of (list of TraceDiff, summary dict).
    """
    analyzer = TraceAnalyzer()
    diffs = analyzer.compare_pairs(pairs)
    summary = analyzer.get_summary()
    return diffs, summary


def analyze_graphs(
    benign_graphs: List[EntityGraph],
    malignant_graphs: List[EntityGraph],
) -> Dict[str, Any]:
    """Analyze graph structure differences.

    Args:
        benign_graphs: List of benign graphs.
        malignant_graphs: List of malignant graphs.

    Returns:
        Analysis summary dictionary.
    """
    builder = EntityGraphBuilder()
    return builder.compare_graphs(benign_graphs, malignant_graphs)
