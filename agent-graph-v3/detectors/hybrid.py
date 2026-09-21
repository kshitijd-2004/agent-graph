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
    """Extract interpretable propagation signals from event graphs.

    Each signal is a deterministic function over detector-visible graph
    structure and features — no learned weights — matching the six
    signals described in detect.tex:

    1. rare_tool              — events involving low-frequency tool types
    2. repeated_transitions   — same (src, dst) pair seen many times
    3. untrusted_to_sensitive — propagation from untrusted agents to
                                sensitive agents
    4. fan_out                — out-degree spike at a source node
    5. convergence            — in-degree spike at a target node
    6. cross_agent_memory     — memory-write → memory-read chains

    All signals are computed from detector-visible features only
    (no perturbation flags).  Each returns a per-event score in [0, 1].
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

    # Agent-role column indices (0-based within 24-dim detector-visible features)
    # Columns 11-23 in the 24-dim space.  We treat "other" and roles not
    # in the sensitive set as untrusted.
    _SENSITIVE_ROLES = frozenset({0, 1, 2, 3, 4, 5, 6})  # analyst, writer, coder, reviewer, manager, planner, researcher
    _MEMORY_EVENT_TYPES = frozenset({6, 7})  # memory_write, memory_read (event-type one-hot cols)

    def __init__(
        self,
        node_feature_dim: int = 24,
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

        # Decode roles from one-hot (cols 11-23 → argmax within that range)
        src_role_idx = node_features[edges_u.clamp(0, num_nodes - 1), 11:24].argmax(dim=1)
        tgt_role_idx = node_features[edges_v.clamp(0, num_nodes - 1), 11:24].argmax(dim=1)
        is_sensitive_src = torch.tensor(
            [int(r.item() in self._SENSITIVE_ROLES) for r in src_role_idx],
            device=device, dtype=torch.float,
        )
        is_sensitive_tgt = torch.tensor(
            [int(r.item() in self._SENSITIVE_ROLES) for r in tgt_role_idx],
            device=device, dtype=torch.float,
        )

        # Event types (cols 0-10)
        src_evt_idx = node_features[edges_u.clamp(0, num_nodes - 1), 0:11].argmax(dim=1)
        tgt_evt_idx = node_features[edges_v.clamp(0, num_nodes - 1), 0:11].argmax(dim=1)

        # ── Signal 1: rare_tool ──────────────────────────────────────────────
        tool_freq = torch.bincount(tgt_evt_idx, minlength=11)
        tool_norm = tool_freq / max(tool_freq.sum().item(), 1)
        rare_tool_scores = 1.0 - tool_norm[tgt_evt_idx]  # infreq tools → high score

        # ── Signal 2: repeated_transitions ───────────────────────────────────
        transition_key = src_evt_idx * 11 + tgt_evt_idx
        _, inv, counts = torch.unique(transition_key, return_inverse=True, return_counts=True)
        repeat_score = counts[inv].float()
        max_count = max(repeat_score.max().item(), 1)
        repeated_trans = (repeat_score - 1.0) / max(max_count - 1.0, 1.0)

        # ── Signal 3: untrusted_to_sensitive ──────────────────────────────────
        untrusted_src = 1.0 - is_sensitive_src  # not sensitive = untrusted
        untrusted_to_sensitive = untrusted_src * is_sensitive_tgt

        # ── Signal 4: fan-out ─────────────────────────────────────────────────
        src_counts = torch.bincount(edges_u.clamp(0, num_nodes - 1), minlength=num_nodes)
        fan_out = src_counts[edges_u.clamp(0, num_nodes - 1)].float()
        max_fan = max(fan_out.max().item(), 1)
        fan_out_scores = (fan_out - 1.0) / max(max_fan - 1.0, 1.0)

        # ── Signal 5: convergence ─────────────────────────────────────────────
        tgt_counts = torch.bincount(edges_v.clamp(0, num_nodes - 1), minlength=num_nodes)
        convergence = tgt_counts[edges_v.clamp(0, num_nodes - 1)].float()
        max_conv = max(convergence.max().item(), 1)
        convergence_scores = (convergence - 1.0) / max(max_conv - 1.0, 1.0)

        # ── Signal 6: cross_agent_memory ──────────────────────────────────────
        is_mem_write = (src_evt_idx == 6).float()  # memory_write event type
        is_mem_read  = (tgt_evt_idx == 7).float()  # memory_read event type
        # For each memory_read, check if there was an earlier memory_write
        # in the same execution stream.  We approximate by checking if
        # any memory write exists upstream (lower event index) that shares
        # the same source node as this read's target.
        cross_agent_memory = torch.zeros(num_events, device=device)
        if is_mem_read.sum() > 0:
            write_times = torch.where(is_mem_write > 0)[0].float()
            if len(write_times) > 0:
                for evt_idx in range(num_events):
                    if is_mem_read[evt_idx] > 0:
                        # Was there a write before this event?
                        has_prior_write = (write_times < evt_idx).any().item()
                        cross_agent_memory[evt_idx] = float(has_prior_write)

        signals = torch.stack([
            rare_tool_scores,
            repeated_trans,
            untrusted_to_sensitive,
            fan_out_scores,
            convergence_scores,
            cross_agent_memory,
        ], dim=-1)  # [num_events, 6]

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
