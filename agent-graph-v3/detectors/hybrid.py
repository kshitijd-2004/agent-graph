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
from generation.feature_schema import OBS_AGENT_ROLE_SLICE, OBSERVABLE_NODE_FEATURE_DIM
from torch_geometric.nn import global_mean_pool

logger = logging.getLogger(__name__)


@dataclass
class HybridDetectionOutput:
    """Output of the hybrid detector forward pass.

    Attributes:
        risk_scores:      Per-event risk scores (sigmoid) [num_events].
        event_logits:     Raw per-event risk logits [num_events].
        final_logit:      Logit of the last event [1].
        final_score:      Sigmoid of the last event's logit [1].
        gnn_output:       Raw TemporalGNN output.
        heuristic_signals: Extracted heuristic signal vector [num_events, heuristic_dim].
        fused_embeddings: Fusion layer embeddings [num_events, fusion_dim].
    """
    risk_scores: torch.Tensor
    event_logits: torch.Tensor
    final_logit: torch.Tensor
    final_score: torch.Tensor
    gnn_output: TemporalDetectionOutput
    heuristic_signals: torch.Tensor
    fused_embeddings: torch.Tensor


class HeuristicSignalExtractor(nn.Module):
    """Extract deterministic propagation signals from event graphs.

    Each signal is a deterministic function over detector-visible graph
    structure and features — no learned weights.  Signal names describe
    exactly what they measure:

    1. event_type_rarity      — target event type is rare in this execution
    2. repeated_transition    — same (src_role, tgt_role) pair appears many times
    3. cross_role_transition  — source and target have different agent roles
    4. source_fan_out         — source node has many outgoing edges
    5. target_convergence     — target node has many incoming edges
    6. memory_chain           — a memory_write precedes a memory_read

    All signals are computed from detector-visible features only
    (no perturbation flags).  Each returns a per-event score in [0, 1].
    """

    # Descriptive names matching actual computation
    SIGNAL_NAMES: List[str] = [
        "event_type_rarity",
        "repeated_transition",
        "cross_role_transition",
        "source_fan_out",
        "target_convergence",
        "memory_chain",
    ]

    # Event-type one-hot column indices (0-10)
    _MEMORY_WRITE_EVT = 6   # memory_write event type
    _MEMORY_READ_EVT  = 7   # memory_read event type

    def __init__(
        self,
        node_feature_dim: int = OBSERVABLE_NODE_FEATURE_DIM,
        hidden_dim: int = 32,
    ) -> None:
        super().__init__()
        self.node_feature_dim = node_feature_dim
        self.hidden_dim = hidden_dim
        self.num_signals = len(self.SIGNAL_NAMES)

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

        src_idx = edges_u.clamp(0, num_nodes - 1)
        tgt_idx = edges_v.clamp(0, num_nodes - 1)

        # Agent role one-hot: use canonical OBS_AGENT_ROLE_SLICE
        src_role = node_features[src_idx, OBS_AGENT_ROLE_SLICE].argmax(dim=1)
        tgt_role = node_features[tgt_idx, OBS_AGENT_ROLE_SLICE].argmax(dim=1)

        # Event type one-hot: cols 0-10 → argmax
        src_evt = node_features[src_idx, 0:11].argmax(dim=1)
        tgt_evt = node_features[tgt_idx, 0:11].argmax(dim=1)

        # ── Signal 1: event_type_rarity ──────────────────────────────────────
        freq = torch.bincount(tgt_evt, minlength=11).float()
        norm = freq / max(freq.sum().item(), 1)
        event_type_rarity = 1.0 - norm[tgt_evt]

        # ── Signal 2: repeated_transition ────────────────────────────────────
        pair_key = src_role * 24 + tgt_role  # role has up to 13 values
        _, inv, counts = torch.unique(pair_key, return_inverse=True, return_counts=True)
        max_count = max(counts.max().item(), 1)
        repeated_transition = (counts[inv].float() - 1.0) / max(max_count - 1.0, 1.0)

        # ── Signal 3: cross_role_transition ──────────────────────────────────
        cross_role_transition = (src_role != tgt_role).float()

        # ── Signal 4: source_fan_out ──────────────────────────────────────────
        out_deg = torch.bincount(src_idx, minlength=num_nodes).float()
        max_out = max(out_deg.max().item(), 1)
        source_fan_out = (out_deg[src_idx] - 1.0) / max(max_out - 1.0, 1.0)

        # ── Signal 5: target_convergence ──────────────────────────────────────
        in_deg = torch.bincount(tgt_idx, minlength=num_nodes).float()
        max_in = max(in_deg.max().item(), 1)
        target_convergence = (in_deg[tgt_idx] - 1.0) / max(max_in - 1.0, 1.0)

        # ── Signal 6: memory_chain ────────────────────────────────────────────
        is_write = (src_evt == self._MEMORY_WRITE_EVT).float()
        is_read  = (tgt_evt == self._MEMORY_READ_EVT).float()
        memory_chain = torch.zeros(num_events, device=device)
        if is_read.sum() > 0:
            write_indices = torch.where(is_write > 0)[0]
            if len(write_indices) > 0:
                for evt_idx in range(num_events):
                    if is_read[evt_idx] > 0 and (write_indices < evt_idx).any():
                        memory_chain[evt_idx] = 1.0

        signals = torch.stack([
            event_type_rarity,
            repeated_transition,
            cross_role_transition,
            source_fan_out,
            target_convergence,
            memory_chain,
        ], dim=-1)

        return signals.clamp(0.0, 1.0)

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
        node_feature_dim: int = OBSERVABLE_NODE_FEATURE_DIM,
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
            event_logits=risk_logits,
            final_logit=risk_logits[-1],
            final_score=torch.sigmoid(risk_logits[-1]),
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
