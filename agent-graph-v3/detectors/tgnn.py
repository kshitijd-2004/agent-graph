"""Temporal graph neural network for downstream-failure forecasting.

Maintains time-aware node representations that evolve as new events arrive.
After each event, the updated node representation is used to estimate
downstream-failure risk.

Architecture (per detect.tex):
- TGN-like memory module with GRU-style updates
- Sinusoidal time encoding for event timestamps
- Message passing per event (src, dst, timestamp)
- Risk head: linear projection on updated target node memory

The model processes DyGLib-compatible CTDG event streams (edges_u, edges_v,
edge_timestamps, node_features). No DyGLib library dependency — this is a
self-contained implementation.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F

from generation.feature_schema import OBSERVABLE_NODE_FEATURE_DIM

logger = logging.getLogger(__name__)


@dataclass
class TemporalDetectionOutput:
    """Output of a temporal detector forward pass.

    Attributes:
        event_risk_logits: Raw risk logit per event [num_events]
        event_risk_scores: Risk score (sigmoid(logits)) per event [num_events]
        final_logit:        Logit of the last event [1]
        final_score:        Score (sigmoid) of the last event [1]
        node_memories:      Final node memory states [num_nodes, memory_dim]
        event_embeddings:   Embedding used for each event's risk prediction
                            [num_events, hidden_dim]
    """
    event_risk_logits: torch.Tensor
    event_risk_scores: torch.Tensor
    final_logit: torch.Tensor
    final_score: torch.Tensor
    node_memories: torch.Tensor
    event_embeddings: torch.Tensor


class SinusoidalTimeEncoder(nn.Module):
    """Sinusoidal positional encoding for continuous timestamps.

    Encodes scalar timestamps into a vector of dimension ``dim`` using
    sinusoidal functions at different frequencies (following the original
    Transformer positional encoding).
    """

    def __init__(self, dim: int) -> None:
        super().__init__()
        self.dim = dim
        # Precompute frequency divisors: 1/(10000^(2i/dim))
        self.register_buffer(
            "divisor",
            torch.exp(torch.arange(0, dim, 2) * (-math.log(10000.0) / dim)),
        )

    def forward(self, timestamps: torch.Tensor) -> torch.Tensor:
        """Encode a batch of timestamps.

        Args:
            timestamps: Float tensor of shape [N] with scalar timestamps.

        Returns:
            Time encoding tensor of shape [N, dim].
        """
        # Normalize timestamps to [0, 1] range to keep encoding stable
        if timestamps.numel() == 0:
            return timestamps.new_empty((0, self.dim))
        ts_min = timestamps.min()
        ts_max = timestamps.max()
        if ts_max > ts_min:
            ts_norm = (timestamps - ts_min) / (ts_max - ts_min)
        else:
            ts_norm = torch.zeros_like(timestamps)

        # Expand for sinusoidal encoding: [N, dim/2]
        ts_expanded = ts_norm.unsqueeze(-1) * self.divisor

        # Interleave sin/cos: [N, dim]
        encoding = torch.zeros(
            timestamps.size(0), self.dim, device=timestamps.device
        )
        encoding[:, 0::2] = torch.sin(ts_expanded)
        encoding[:, 1::2] = torch.cos(ts_expanded)

        return encoding


class TemporalGNN(nn.Module):
    """Temporal graph neural network for downstream-failure forecasting.

    Maintains time-aware node representations that evolve as new events
    arrive. After each event, the updated node representation is used to
    estimate downstream-failure risk.

    The model processes a continuous-time event stream where each event is
    a (source, target, timestamp) tuple. Events are processed in
    chronological order, updating the target node's memory state.

    Args:
        node_feature_dim:  Input node feature dim (24, detector-visible).
        memory_dim:        Dimension of node memory states (default 64).
        time_dim:          Dimension of time encoding (default 16).
        num_layers:        Number of stacked message-passing MLPs (default 2).
        dropout:           Dropout rate (default 0.1).
    """

    def __init__(
        self,
        node_feature_dim: int = OBSERVABLE_NODE_FEATURE_DIM,
        memory_dim: int = 64,
        time_dim: int = 16,
        num_layers: int = 2,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        self.node_feature_dim = node_feature_dim
        self.memory_dim = memory_dim
        self.time_dim = time_dim
        self.num_layers = num_layers
        self.dropout = dropout

        # Project initial node features to memory dimension
        self.node_proj = nn.Linear(node_feature_dim, memory_dim)

        # Time encoding
        self.time_encoder = SinusoidalTimeEncoder(time_dim)

        # Message computation MLP
        # Input: source_memory + target_memory + time_encoding + node_features
        msg_input_dim = memory_dim + memory_dim + time_dim + node_feature_dim
        self.msg_mlp = nn.Sequential(
            nn.Linear(msg_input_dim, memory_dim * 2),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(memory_dim * 2, memory_dim),
        )

        # Memory update GRU-style cell
        # input: concat(old_memory, message) → new_memory
        self.memory_cell = nn.GRUCell(memory_dim, memory_dim)

        # Risk prediction head
        # Input: updated target memory + time encoding + node features
        risk_input_dim = memory_dim + time_dim + node_feature_dim
        self.risk_head = nn.Sequential(
            nn.Linear(risk_input_dim, memory_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(memory_dim, 1),
        )

        self._init_weights()

    def _init_weights(self) -> None:
        for module in self.modules():
            if isinstance(module, nn.Linear):
                torch.nn.init.xavier_uniform_(module.weight)
                if module.bias is not None:
                    torch.nn.init.zeros_(module.bias)

    def forward(
        self,
        node_features: torch.Tensor,
        edges_u: torch.Tensor,
        edges_v: torch.Tensor,
        edge_timestamps: torch.Tensor,
        num_nodes: int,
    ) -> TemporalDetectionOutput:
        """Process a temporal event stream and return per-event risk scores.

        Args:
            node_features:   Node features [num_nodes, node_feature_dim]
            edges_u:         Source node IDs [num_events]
            edges_v:         Target node IDs [num_events]
            edge_timestamps: Event timestamps [num_events]
            num_nodes:       Total number of nodes in the graph

        Returns:
            TemporalDetectionOutput with per-event risk scores, final
            node memories, and event embeddings.
        """
        num_events = edges_u.size(0)
        device = node_features.device

        # Initialize node memories from projected node features
        node_memories = self.node_proj(node_features)  # [num_nodes, memory_dim]

        # Storage for per-event outputs
        event_logits = torch.zeros(num_events, device=device)
        event_embeddings = torch.zeros(num_events, self.memory_dim, device=device)

        # Encode all timestamps upfront
        time_encodings = self.time_encoder(edge_timestamps)  # [num_events, time_dim]

        # Process events in chronological order
        for i in range(num_events):
            src = int(edges_u[i].item())
            dst = int(edges_v[i].item())
            ts_enc = time_encodings[i]  # [time_dim]
            dst_feat = node_features[dst]  # [node_feature_dim]

            # Sanity clamp for node indices
            src = max(0, min(src, num_nodes - 1))
            dst = max(0, min(dst, num_nodes - 1))

            src_mem = node_memories[src]
            dst_mem = node_memories[dst]

            # Compute message from source to target
            msg_input = torch.cat([
                src_mem,
                dst_mem,
                ts_enc,
                dst_feat,
            ], dim=-1)
            message = self.msg_mlp(msg_input)  # [memory_dim]

            # Update target node memory
            new_dst_mem = self.memory_cell(message, dst_mem)
            node_memories = node_memories.clone()
            node_memories[dst] = new_dst_mem

            # Compute raw risk logit at this event (no sigmoid)
            risk_input = torch.cat([new_dst_mem, ts_enc, dst_feat], dim=-1)
            event_logits[i] = self.risk_head(risk_input).squeeze()
            event_embeddings[i] = new_dst_mem

        # Sigmoid applied here only for scores exposed to callers
        event_scores = torch.sigmoid(event_logits)
        final_logit = event_logits[-1] if num_events else node_memories.sum() * 0.0
        final_score = torch.sigmoid(final_logit)

        return TemporalDetectionOutput(
            event_risk_logits=event_logits,
            event_risk_scores=event_scores,
            final_logit=final_logit,
            final_score=final_score,
            node_memories=node_memories,
            event_embeddings=event_embeddings,
        )

    @torch.no_grad()
    def predict(
        self,
        node_features: torch.Tensor,
        edges_u: torch.Tensor,
        edges_v: torch.Tensor,
        edge_timestamps: torch.Tensor,
        num_nodes: int,
    ) -> TemporalDetectionOutput:
        """Run inference on a TemporalGraphData object.

        Args:
            node_features:   Node features [num_nodes, node_feature_dim]
            edges_u:         Source node IDs [num_events]
            edges_v:         Target node IDs [num_events]
            edge_timestamps: Event timestamps [num_events]
            num_nodes:       Total number of nodes

        Returns:
            TemporalDetectionOutput
        """
        self.eval()
        return self.forward(node_features, edges_u, edges_v,
                            edge_timestamps, num_nodes)
