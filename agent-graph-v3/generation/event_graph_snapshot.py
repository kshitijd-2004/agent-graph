"""Temporal snapshot builder for static GNN detector.

Converts an EventGraph into a sequence of growing graph snapshots
$G_1, G_2, \ldots, G_T$, where $G_t$ contains all nodes and edges
observed up to step $t$. Each snapshot is a complete EventGraph with
precomputed node/edge features.

Snapshot selection is intentionally independent of LEP / failure metadata:
snapshots are taken at regular intervals plus the final event.  Injection
and failure timestamps are available offline (in trace labels) for
calculating lead time during evaluation.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from generation.event_graph_builder import DependsOnGraphBuilder, EventGraph
from schemas.trace import Trace

logger = logging.getLogger(__name__)


class TemporalSnapshotBuilder:
    """Build incremental graph snapshots from a trace or EventGraph.

    Each snapshot is a full EventGraph containing all events up to and
    including a specific cutoff.  Snapshots share the parent trace's
    topology and task metadata.

    Snapshot selection: every ``snapshot_interval`` events, plus the
    final event.  No hidden-label-dependent milestones.

    Usage::

        builder = TemporalSnapshotBuilder(snapshot_interval=5)
        snapshots = builder.build_from_trace(trace, "review_loop", "code_review")
        # or
        snapshots = builder.build_from_event_graph(event_graph)
    """

    def __init__(
        self,
        snapshot_interval: int = 5,
        strict: bool = False,
    ) -> None:
        self.snapshot_interval = snapshot_interval
        self.strict = strict
        self._graph_builder = DependsOnGraphBuilder()

    def build_from_trace(
        self,
        trace: Trace,
        topology_name: str,
        task_family: str,
    ) -> List[EventGraph]:
        """Build snapshots from a raw Trace object.

        Convenience method that first builds a full EventGraph, then
        delegates to ``build_from_event_graph``.

        Args:
            trace:           The enriched Trace object
            topology_name:   Topology identifier (e.g. ``"review_loop"``)
            task_family:     Task family (e.g. ``"code_review"``)

        Returns:
            List of EventGraph snapshots, ordered by increasing cutoff
        """
        if not trace.events:
            return []
        full_graph = self._graph_builder.build(
            trace, topology_name=topology_name, task_family=task_family, strict=self.strict
        )
        return self.build_from_event_graph(full_graph)

    def build_from_event_graph(self, event_graph: EventGraph) -> List[EventGraph]:
        """Build snapshots from a pre-built EventGraph.

        Creates snapshots at regular intervals plus the final event.
        No hidden-label-dependent milestones.

        Args:
            event_graph: A fully-constructed EventGraph from DependsOnGraphBuilder

        Returns:
            List of EventGraph snapshots, ordered by increasing cutoff
            (i.e., by increasing number of events included)
        """
        if event_graph.num_nodes == 0:
            return []

        total = event_graph.num_nodes
        interval = self.snapshot_interval

        # Cutoffs: every `interval` events, plus the final event
        cutoffs: List[int] = []
        for i in range(0, total, interval):
            cutoffs.append(min(i, total - 1))
        if not cutoffs or cutoffs[-1] != total - 1:
            cutoffs.append(total - 1)

        # Deduplicate while preserving order
        seen: set = set()
        snapshots: List[EventGraph] = []
        for cutoff_event_index in cutoffs:
            if cutoff_event_index in seen:
                continue
            seen.add(cutoff_event_index)
            snapshot = self._build_snapshot(event_graph, cutoff_event_index)
            if snapshot.num_nodes > 0:
                snapshots.append(snapshot)

        logger.debug(
            "Built %d snapshots from %d events (interval=%d)",
            len(snapshots), total, interval,
        )
        return snapshots

    def _build_snapshot(
        self,
        parent_graph: EventGraph,
        cutoff_event_index: int,
    ) -> EventGraph:
        """Build a single snapshot containing events up to ``cutoff_event_index``.

        The snapshot includes all nodes with ``event_index <= cutoff_event_index``
        and all edges where both source and target are within the cutoff.

        Node features and edge features are recomputed from scratch for the
        subgraph.

        Args:
            parent_graph:         The full EventGraph
            cutoff_event_index:   Include events with event_index <= this value

        Returns:
            A new EventGraph representing the execution prefix
        """
        # Select nodes within the cutoff
        included_nodes: List[EventNode] = []
        old_to_new_idx: dict = {}

        for i, node in enumerate(parent_graph.nodes):
            if node.event_index <= cutoff_event_index:
                old_to_new_idx[i] = len(included_nodes)
                included_nodes.append(node)

        if not included_nodes:
            return EventGraph(
                trace_id=parent_graph.trace_id,
                execution_id=parent_graph.execution_id,
                variant=parent_graph.variant,
                topology_name=parent_graph.topology_name,
                task_family=parent_graph.task_family,
                nodes=[],
                edges=[],
                labels=parent_graph.labels,
                metadata=dict(parent_graph.metadata),
            )

        # Select edges within the cutoff (both endpoints must be included)
        included_edges: List[tuple] = []
        for edge in parent_graph.edges:
            src, tgt = edge
            if src in old_to_new_idx and tgt in old_to_new_idx:
                included_edges.append((old_to_new_idx[src], old_to_new_idx[tgt]))

        # Build the snapshot graph
        snapshot = EventGraph(
            trace_id=parent_graph.trace_id,
            execution_id=parent_graph.execution_id,
            variant=parent_graph.variant,
            topology_name=parent_graph.topology_name,
            task_family=parent_graph.task_family,
            nodes=included_nodes,
            edges=included_edges,
            labels=parent_graph.labels,
            metadata=dict(parent_graph.metadata),
        )

        # Recompute features for the subgraph
        try:
            snapshot.node_features = self._graph_builder._compute_node_features(snapshot)
            snapshot.edge_features = self._graph_builder._compute_edge_features(snapshot)
        except ImportError:
            pass

        return snapshot

    def count_snapshots(self, num_events: int) -> int:
        """Estimate the number of snapshots for a trace with ``num_events`` events."""
        if num_events == 0:
            return 0
        regular = (num_events + self.snapshot_interval - 1) // self.snapshot_interval
        return min(regular + 1, num_events)  # +1 for final event
