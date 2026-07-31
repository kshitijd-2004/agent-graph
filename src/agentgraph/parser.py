"""JSONL trace parser for AgentGraphs.

Reads trace files in the JSONL format where each line is a JSON object
representing one TraceEvent. Supports loading single files or entire
directories of paired traces.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from agentgraph.trace import (
    Trace,
    TraceEvent,
    TraceVariant,
)


class JSONLTraceParser:
    """Parse JSONL trace files into Trace objects.

    Each JSONL file contains one trace. Paired files share the same
    execution_id (first 10 chars of filename) but differ in the variant
    suffix ('a' for benign, 'b' for malignant).

    Expected file naming: trace_{execution_id}{variant}.jsonl
    Example: trace_abc123def4a.jsonl, trace_abc123def4b.jsonl
    """

    def __init__(self, trace_dir: Path) -> None:
        """Initialize parser for a directory of trace files.

        Args:
            trace_dir: Directory containing trace_*.jsonl files
        """
        self.trace_dir = Path(trace_dir)
        if not self.trace_dir.exists():
            raise FileNotFoundError(f"Trace directory not found: {self.trace_dir}")

    def parse_file(self, file_path: Union[Path, str]) -> Trace:
        """Parse a single JSONL trace file.

        Args:
            file_path: Path to the .jsonl file

        Returns:
            Trace object with all events loaded
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"Trace file not found: {file_path}")

        # Extract trace_id from filename: trace_XXXXXa.jsonl -> trace_XXXXXa
        stem = file_path.stem  # "trace_XXXXXa"
        trace_id = stem.replace("trace_", "", 1)

        # Extract execution_id (everything except last char) and variant (last char)
        execution_id = trace_id[:-1]
        variant_char = trace_id[-1]
        variant = TraceVariant.BENIGN if variant_char == "a" else TraceVariant.MALIGNANT

        # Read events
        events: List[TraceEvent] = []
        with open(file_path, "r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    event_data = json.loads(line)
                    events.append(TraceEvent.from_dict(event_data))
                except (json.JSONDecodeError, KeyError, ValueError) as e:
                    raise ValueError(
                        f"Failed to parse {file_path}:{line_no}: {e}\nLine: {line[:100]}"
                    ) from e

        if not events:
            raise ValueError(f"No events found in {file_path}")

        # Verify all events share the same execution_id
        exec_ids = {e.execution_id for e in events}
        if len(exec_ids) > 1:
            raise ValueError(
                f"Inconsistent execution_ids in {file_path}: {exec_ids}"
            )

        # Use execution_id from events if available, fall back to filename
        actual_exec_id = events[0].execution_id or execution_id

        return Trace(
            trace_id=trace_id,
            execution_id=actual_exec_id,
            variant=variant,
            events=events,
            file_path=str(file_path),
        )

    def parse_directory(self) -> List[Trace]:
        """Parse all trace_*.jsonl files in the directory.

        Returns:
            List of Trace objects, sorted by trace_id
        """
        trace_files = sorted(self.trace_dir.glob("trace_*.jsonl"))
        traces: List[Trace] = []
        for f in trace_files:
            try:
                traces.append(self.parse_file(f))
            except (ValueError, FileNotFoundError) as e:
                # Skip unparseable files but log the issue
                print(f"Warning: Skipping {f}: {e}")
                continue
        return traces

    def get_pairs(self) -> List[tuple[Trace, Trace]]:
        """Get paired benign/malignant traces.

        Returns:
            List of (benign_trace, malignant_trace) tuples, sorted by execution_id
        """
        traces = self.parse_directory()
        by_exec: Dict[str, List[Trace]] = {}
        for t in traces:
            by_exec.setdefault(t.execution_id, []).append(t)

        pairs: List[tuple[Trace, Trace]] = []
        for exec_id in sorted(by_exec):
            variants = by_exec[exec_id]
            benign = next((t for t in variants if t.is_benign), None)
            malignant = next((t for t in variants if t.is_malignant), None)
            if benign and malignant:
                pairs.append((benign, malignant))
            else:
                missing = []
                if not benign:
                    missing.append("benign (a)")
                if not malignant:
                    missing.append("malignant (b)")
                print(f"Warning: Incomplete pair for {exec_id}: missing {', '.join(missing)}")

        return pairs


class ParseResult:
    """Result of parsing a batch of traces.

    Attributes:
        traces:      All parsed traces
        pairs:       Paired (benign, malignant) traces
        num_traces:  Total number of traces
        num_pairs:   Number of complete pairs
        num_events:  Total events across all traces
        errors:      Any parsing errors encountered
    """

    def __init__(self, traces: List[Trace], pairs: List[tuple[Trace, Trace]]) -> None:
        self.traces = traces
        self.pairs = pairs
        self.num_traces = len(traces)
        self.num_pairs = len(pairs)
        self.num_events = sum(t.num_events for t in traces)
        self.errors: List[str] = []

    def summary(self) -> Dict[str, Any]:
        """Return a summary dictionary."""
        benign_count = sum(1 for t in self.traces if t.is_benign)
        malignant_count = sum(1 for t in self.traces if t.is_malignant)
        return {
            "num_traces": self.num_traces,
            "num_pairs": self.num_pairs,
            "benign_traces": benign_count,
            "malignant_traces": malignant_count,
            "total_events": self.num_events,
            "avg_events_per_trace": self.num_events / max(self.num_traces, 1),
            "avg_events_per_pair": (
                sum(b.num_events + m.num_events for b, m in self.pairs) / max(self.num_pairs, 1)
            ),
        }
