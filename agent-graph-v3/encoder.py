"""Graph encoder for GNN training and inference.

Converts EntityGraph objects into:
- Static graph format: PyG Data objects (for GCN, GAT, GraphSAGE)

And EventGraph objects into:
- Static graph format: PyG StaticGraphData
- Temporal CTDG format: DyGLib-compatible (src, dst, timestamp, edge_features) event streams
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import torch

from generation.event_graph_builder import EventGraph
from generation.feature_schema import (
    LEAKAGE_COLUMNS,
    OBSERVABLE_NODE_FEATURE_DIM,
    PERTURBATION_FLAG_COLUMNS,
)

if TYPE_CHECKING:
    from graph_builder import EntityGraph


def _strip_perturbation_columns(x: torch.Tensor) -> torch.Tensor:
    """Remove perturbation-flag columns from node features.

    Raw EventGraph.node_features is 29-dim: [event_type(11) | agent_role(13) | perturbation_flags(5)].
    The last 5 columns encode perturbation ground-truth and must not be
    visible to any detector. Returns a 24-dim tensor with only the
    event-type and agent-role one-hot slices.
    """
    if x.shape[1] != 29:
        # If the feature dimension is already 24 (detector-visible) or some
        # other size, return as-is. This makes the function safe to call
        # on already-sanitized tensors.
        return x
    keep = [c for c in range(x.shape[1]) if c not in LEAKAGE_COLUMNS]
    return x[:, keep].contiguous()


@dataclass
class StaticGraphData:
    """A single graph encoded for static GNN training.

    Node features are detector-visible only: event-type one-hot (11 dims)
    + agent-role one-hot (13 dims) = 24 dims. Perturbation-flag columns
    (5 dims) are stripped. Edge features are omitted entirely.

    Attributes:
        x:              Node features [num_nodes, 24] (detector-visible)
        edge_index:     Edge connectivity [2, num_edges]
        edge_attr:      Edge features [num_edges, 0] (empty — propagation_role
                        derived from perturbation flags, not detector-visible)
        y:              Graph-level label [1] (1.0 = malignant, 0.0 = benign)
        trace_id:       Trace identifier
        execution_id:   Execution identifier
        num_nodes:      Number of nodes
        num_edges:      Number of edges
    """

    x: torch.Tensor           # [num_nodes, 24] (detector-visible)
    edge_index: torch.Tensor  # [2, num_edges]
    edge_attr: torch.Tensor   # [num_edges, 0] — perturbation-derived features excluded
    y: torch.Tensor           # [1]
    trace_id: str
    execution_id: str
    num_nodes: int
    num_edges: int

    def to(self, device: torch.device) -> "StaticGraphData":
        """Move all tensors to a device."""
        return StaticGraphData(
            x=self.x.to(device),
            edge_index=self.edge_index.to(device),
            edge_attr=self.edge_attr.to(device),
            y=self.y.to(device),
            trace_id=self.trace_id,
            execution_id=self.execution_id,
            num_nodes=self.num_nodes,
            num_edges=self.num_edges,
        )

    def __repr__(self) -> str:
        return (
            f"StaticGraphData(trace={self.trace_id}, "
            f"nodes={self.num_nodes}, edges={self.num_edges}, "
            f"y={self.y.item():.0f})"
        )


@dataclass
class TemporalGraphData:
    """A temporal graph snapshot for DyGLib-style models.

    TGN, JODIE, and similar models require an event stream where each event is
    a (source, target, timestamp, features) tuple. Events must be
    ordered by timestamp.

    In AgentProp, this is a continuous-time dynamic graph (CTDG): a flat event
    stream of causal/information dependencies between event nodes, sorted
    chronologically by destination-node timestamp.

    Node features are detector-visible only (24 dims). Edge features are
    omitted (propagation_role derived from perturbation flags).

    Attributes:
        edges_u:        Source node IDs [num_events]
        edges_v:        Target node IDs [num_events]
        edge_timestamps: Normalized timestamps [num_events]
        edge_features:  Edge feature matrix [num_events, 0] (empty)
        event_types:    Event type IDs [num_events]
        num_nodes:      Total number of nodes in the graph
        trace_id:       Trace identifier
        execution_id:   Execution identifier
        label:          Graph-level label (1.0 = malignant, 0.0 = benign)
        node_features:  Node feature matrix [num_nodes, 24] (detector-visible)
    """

    edges_u: torch.Tensor       # [num_events]
    edges_v: torch.Tensor       # [num_events]
    edge_timestamps: torch.Tensor  # [num_events]
    edge_features: torch.Tensor    # [num_events, 0] — perturbation-derived features excluded
    event_types: torch.Tensor      # [num_events]
    num_nodes: int
    trace_id: str
    execution_id: str
    label: float
    node_features: torch.Tensor  # [num_nodes, 24] (detector-visible)

    def __len__(self) -> int:
        return len(self.edges_u)

    def __repr__(self) -> str:
        return (
            f"TemporalGraphData(trace={self.trace_id}, "
            f"nodes={self.num_nodes}, events={len(self)}, label={self.label:.0f})"
        )


class GraphEncoder:
    """Encode EntityGraph and EventGraph objects for ML model consumption.

    Supports:
    - Entity-as-node graphs (EntityGraph) → static (PyG) and temporal (DyGLib) formats
    - Event-as-node graphs (EventGraph) → static (PyG) and temporal CTDG formats

    The EntityGraph path is unchanged. The EventGraph path uses precomputed
    features from DependsOnGraphBuilder and produces a CTDG event stream where
    each interaction is (src_event, dst_event, dst.timestamp, edge_features).
    """

    # Node feature dimension: 5 one-hot (entity type) + 1 degree feature
    NODE_FEATURE_DIM = 6

    def __init__(self) -> None:
        self._node_feature_cache: Dict[str, torch.Tensor] = {}

    # ── EntityGraph encoding (unchanged) ────────────────────────────────────

    def _compute_node_features(self, graph: EntityGraph) -> torch.Tensor:
        """Compute node features from the entity-node graph.

        Each node gets:
        - 5-dim one-hot: entity type
        - 1-dim: normalized in-degree (how many events target this entity)
        """
        num_nodes = len(graph.nodes)
        features = []

        # Compute in-degrees
        in_degrees = torch.zeros(num_nodes, dtype=torch.float)
        if graph.num_edges > 0:
            for target_idx in graph.edge_index[1]:
                in_degrees[target_idx] += 1.0
        max_degree = max(in_degrees.max().item(), 1.0)
        in_degrees = in_degrees / max_degree

        for i, node in enumerate(graph.nodes):
            type_vec = node.to_feature_vector(5)
            degree = [in_degrees[i].item()]
            features.append(type_vec + degree)

        return torch.tensor(features, dtype=torch.float)

    def encode_static(
        self,
        graphs: List[EntityGraph],
        labels: Optional[List[float]] = None,
    ) -> List[StaticGraphData]:
        """Encode graphs for static GNN models (PyG).

        Args:
            graphs:  List of EntityGraph objects
            labels:  Optional list of labels (1.0 = malignant, 0.0 = benign).
                     If None, derived from graph.variant

        Returns:
            List of StaticGraphData objects, one per graph
        """
        data_list: List[StaticGraphData] = []
        for i, graph in enumerate(graphs):
            label = labels[i] if labels is not None else (1.0 if graph.variant == "b" else 0.0)
            label_tensor = torch.tensor([label], dtype=torch.float)

            x = self._compute_node_features(graph)

            data_list.append(StaticGraphData(
                x=x,
                edge_index=graph.edge_index.clone(),
                edge_attr=graph.edge_attr.clone(),
                y=label_tensor,
                trace_id=graph.trace_id,
                execution_id=graph.execution_id,
                num_nodes=graph.num_nodes,
                num_edges=graph.num_edges,
            ))

        return data_list

    def encode_temporal(
        self,
        graphs: List[EntityGraph],
        labels: Optional[List[float]] = None,
    ) -> List[TemporalGraphData]:
        """Encode graphs for temporal GNN models (DyGLib).

        DyGLib models like TGN require an event stream where each event is
        a (source, target, timestamp, features) tuple. Events must be
        ordered by timestamp.

        Args:
            graphs:  List of EntityGraph objects
            labels:  Optional list of labels

        Returns:
            List of TemporalGraphData objects, one per graph
        """
        data_list: List[TemporalGraphData] = []
        for i, graph in enumerate(graphs):
            label = labels[i] if labels is not None else (1.0 if graph.variant == "b" else 0.0)

            x = self._compute_node_features(graph)

            # Sort edges by timestamp for temporal models
            if graph.num_edges > 0:
                sorted_indices = torch.argsort(graph.edge_timestamps)
                edges_u = graph.edge_index[0][sorted_indices]
                edges_v = graph.edge_index[1][sorted_indices]
                timestamps = graph.edge_timestamps[sorted_indices]
                features = graph.edge_attr[sorted_indices]
                event_types = graph.edge_event_types[sorted_indices]
            else:
                edges_u = torch.zeros(0, dtype=torch.long)
                edges_v = torch.zeros(0, dtype=torch.long)
                timestamps = torch.zeros(0, dtype=torch.float)
                features = torch.zeros((0, graph.edge_attr.shape[1]), dtype=torch.float)
                event_types = torch.zeros(0, dtype=torch.long)

            data_list.append(TemporalGraphData(
                edges_u=edges_u,
                edges_v=edges_v,
                edge_timestamps=timestamps,
                edge_features=features,
                event_types=event_types,
                num_nodes=graph.num_nodes,
                trace_id=graph.trace_id,
                execution_id=graph.execution_id,
                label=label,
                node_features=x,
            ))

        return data_list

    def encode(
        self,
        graphs: List[EntityGraph],
        labels: Optional[List[float]] = None,
    ) -> Tuple[List[StaticGraphData], List[TemporalGraphData]]:
        """Encode graphs in both formats at once.

        Args:
            graphs: List of EntityGraph objects
            labels: Optional list of labels

        Returns:
            (static_data_list, temporal_data_list)
        """
        static = self.encode_static(graphs, labels)
        temporal = self.encode_temporal(graphs, labels)
        return static, temporal

    # ── EventGraph encoding (new) ───────────────────────────────────────────

    @staticmethod
    def _derive_label(
        event_graph: EventGraph,
        override_labels: Optional[List[float]],
        index: int,
    ) -> float:
        """Derive a graph label from the canonical label source.

        Priority:
        1. Explicit override_labels[index] if provided
        2. event_graph.labels.downstream_failure if available
        3. Raise ValueError — do not fall back to variant, because a
           perturbed run can recover and downstream_failure is the
           authoritative outcome signal.

        Args:
            event_graph:      The EventGraph to label
            override_labels:  Optional list of explicit labels
            index:            Index into override_labels

        Returns:
            float label (1.0 = malignant/downstream failure, 0.0 = benign)

        Raises:
            ValueError: if no label source is available
        """
        if override_labels is not None:
            return float(override_labels[index])

        labels = event_graph.labels
        if labels is not None and labels.downstream_failure is not None:
            return 1.0 if labels.downstream_failure else 0.0

        raise ValueError(
            f"No downstream_failure label available for {event_graph.trace_id}. "
            f"Ensure the trace was evaluated and labels were populated before encoding."
        )

    def encode_event_graph_static(
        self,
        event_graphs: List[EventGraph],
        labels: Optional[List[float]] = None,
    ) -> List[StaticGraphData]:
        """Encode EventGraphs for static GNN models (PyG).

        Consumes precomputed node_features and edge_features from the
        EventGraph. These are the canonical feature tensors produced by
        DependsOnGraphBuilder.build().

        Args:
            event_graphs: List of EventGraph objects with precomputed features
            labels:       Optional list of labels (1.0 = malignant, 0.0 = benign).
                          If None, derived from event_graph.labels.downstream_failure

        Returns:
            List of StaticGraphData objects, one per EventGraph

        Raises:
            ValueError: if node_features or edge_features are None
            ValueError: if no label source is available
        """
        data_list: List[StaticGraphData] = []

        for i, eg in enumerate(event_graphs):
            # ── Node features (detector-visible only) ────────────────────
            # Raw EventGraph.node_features is 29-dim:
            #   [event_type(11) | agent_role(13) | perturbation_flags(5)]
            # The last 5 columns encode perturbation ground-truth and must be
            # excluded from any detector input.
            if eg.node_features is None:
                raise ValueError(
                    f"EventGraph {eg.trace_id} has no node_features. "
                    f"Ensure DependsOnGraphBuilder.build() ran successfully."
                )
            x = _strip_perturbation_columns(eg.node_features)

            # ── Edge index ─────────────────────────────────────────────────
            if eg.num_edges == 0:
                edge_index = torch.empty((2, 0), dtype=torch.long)
            else:
                edge_index = torch.tensor(eg.edges, dtype=torch.long).t()

            # edge_features are omitted entirely: propagation_role is derived
            # from perturbation flags (is_injection_origin, recovers_from_...,
            # stores_perturbed_info, transforms_perturbed_info) which are not
            # detector-visible. A detector must not receive these columns.

            # ── Label ──────────────────────────────────────────────────────
            label = self._derive_label(eg, labels, i)
            y = torch.tensor([label], dtype=torch.float)

            data_list.append(StaticGraphData(
                x=x,
                edge_index=edge_index,
                edge_attr=torch.empty((eg.num_edges, 0), dtype=torch.float),
                y=y,
                trace_id=eg.trace_id,
                execution_id=eg.execution_id,
                num_nodes=eg.num_nodes,
                num_edges=eg.num_edges,
            ))

        return data_list

    def encode_event_graph_temporal(
        self,
        event_graphs: List[EventGraph],
        labels: Optional[List[float]] = None,
    ) -> List[TemporalGraphData]:
        """Encode EventGraphs as DyGLib-compatible CTDG event streams.

        Each temporal interaction represents a causal/information dependency
        between two event nodes, timestamped at the destination node's time:

            Event/action src
                 ↓ depends_on dependency
            Event/action dst  @  dst.timestamp

        Interactions are sorted chronologically by dst.timestamp. For equal
        timestamps, the original edge-list order is preserved (stable sort).

        Args:
            event_graphs: List of EventGraph objects with precomputed features
            labels:       Optional list of labels (1.0 = malignant, 0.0 = benign).
                          If None, derived from event_graph.labels.downstream_failure

        Returns:
            List of TemporalGraphData objects, one per EventGraph

        Raises:
            ValueError: if node_features or edge_features are None
            ValueError: if no label source is available
        """
        data_list: List[TemporalGraphData] = []

        for i, eg in enumerate(event_graphs):
            # ── Node features (detector-visible only) ────────────────────
            if eg.node_features is None:
                raise ValueError(
                    f"EventGraph {eg.trace_id} has no node_features. "
                    f"Ensure DependsOnGraphBuilder.build() ran successfully."
                )
            node_features = _strip_perturbation_columns(eg.node_features)

            # ── Temporal ordering ──────────────────────────────────────────
            if eg.num_edges == 0:
                # Zero-edge graph: all temporal tensors empty
                edges_u = torch.empty(0, dtype=torch.long)
                edges_v = torch.empty(0, dtype=torch.long)
                edge_timestamps = torch.empty(0, dtype=torch.float)
                sorted_edge_features = torch.empty((0, 0), dtype=torch.float)
                event_types = torch.empty(0, dtype=torch.long)
            else:
                # Timestamp per interaction = destination node's timestamp
                # This is when the dependency/information reaches the downstream event
                raw_timestamps: List[float] = []
                for (src_idx, dst_idx) in eg.edges:
                    dst_node = eg.nodes[dst_idx]
                    ts = float(dst_node.timestamp)
                    raw_timestamps.append(ts)

                # Stable sort by timestamp ascending
                # Python's sort is stable, so equal timestamps preserve original order
                sort_key = list(range(len(raw_timestamps)))
                sort_key.sort(key=lambda j: raw_timestamps[j])

                edges_u = torch.tensor([eg.edges[j][0] for j in sort_key], dtype=torch.long)
                edges_v = torch.tensor([eg.edges[j][1] for j in sort_key], dtype=torch.long)
                edge_timestamps = torch.tensor(
                    [raw_timestamps[j] for j in sort_key], dtype=torch.float
                )
                # edge_features omitted: propagation_role derived from perturbation flags
                sorted_edge_features = torch.empty((eg.num_edges, 0), dtype=torch.float)
                event_types = torch.empty(0, dtype=torch.long)

            data_list.append(TemporalGraphData(
                edges_u=edges_u,
                edges_v=edges_v,
                edge_timestamps=edge_timestamps,
                edge_features=sorted_edge_features,
                event_types=event_types,
                num_nodes=eg.num_nodes,
                trace_id=eg.trace_id,
                execution_id=eg.execution_id,
                label=label,
                node_features=node_features,
            ))

        return data_list

    def encode_event_graph(
        self,
        event_graphs: List[EventGraph],
        labels: Optional[List[float]] = None,
    ) -> Tuple[List[StaticGraphData], List[TemporalGraphData]]:
        """Encode EventGraphs in both static and temporal formats.

        Convenience method that calls encode_event_graph_static and
        encode_event_graph_temporal on the same input list.

        Args:
            event_graphs: List of EventGraph objects
            labels:       Optional list of labels

        Returns:
            (static_data_list, temporal_data_list)
        """
        static = self.encode_event_graph_static(event_graphs, labels)
        temporal = self.encode_event_graph_temporal(event_graphs, labels)
        return static, temporal


# Backwards compatibility alias
EntityGraphEncoder = GraphEncoder
