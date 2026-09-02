"""Graph encoder for GNN training and inference.

Converts EntityGraph objects into:
- Static graph format: PyG Data objects (for GCN, GAT, GraphSAGE)
- Temporal graph format: DyGLib-compatible tuples (for TGN, JODIE, TGAT)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import torch

from generation.graph_builder import EntityGraph


@dataclass
class StaticGraphData:
    """A single graph encoded for static GNN training.

    This is a PyG Data-like container that can be used directly with
    PyTorch Geometric models.

    Attributes:
        x:              Node features [num_nodes, node_feature_dim]
        edge_index:     Edge connectivity [2, num_edges]
        edge_attr:      Edge features [num_edges, edge_feature_dim]
        y:              Graph-level label [1] (1.0 = malignant, 0.0 = benign)
        trace_id:       Trace identifier
        execution_id:   Execution identifier
        num_nodes:      Number of nodes
        num_edges:      Number of edges
    """

    x: torch.Tensor           # [num_nodes, node_feature_dim]
    edge_index: torch.Tensor  # [2, num_edges]
    edge_attr: torch.Tensor   # [num_edges, edge_feature_dim]
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

    TGN, JODIE, and similar models require event-stream format:
    - A stream of (u, v, ts, label) events
    - Each event is a node-to-node interaction at a timestamp

    Attributes:
        edges_u:        Source node IDs [num_events]
        edges_v:        Target node IDs [num_events]
        edge_timestamps: Normalized timestamps [num_events]
        edge_features:  Edge feature matrix [num_events, edge_feature_dim]
        event_types:    Event type IDs [num_events]
        num_nodes:      Total number of nodes in the graph
        trace_id:       Trace identifier
        execution_id:   Execution identifier
        label:          Graph-level label (1.0 = malignant, 0.0 = benign)
        node_features:  Node feature matrix [num_nodes, node_feature_dim]
    """

    edges_u: torch.Tensor       # [num_events]
    edges_v: torch.Tensor       # [num_events]
    edge_timestamps: torch.Tensor  # [num_events]
    edge_features: torch.Tensor    # [num_events, edge_feature_dim]
    event_types: torch.Tensor      # [num_events]
    num_nodes: int
    trace_id: str
    execution_id: str
    label: float
    node_features: torch.Tensor  # [num_nodes, node_feature_dim]

    def __len__(self) -> int:
        return len(self.edges_u)

    def __repr__(self) -> str:
        return (
            f"TemporalGraphData(trace={self.trace_id}, "
            f"nodes={self.num_nodes}, events={len(self)}, label={self.label:.0f})"
        )


class GraphEncoder:
    """Encode EntityGraph objects for ML model consumption.

    Supports both static (PyG) and temporal (DyGLib) formats.

    Usage:
        encoder = GraphEncoder()

        # Static format for GCN/GAT/GraphSAGE
        static_batch = encoder.encode_static(graphs, labels)

        # Temporal format for TGN/JODIE
        temporal_batch = encoder.encode_temporal(graphs, labels)
    """

    # Node feature dimension: 5 one-hot (entity type) + 1 degree feature
    NODE_FEATURE_DIM = 6

    def __init__(self) -> None:
        self._node_feature_cache: Dict[str, torch.Tensor] = {}

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


# Backwards compatibility alias
EntityGraphEncoder = GraphEncoder
