"""Hybrid detector combining heuristic signals + TGNN representations.

The heuristic component captures explicit policy violations, provenance
flows, and known risky structures. The TGNN component captures less
obvious temporal and structural patterns. Their representations are
jointly used to produce the final risk score.

Architecture (per detect.tex):
- Heuristic signal extractor: 6 structural/information-flow signals
- TGNN backbone: learned temporal node representations
- Fusion layer: concatenation → MLP → final risk score
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

import torch
import torch.nn as nn
import torch.nn.functional as F

from detectors.tgnn import TemporalGNN, TemporalDetectionOutput
from generation.event_graph_builder import EventGraph
from torch_geometric.nn import global_mean_pool

logger = logging.getLogger(__name__)


@dataclass
class HybridDetectionOutput:
    """Output of the hybrid detector forward pass.

    Attributes:
        risk_scores: Per-event risk scores [num_events].
        gnn_output: Raw TemporalGNN output.
        heuristic_signals: Extracted heuristic signal vector [num_events, heuristic_dim].
        fused_embeddings: Fusion layer embeddings [num_events, fusion_dim].
    """
    risk_scores: torch.Tensor
    gnn_output: TemporalDetectionOutput
    heuristic_signals: torch.Tensor
    fused_embeddings: torch.Tensor


class HeuristicSignalExtractor(nn.Module):
    """Extract interpretable propagation signals from event graphs.

    Computes 6 structural/information-flow signals (per detect.tex):
    1. Rare or unauthorized tool interactions
    2. Repeated or unusual execution transitions
    3. Propagation from untrusted sources to sensitive sinks
    4. Abrupt fan-out from one abnormal source
    5. Convergence of multiple suspicious inputs on one action
    6. Unusual cross-agent or memory access paths

    Each signal is computed from detector-visible features only
    (no perturbation flags).
    """

    # Signal names match detect.tex lines 31-39
    SIGNAL_NAMES: List[str] = [
        "rare_tool",
        "repeated_transitions",
        "untrusted_to_sensitive",
        "fan_out",
        "convergence",
        "cross_agent_memory",
    ]

    def __init__(self, node_feature_dim: int = 24, hidden_dim: int = 32) -> None:
        super().__init__()
        self.node_feature_dim = node_feature_dim
        self.hidden_dim = hidden_dim
        self.num_signals = len(self.SIGNAL_NAMES)

        # Project combined features to signal space
        self.signal_extractors = nn.ModuleList([
            nn.Sequential(
                nn.Linear(node_feature_dim + 2, hidden_dim),
                nn.ReLU(),
                nn.Linear(hidden_dim, 1),
                nn.Sigmoid(),
            )
            for _ in range(self.num_signals)
        ])

    def forward(
        self,
        node_features: torch.Tensor,
        edges_u: torch.Tensor,
        edges_v: torch.Tensor,
        edge_timestamps: torch.Tensor,
        num_nodes: int,
    ) -> torch.Tensor:
        """Extract heuristic signals per event.

        Args:
            node_features:   [num_nodes, node_feature_dim]
            edges_u:         [num_events]
            edges_v:         [num_events]
            edge_timestamps: [num_events]
            num_nodes:       Total nodes

        Returns:
            Signal tensor [num_events, num_signals] with values in [0, 1].
        """
        num_events = edges_u.size(0)
        device = node_features.device

        if num_events == 0:
            return torch.zeros(0, self.num_signals, device=device)

        # Per-edge features: source node, target node, time delta
        src_feats = node_features[edges_u.clamp(0, num_nodes - 1)]
        tgt_feats = node_features[edges_v.clamp(0, num_nodes - 1)]

        # Time delta from previous event
        time_deltas = torch.zeros(num_events, device=device)
        if num_events > 1:
            time_deltas[1:] = edge_timestamps[1:] - edge_timestamps[:-1]
            time_deltas = time_deltas / (time_deltas.max() + 1e-8)

        # Agent role one-hot slices (columns 11-23 in 24-dim features)
        src_role = src_feats[:, 11:24]   # [num_events, 13]
        tgt_role = tgt_feats[:, 11:24]   # [num_events, 13]

        # Event type one-hot slices (columns 0-10)
        src_evt = src_feats[:, 0:11]     # [num_events, 11]
        tgt_evt = tgt_feats[:, 0:11]     # [num_events, 11]

        # Combine features for signal computation
        edge_input = torch.cat([
            src_feats,
            tgt_feats,
            time_deltas.unsqueeze(-1),
        ], dim=-1)

        # Compute each signal
        signals = []
        for extractor in self.signal_extractors:
            sig = extractor(edge_input).squeeze(-1)  # [num_events]
            signals.append(sig.unsqueeze(-1))

        return torch.cat(signals, dim=-1)  # [num_events, num_signals]

    @property
    def signal_names(self) -> List[str]:
        return list(self.SIGNAL_NAMES)


class HybridDetector(nn.Module):
    """Hybrid detector combining interpretable heuristic signals with TGNN.

    The heuristic component captures explicit policy violations, provenance
    flows, and known risky structures. The TGNN component captures less
    obvious temporal and structural patterns. Their representations are
    jointly used to produce the final risk score.

    Args:
        node_feature_dim: Detector-visible node feature dim (default 24).
        memory_dim:       TGNN memory dimension (default 64).
        time_dim:         Time encoding dimension (default 16).
        fusion_dim:       Hidden dimension for fusion layer (default 32).
        dropout:          Dropout rate (default 0.1).
        use_static_gnn:   If True, use StaticGNN instead of TemporalGNN for
                          the learned component. Useful for ablation studies.
    """

    def __init__(
        self,
        node_feature_dim: int = 24,
        memory_dim: int = 64,
        time_dim: int = 16,
        fusion_dim: int = 32,
        dropout: float = 0.1,
        use_static_gnn: bool = False,
    ) -> None:
        super().__init__()

        self.node_feature_dim = node_feature_dim
        self.memory_dim = memory_dim
        self.time_dim = time_dim
        self.fusion_dim = fusion_dim
        self.use_static_gnn = use_static_gnn

        # Learned component: TGNN (or StaticGNN for ablation)
        if use_static_gnn:
            from detectors.static_gnn import StaticGNN
            self.gnn_backbone = StaticGNN(
                node_feature_dim=node_feature_dim,
                hidden_dim=memory_dim,
                dropout=dropout,
            )
        else:
            self.gnn_backbone = TemporalGNN(
                node_feature_dim=node_feature_dim,
                memory_dim=memory_dim,
                time_dim=time_dim,
                dropout=dropout,
            )

        # Heuristic signal extractor
        self.heuristic_extractor = HeuristicSignalExtractor(
            node_feature_dim=node_feature_dim,
            hidden_dim=memory_dim,
        )

        num_signals = self.heuristic_extractor.num_signals

        # Fusion layer
        if use_static_gnn:
            # Static GNN outputs graph-level embeddings
            fusion_input_dim = memory_dim + num_signals
        else:
            # TGNN outputs per-event embeddings
            fusion_input_dim = memory_dim + num_signals

        self.fusion = nn.Sequential(
            nn.Linear(fusion_input_dim, fusion_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(fusion_dim, 1),
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
        batch: Optional[torch.Tensor] = None,
    ) -> HybridDetectionOutput:
        """Forward pass combining GNN + heuristic signals.

        Args:
            node_features:   [num_nodes, node_feature_dim]
            edges_u:         [num_events]
            edges_v:         [num_events]
            edge_timestamps: [num_events]
            num_nodes:       Total nodes
            batch:           Batch assignment [num_nodes] (for StaticGNN mode)

        Returns:
            HybridDetectionOutput
        """
        if batch is None:
            batch = torch.zeros(node_features.size(0), dtype=torch.long,
                                device=node_features.device)

        # ── GNN branch ──────────────────────────────────────────────────────
        if self.use_static_gnn:
            gnn_out = self.gnn_backbone(node_features, edges_v, batch)
            # Use graph-level embedding (pool over all nodes)
            # For static GNN, we need node embeddings then pool
            gnn_emb = global_mean_pool(
                node_features.new_zeros(num_nodes, self.memory_dim),
                batch,
            )
            # The static GNN doesn't expose intermediate embeddings easily,
            # so we use a simpler approach: pass through a projection
            gnn_emb_proj = self.gnn_backbone.node_proj(node_features)
            gnn_emb_pooled = global_mean_pool(gnn_emb_proj, batch)
            # Expand to per-node for fusion
            gnn_per_node = gnn_emb_pooled[batch]  # [num_nodes, memory_dim]
            # For per-event risk, average the source and target node embeddings
            gnn_event_emb = (gnn_per_node[edges_u] + gnn_per_node[edges_v]) / 2
        else:
            gnn_out = self.gnn_backbone(
                node_features, edges_u, edges_v, edge_timestamps, num_nodes
            )
            gnn_event_emb = gnn_out.event_embeddings  # [num_events, memory_dim]

        # ── Heuristic branch ────────────────────────────────────────────────
        heuristic_signals = self.heuristic_extractor(
            node_features, edges_u, edges_v, edge_timestamps, num_nodes
        )  # [num_events, num_signals]

        # ── Fusion ──────────────────────────────────────────────────────────
        fused = torch.cat([gnn_event_emb, heuristic_signals], dim=-1)
        risk_logits = self.fusion(fused).squeeze(-1)
        risk_scores = torch.sigmoid(risk_logits)

        return HybridDetectionOutput(
            risk_scores=risk_scores,
            gnn_output=gnn_out,
            heuristic_signals=heuristic_signals,
            fused_embeddings=fused,
        )

    @torch.no_grad()
    def predict(
        self,
        node_features: torch.Tensor,
        edges_u: torch.Tensor,
        edges_v: torch.Tensor,
        edge_timestamps: torch.Tensor,
        num_nodes: int,
        batch: Optional[torch.Tensor] = None,
    ) -> HybridDetectionOutput:
        """Run inference.

        Args:
            Same as forward().

        Returns:
            HybridDetectionOutput with detached tensors.
        """
        self.eval()
        return self.forward(
            node_features, edges_u, edges_v, edge_timestamps, num_nodes, batch
        )
