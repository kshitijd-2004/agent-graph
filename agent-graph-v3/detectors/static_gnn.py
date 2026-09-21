"""Static graph neural network for downstream-failure prediction.

Operates on independent graph snapshots. Each snapshot is processed
without temporal context from previous snapshots.

Architecture:
- GCN (GCNConv) with configurable depth
- Global mean pooling for graph-level representation
- Linear readout for binary classification

All models operate on detector-visible features only (no perturbation flags).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import torch
import torch.nn.functional as F
from torch_geometric.nn import GCNConv, global_mean_pool
from torch_geometric.data import Batch

logger = logging.getLogger(__name__)


@dataclass
class DetectionOutput:
    """Output of a learned detector forward pass.

    Attributes:
        logits:        Raw logits [N] (one per input graph).
        probabilities: Sigmoid probabilities [N].
        is_malignant:  Boolean predictions [N] (threshold=0.5).
        final_logit:   Logit of the last graph in the batch [1].
        final_score:   Sigmoid of the last graph's logit [1].
    """
    logits: torch.Tensor
    probabilities: torch.Tensor
    is_malignant: torch.Tensor
    final_logit: torch.Tensor
    final_score: torch.Tensor


class StaticGNN(torch.nn.Module):
    """Static graph neural network for downstream-failure prediction.

    Processes each graph snapshot independently. Uses GCNConv for
    message passing, global mean pooling for graph-level aggregation,
    and a linear readout for binary classification.

    Args:
        node_feature_dim: Input node feature dimension (default 24,
                          matching the detector-visible encoding).
        hidden_dim:       Hidden layer dimension (default 64).
        num_layers:       Number of GCN layers (default 3).
        dropout:          Dropout rate (default 0.1).
        num_edge_types:   Number of edge types for heterogeneous GNN.
                          Currently unused (homogeneous GCN); reserved for
                          future heterogeneous extensions.
    """

    def __init__(
        self,
        node_feature_dim: int = 24,
        hidden_dim: int = 64,
        num_layers: int = 3,
        dropout: float = 0.1,
        num_edge_types: int = 1,
    ) -> None:
        super().__init__()
        self.node_feature_dim = node_feature_dim
        self.hidden_dim = hidden_dim
        self.num_layers = num_layers
        self.dropout = dropout

        # GCN layers: input → hidden → ... → hidden
        self.convs = torch.nn.ModuleList()
        self.convs.append(GCNConv(node_feature_dim, hidden_dim))

        for _ in range(num_layers - 1):
            self.convs.append(GCNConv(hidden_dim, hidden_dim))

        # Batch normalization after each layer
        self.bns = torch.nn.ModuleList()
        for _ in range(num_layers):
            self.bns.append(torch.nn.BatchNorm1d(hidden_dim))

        # Graph pooling + readout
        self.pool = global_mean_pool
        self.readout = torch.nn.Sequential(
            torch.nn.Linear(hidden_dim, hidden_dim // 2),
            torch.nn.ReLU(),
            torch.nn.Dropout(dropout),
            torch.nn.Linear(hidden_dim // 2, 1),
        )

        self._init_weights()

    def _init_weights(self) -> None:
        """Initialize weights with Xavier uniform."""
        for module in self.modules():
            if isinstance(module, torch.nn.Linear):
                torch.nn.init.xavier_uniform_(module.weight)
                if module.bias is not None:
                    torch.nn.init.zeros_(module.bias)

    def forward(
        self,
        x: torch.Tensor,
        edge_index: torch.Tensor,
        batch: Optional[torch.Tensor] = None,
    ) -> DetectionOutput:
        """Forward pass.

        Args:
            x:          Node features [num_nodes, node_feature_dim]
            edge_index: Edge connectivity [2, num_edges]
            batch:      Batch assignment [num_nodes] (maps each node to its
                        graph). If None, treats the input as a single graph.

        Returns:
            DetectionOutput with logits, probabilities, and binary predictions.
        """
        if batch is None:
            batch = torch.zeros(x.size(0), dtype=torch.long, device=x.device)

        # Message passing layers
        for i, (conv, bn) in enumerate(zip(self.convs, self.bns)):
            x = conv(x, edge_index)
            x = bn(x)
            x = F.relu(x)
            x = F.dropout(x, p=self.dropout, training=self.training)

        # Graph-level pooling
        graph_embeddings = self.pool(x, batch)  # [num_graphs, hidden_dim]

        # Readout
        logits = self.readout(graph_embeddings).squeeze(-1)  # [num_graphs]
        probs = torch.sigmoid(logits)
        preds = probs >= 0.5

        return DetectionOutput(
            logits=logits,
            probabilities=probs,
            is_malignant=preds,
            final_logit=logits[-1] if logits.numel() > 0 else torch.tensor(0.0, device=logits.device),
            final_score=probs[-1] if probs.numel() > 0 else torch.tensor(0.0, device=probs.device),
        )

    @torch.no_grad()
    def predict(self, data) -> DetectionOutput:
        """Run inference on a PyG Data or StaticGraphData object.

        Args:
            data: A StaticGraphData or PyG Data object with x, edge_index,
                  and optionally batch attributes.

        Returns:
            DetectionOutput
        """
        self.eval()
        x = data.x
        edge_index = data.edge_index
        batch = getattr(data, "batch", None)
        if batch is None:
            batch = torch.zeros(x.size(0), dtype=torch.long, device=x.device)
        return self.forward(x, edge_index, batch)
