"""Event-as-node execution DAG builder.

Converts enriched Trace objects into execution DAGs where each node is
an individual execution event and edges are depends_on causal dependencies.

This is the correct model for propagation-pattern detection research —
the existing graph_builder.py (entity-as-node) is left untouched for
reference.

Example:
    builder = DependsOnGraphBuilder()
    graph = builder.build(trace, topology_name="linear_3", task_family="code_review")
"""

from __future__ import annotations

import logging
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple

from schemas.trace_event import TraceEvent, TraceEventType
from schemas.trace_labels import TraceLabels
from schemas.event_labels import EventLabels

if TYPE_CHECKING:
    from schemas.trace import Trace

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

# Fixed vocabulary for agent_role one-hot encoding
AGENT_ROLE_VOCAB: List[str] = [
    "researcher", "analyst", "verifier", "coordinator",
    "specialist_a", "specialist_b",
    "source_a", "source_b", "synthesizer",
    "branch_a", "branch_b",
    "inspector", "reviewer",
]

# All TraceEventType values (11 types)
EVENT_TYPES: List[str] = [
    "user_input", "system_init", "reasoning", "tool_call",
    "tool_result", "agent_handoff", "topology_transition",
    "llm_output", "memory_retrieval", "memory_write", "final_response",
]

# PropagationRole values (11 values, matching EdgeAnnotation)
PROPAGATION_ROLES: List[str] = [
    "origin", "transfer", "transformation", "storage",
    "convergence", "recovery", "terminal_impact", "unknown",
]


class TraceIntegrityError(Exception):
    """Raised when a trace fails structural validation."""


# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class EventNode:
    """A node in the event-as-node execution DAG.

    One node per TraceEvent.  Nodes are indexed by event_index for
    stable, deterministic graph construction.
    """

    event_id: str
    event_index: int
    event_type: str           # TraceEventType.value (lowercase string)
    agent_role: str           # raw role string from the event
    tool_name: Optional[str]  # tool name or None

    # Perturbation lifecycle flags (from EventLabels)
    is_injection_origin: bool = False
    consumes_perturbed_info: bool = False
    transforms_perturbed_info: bool = False
    stores_perturbed_info: bool = False
    recovers_from_perturbation: bool = False

    # Convenience summary
    is_perturbation_affected: bool = False

    # Additional context
    timestamp: str = ""
    stage_event_index: Optional[int] = None  # from TraceEvent
    source_entity_id: str = ""
    target_entity_id: str = ""

    def __post_init__(self) -> None:
        self.is_perturbation_affected = (
            self.is_injection_origin
            or self.consumes_perturbed_info
            or self.transforms_perturbed_info
            or self.stores_perturbed_info
            or self.recovers_from_perturbation
        )


@dataclass
class EventGraph:
    """Event-as-node execution DAG for a single trace."""

    trace_id: str
    execution_id: str
    variant: str                      # "a" (benign) or "b" (malignant)
    topology_name: str
    task_family: str

    # Core graph data
    nodes: List[EventNode] = field(default_factory=list)
    edges: List[Tuple[int, int]] = field(default_factory=list)  # (src_idx, tgt_idx)

    # Global graph-level labels
    labels: Optional[TraceLabels] = None

    # Metadata
    metadata: Dict[str, any] = field(default_factory=dict)

    # ── Computed properties ────────────────────────────────────────────

    @property
    def num_nodes(self) -> int:
        return len(self.nodes)

    @property
    def num_edges(self) -> int:
        return len(self.edges)

    @property
    def is_malignant(self) -> bool:
        return self.variant == "b"


# ── Builder ──────────────────────────────────────────────────────────────────


class DependsOnGraphBuilder:
    """Build event-as-node execution DAGs from Trace objects.

    Nodes are individual events; edges are depends_on causal dependencies.
    """

    def build(
        self,
        trace: "Trace",
        topology_name: str,
        task_family: str,
        strict: bool = True,
    ) -> EventGraph:
        """Build an EventGraph from a single trace.

        Args:
            trace:           The enriched Trace object
            topology_name:   Topology identifier (e.g. "linear_3")
            task_family:     Task family (e.g. "code_review")
            strict:          If True, raise on cycle; if False, skip cycle edges

        Returns:
            EventGraph
        """
        graph = EventGraph(
            trace_id=trace.trace_id,
            execution_id=trace.execution_id,
            variant=trace.variant.value if hasattr(trace.variant, "value") else str(trace.variant),
            topology_name=topology_name,
            task_family=task_family,
            labels=trace.labels,
            metadata=getattr(trace, "metadata", {}),
        )

        self._create_nodes(graph, trace)
        self._create_edges(graph, trace, strict=strict)
        self._validate(graph, strict=strict)
        return graph

    def build_paired(
        self,
        benign: "Trace",
        malignant: "Trace",
        topology_name: str,
        task_family: str,
        strict: bool = True,
    ) -> Tuple[EventGraph, EventGraph]:
        """Build two EventGraphs from matched benign/malignant traces.

        Args:
            benign:      Paired benign trace
            malignant:   Paired malignant trace
            topology_name:   Topology identifier
            task_family:     Task family
            strict:          If True, raise on cycle; if False, skip cycle edges

        Returns:
            (benign_graph, malignant_graph)
        """
        g_b = self.build(benign, topology_name, task_family, strict=strict)
        g_m = self.build(malignant, topology_name, task_family, strict=strict)
        return g_b, g_m

    # ── Private: node creation ──────────────────────────────────────────

    def _create_nodes(self, graph: EventGraph, trace: "Trace") -> None:
        """Create one EventNode per TraceEvent, indexed by event_index."""
        for event in trace.events:
            et = event.event_type
            type_str = et.value if isinstance(et, TraceEventType) else str(et)

            labels = event.event_labels
            node = EventNode(
                event_id=event.event_id,
                event_index=event.event_index,
                event_type=type_str,
                agent_role=event.agent_role or "unknown",
                tool_name=event.tool_name,
                is_injection_origin=labels.is_injection_origin,
                consumes_perturbed_info=labels.consumes_perturbed_info,
                transforms_perturbed_info=labels.transforms_perturbed_info,
                stores_perturbed_info=labels.stores_perturbed_info,
                recovers_from_perturbation=labels.recovers_from_perturbation,
                timestamp=event.timestamp,
                stage_event_index=event.stage_event_index,
                source_entity_id=event.source_entity_id,
                target_entity_id=event.target_entity_id,
            )
            graph.nodes.append(node)

        if not graph.nodes:
            logger.warning("Trace %s has no events", trace.trace_id)

    # ── Private: edge creation ──────────────────────────────────────────

    def _create_edges(
        self, graph: EventGraph, trace: "Trace", strict: bool = True,
    ) -> None:
        """Create edges from depends_on lists.

        Each depends_on entry is a dependency from the source event to
        the target event (the current event).
        """
        # Build lookup: event_id -> node index
        id_to_idx: Dict[str, int] = {n.event_id: i for i, n in enumerate(graph.nodes)}
        missing: List[str] = []
        self_loops: int = 0

        for node in graph.nodes:
            # Find the source TraceEvent to get its depends_on list
            target_evt = self._find_event_by_id(trace, node.event_id)
            if target_evt is None:
                continue

            for dep_id in target_evt.depends_on:
                if dep_id not in id_to_idx:
                    missing.append(dep_id)
                    continue
                src_idx = id_to_idx[dep_id]
                tgt_idx = id_to_idx[node.event_id]

                if src_idx == tgt_idx:
                    self_loops += 1
                    continue

                graph.edges.append((src_idx, tgt_idx))

        if missing:
            warnings.warn(
                f"Trace {trace.trace_id}: {len(missing)} depends_on references "
                f"not found as event nodes, skipping. First few: {missing[:5]}"
            )
        if self_loops:
            warnings.warn(
                f"Trace {trace.trace_id}: {self_loops} self-loop edges skipped"
            )

    @staticmethod
    def _find_event_by_id(trace: "Trace", event_id: str) -> Optional["TraceEvent"]:
        """Find a TraceEvent by its event_id."""
        for evt in trace.events:
            if evt.event_id == event_id:
                return evt
        return None

    # ── Private: validation ─────────────────────────────────────────────

    def _validate(self, graph: EventGraph, strict: bool = True) -> None:
        """Validate structural invariants."""
        # Unique node IDs
        ids = [n.event_id for n in graph.nodes]
        if len(ids) != len(set(ids)):
            dupes = [eid for eid in ids if ids.count(eid) > 1]
            raise TraceIntegrityError(
                f"Duplicate event IDs: {set(dupes)}"
            )

        # No self-loops
        self_loops = [(s, t) for s, t in graph.edges if s == t]
        if self_loops:
            msg = f"Self-loop edges present: {self_loops[:3]}"
            if strict:
                raise TraceIntegrityError(msg)
            warnings.warn(msg)

        # No duplicate edges
        edge_set = set(graph.edges)
        if len(edge_set) != len(graph.edges):
            dup_edges = [e for e in graph.edges if graph.edges.count(e) > 1]
            raise TraceIntegrityError(
                f"Duplicate edges: {set(dup_edges)}"
            )

        # Acyclic
        self._check_acyclic(graph, strict=strict)

        # Timestamps monotonic along edges
        self._check_timestamps_monotonic(graph)

        # Node count
        if graph.num_nodes == 0:
            raise TraceIntegrityError("EventGraph has zero nodes")

    def _check_acyclic(self, graph: EventGraph, strict: bool = True) -> None:
        """Check for cycles using DFS coloring."""
        WHITE, GRAY, BLACK = 0, 1, 2
        color = [WHITE] * graph.num_nodes

        # Build adjacency list
        adj: Dict[int, List[int]] = defaultdict(list)
        for src, tgt in graph.edges:
            adj[src].append(tgt)

        def dfs(u: int, path: List[int]) -> List[int]:
            """Returns cycle path starting from u, or empty list."""
            color[u] = GRAY
            for v in adj.get(u, []):
                if color[v] == GRAY:
                    # Found cycle: from v back to u
                    return path + [v]
                if color[v] == WHITE:
                    result = dfs(v, path + [v])
                    if result:
                        return result
            color[u] = BLACK
            return []

        for i in range(graph.num_nodes):
            if color[i] == WHITE:
                cycle = dfs(i, [i])
                if cycle:
                    cycle_ids = [graph.nodes[j].event_id for j in cycle]
                    msg = f"Causal cycle detected: {' -> '.join(cycle_ids)}"
                    if strict:
                        raise TraceIntegrityError(msg)
                    warnings.warn(f"{msg}. Cycle edges skipped in strict=False mode.")
                    return

    @staticmethod
    def _check_timestamps_monotonic(graph: EventGraph) -> None:
        """Verify timestamps are non-decreasing along edges.

        source timestamp <= target timestamp for each edge.
        """
        violations = 0
        for src_idx, tgt_idx in graph.edges:
            if src_idx >= len(graph.nodes) or tgt_idx >= len(graph.nodes):
                continue
            ts_src = graph.nodes[src_idx].timestamp
            ts_tgt = graph.nodes[tgt_idx].timestamp
            if ts_src > ts_tgt:
                violations += 1
        if violations:
            warnings.warn(
                f"Trace {graph.trace_id}: {violations} edges with "
                f"backward timestamps (source later than target)"
            )

    # ── Private: feature computation ────────────────────────────────────

    def _compute_node_features(self, graph: EventGraph) -> torch.Tensor:
        """Compute node feature matrix.

        Features per node (concatenated):
          - event_type one-hot (11-dim)
          - agent_role one-hot (fixed vocab size, len(AGENT_ROLE_VOCAB) + 1 for OTHER)
          - perturbation flags (5-dim): origin, consumes, transforms, stores, recovers

        Total: ~11 + len(AGENT_ROLE_VOCAB) + 1 + 5 = 17-dim
        """
        import torch

        et_dim = len(EVENT_TYPES)
        ar_dim = len(AGENT_ROLE_VOCAB) + 1  # +1 for OTHER
        pert_dim = 5
        total_dim = et_dim + ar_dim + pert_dim

        type_to_idx = {t: i for i, t in enumerate(EVENT_TYPES)}
        role_to_idx = {r: i for i, r in enumerate(AGENT_ROLE_VOCAB)}

        features = []
        for node in graph.nodes:
            # Event type one-hot
            et_idx = type_to_idx.get(node.event_type, et_dim - 1)
            et_vec = [0.0] * et_dim
            et_vec[et_idx] = 1.0

            # Agent role one-hot
            role_idx = role_to_idx.get(node.agent_role, ar_dim - 1)
            ar_vec = [0.0] * ar_dim
            ar_vec[role_idx] = 1.0

            # Perturbation flags
            pert_vec = [
                1.0 if node.is_injection_origin else 0.0,
                1.0 if node.consumes_perturbed_info else 0.0,
                1.0 if node.transforms_perturbed_info else 0.0,
                1.0 if node.stores_perturbed_info else 0.0,
                1.0 if node.recovers_from_perturbation else 0.0,
            ]

            features.append(et_vec + ar_vec + pert_vec)

        return torch.tensor(features, dtype=torch.float)

    def _compute_edge_features(self, graph: EventGraph) -> torch.Tensor:
        """Compute edge feature matrix.

        Features per edge:
          - propagation_role one-hot (8-dim)
          - causal_strength (1-dim, 0.0 by default for depends_on edges)

        Total: 9-dim
        """
        import torch

        pr_dim = len(PROPAGATION_ROLES)
        pr_to_idx = {r: i for i, r in enumerate(PROPAGATION_ROLES)}

        features = []
        for src_idx, tgt_idx in graph.edges:
            src_node = graph.nodes[src_idx]

            # Determine propagation_role from source node labels
            if src_node.is_injection_origin:
                role = "origin"
            elif src_node.recovers_from_perturbation:
                role = "recovery"
            elif src_node.stores_perturbed_info:
                role = "storage"
            elif src_node.transforms_perturbed_info:
                role = "transformation"
            else:
                role = "transfer"

            pr_idx = pr_to_idx.get(role, pr_to_idx["unknown"])
            pr_vec = [0.0] * pr_dim
            pr_vec[pr_idx] = 1.0

            causal_strength = [1.0]  # depends_on edges are direct causal links
            features.append(pr_vec + causal_strength)

        return torch.tensor(features, dtype=torch.float)


# ── Convenience function ─────────────────────────────────────────────────────


def build_event_graph(
    trace: "Trace",
    topology_name: str,
    task_family: str,
    strict: bool = True,
) -> EventGraph:
    """One-shot: build an EventGraph from a trace.

    Args:
        trace:          The enriched Trace object
        topology_name:  Topology identifier
        task_family:    Task family name
        strict:         If True, raise on cycle; if False, skip cycle edges

    Returns:
        EventGraph
    """
    return DependsOnGraphBuilder().build(trace, topology_name, task_family, strict=strict)
