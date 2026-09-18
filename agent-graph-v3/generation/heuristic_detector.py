"""Heuristic baseline detectors for LEP detection.

Three baselines defined in KJ_notes (medium-term, teal): random, degree-based,
and temporal heuristics. All operate exclusively on detector-visible data:

    StaticGraphData  (degree-based, random)
    TemporalGraphData (temporal)

No perturbation flags, no TraceLabels, no variant, no edge_features.

Calibration (fit):
    Thresholds are derived from clean training graphs only. Call fit() once
    with a list of clean StaticGraphData/TemporalGraphData objects, then
    detect() uses the frozen thresholds on held-out data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import torch

from encoder import StaticGraphData, TemporalGraphData


# ── Result type ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DetectionResult:
    """Output of a heuristic detector.

    Attributes:
        is_malignant:  Whether the detector classified this graph as malignant.
        confidence:    Detection confidence in [0.0, 1.0].
        explanation:   One-line human-readable summary.
    """

    is_malignant: bool
    confidence: float
    explanation: str


# ── Helpers ──────────────────────────────────────────────────────────────────


def _agent_role(node_features: torch.Tensor, node_idx: int) -> int:
    """Decode agent_role from detector-visible node_features.

    The agent_role slice occupies columns 11–23 (13 dims, 12 roles + OTHER).
    Returns the argmax of that slice.
    """
    role_slice = node_features[node_idx, 11:24]
    return int(role_slice.argmax().item())


def _compute_out_degrees(edge_index: torch.Tensor, num_nodes: int) -> torch.Tensor:
    """Compute per-node out-degree from edge_index."""
    if edge_index.shape[1] == 0:
        return torch.zeros(num_nodes, dtype=torch.long)
    src = edge_index[0]
    out_deg = torch.zeros(num_nodes, dtype=torch.long)
    out_deg.scatter_add_(0, src, torch.ones_like(src))
    return out_deg


def _percentile(values: List[float], q: float) -> float:
    """Compute the q-th percentile (0–100) of a list of floats."""
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    # Linear interpolation method
    idx = q / 100.0 * (n - 1)
    lower = int(idx)
    upper = min(lower + 1, n - 1)
    frac = idx - lower
    return s[lower] * (1 - frac) + s[upper] * frac


# ── Detector class ───────────────────────────────────────────────────────────


class HeuristicDetector:
    """Heuristic baseline detectors for LEP detection.

    Three presets: RANDOM, DEGREE_BASED, TEMPORAL. Each operates on
    detector-visible graph data only (no perturbation flags, no labels).

    Usage:
        detector = HeuristicDetector(HeuristicDetector.DEGREE_BASED)
        detector.fit(clean_graphs)          # calibrate thresholds
        result = detector.detect(test_graph) # classify

    Attributes:
        RANDOM:       Seeded Bernoulli(0.5) — null baseline.
        DEGREE_BASED: Structural connectivity signals from edge_index.
        TEMPORAL:     Timing and cross-agent patterns from timestamps.
    """

    # ── Preset configs ───────────────────────────────────────────────────

    RANDOM: Dict[str, Any] = {
        "mode": "random",
        "seed": 42,
        "classification_threshold": 0.5,
    }

    DEGREE_BASED: Dict[str, Any] = {
        "mode": "degree",
        "classification_threshold": 0.5,
        # Signal weights (sum to 1.0)
        "weights": {
            "max_out_degree": 0.4,
            "graph_density": 0.3,
            "edge_count": 0.3,
        },
        # Thresholds are set by fit(); these are fallback defaults if
        # fit() is not called.
        "thresholds": {
            "max_out_degree": 5.0,
            "graph_density": 0.3,
            "edge_count": 10.0,
        },
    }

    TEMPORAL: Dict[str, Any] = {
        "mode": "temporal",
        "classification_threshold": 0.5,
        "weights": {
            "burst_score": 0.4,
            "inter_event_gap": 0.3,
            "cross_agent_density": 0.3,
        },
        "thresholds": {
            "burst_threshold": 5.0,
            "inter_event_threshold": 1.0,
            "cross_agent_threshold": 0.5,
        },
        "burst_window_seconds": 10.0,
    }

    # ── Construction ─────────────────────────────────────────────────────

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = dict(self.DEGREE_BASED)  # default
        if config:
            self.config.update(config)

        # Calibrated thresholds (set by fit); fall back to config defaults.
        self._calibrated_thresholds: Optional[Dict[str, float]] = None

    # ── Calibration ──────────────────────────────────────────────────────

    def fit(self, clean_graphs: List[Any]) -> "HeuristicDetector":
        """Calibrate thresholds from clean (benign) training graphs.

        Computes percentile-based thresholds from the empirical distribution
        of each metric across clean runs. Uses 95th percentile for
        "higher-is-suspicious" metrics and 5th percentile for
        "lower-is-suspicious" metrics.

        Args:
            clean_graphs: List of StaticGraphData (for DEGREE_BASED) or
                          TemporalGraphData (for TEMPORAL) from benign runs.

        Returns:
            self (for chaining)

        Note:
            fit() is idempotent — calling it multiple times does not
            overwrite already-calibrated thresholds.
        """
        mode = self.config["mode"]

        if mode == "random":
            # No calibration needed for random baseline
            return self

        if self._calibrated_thresholds is not None:
            # Already calibrated — do not overwrite
            return self

        metrics: Dict[str, List[float]] = {k: [] for k in self.config["weights"]}

        for graph in clean_graphs:
            if mode == "degree":
                self._collect_degree_metrics(graph, metrics)
            elif mode == "temporal":
                self._collect_temporal_metrics(graph, metrics)

        # Compute thresholds from clean distribution
        thresholds: Dict[str, float] = {}
        for key, values in metrics.items():
            if not values:
                thresholds[key] = self.config["thresholds"].get(key, 1.0)
                continue

            if key == "inter_event_gap":
                # Lower gap = more suspicious → use 5th percentile
                # (most clean traces have gaps >= this value)
                thresholds[key] = _percentile(values, 5)
            else:
                # Higher value = more suspicious → use 95th percentile
                thresholds[key] = _percentile(values, 95)

        self._calibrated_thresholds = thresholds
        return self

    def _collect_degree_metrics(
        self, graph: StaticGraphData, metrics: Dict[str, List[float]]
    ) -> None:
        """Compute degree-based metrics for one clean graph."""
        num_nodes = graph.num_nodes
        edge_index = graph.edge_index
        num_edges = graph.num_edges

        out_deg = _compute_out_degrees(edge_index, num_nodes)
        max_out_degree = float(out_deg.max().item()) if num_nodes > 0 else 0.0

        if num_nodes <= 1:
            density = 0.0
        else:
            density = num_edges / (num_nodes * (num_nodes - 1))

        metrics["max_out_degree"].append(max_out_degree)
        metrics["graph_density"].append(density)
        metrics["edge_count"].append(float(num_edges))

    def _collect_temporal_metrics(
        self, graph: TemporalGraphData, metrics: Dict[str, List[float]]
    ) -> None:
        """Compute temporal metrics for one clean graph."""
        timestamps = graph.edge_timestamps
        num_events = len(timestamps)
        node_features = graph.node_features

        # Inter-event gap
        if num_events >= 2:
            gaps = timestamps[1:] - timestamps[:-1]
            mean_gap = float(gaps.mean().item())
        else:
            mean_gap = 0.0
        metrics["inter_event_gap"].append(mean_gap)

        # Burst: max events in a sliding window
        window = self.config.get("burst_window_seconds", 10.0)
        burst = self._compute_burst(timestamps, window)
        metrics["burst_score"].append(burst)

        # Cross-agent density
        if num_events >= 1 and node_features.shape[0] >= 2:
            ca_count = 0
            for i in range(num_events):
                u = int(graph.edges_u[i].item())
                v = int(graph.edges_v[i].item())
                if u < node_features.shape[0] and v < node_features.shape[0]:
                    if _agent_role(node_features, u) != _agent_role(node_features, v):
                        ca_count += 1
            cross_agent_density = ca_count / num_events
        else:
            cross_agent_density = 0.0
        metrics["cross_agent_density"].append(cross_agent_density)

    @staticmethod
    def _compute_burst(timestamps: torch.Tensor, window: float) -> float:
        """Max number of events in any sliding window of `window` seconds."""
        if len(timestamps) < 2:
            return float(len(timestamps))
        ts = timestamps.sort()[0]  # sorted copy
        max_count = 0
        j = 0
        for i in range(len(ts)):
            while j < len(ts) and ts[j] - ts[i] <= window:
                j += 1
            count = j - i
            if count > max_count:
                max_count = count
        return float(max_count)

    # ── Threshold access ──────────────────────────────────────────────────

    def _get_threshold(self, key: str, fallback: float) -> float:
        """Return calibrated threshold if available, else config default."""
        if self._calibrated_thresholds is not None:
            return self._calibrated_thresholds.get(key, fallback)
        return self.config["thresholds"].get(key, fallback)

    # ── Detection ────────────────────────────────────────────────────────

    def detect(self, graph: Any) -> DetectionResult:
        """Classify a graph as benign or malignant.

        Args:
            graph: StaticGraphData (for DEGREE_BASED/RANDOM) or
                   TemporalGraphData (for TEMPORAL).

        Returns:
            DetectionResult with binary classification and confidence.
        """
        mode = self.config["mode"]
        threshold = self.config["classification_threshold"]

        if mode == "random":
            return self._detect_random()

        if mode == "degree":
            return self._detect_degree(graph, threshold)

        if mode == "temporal":
            return self._detect_temporal(graph, threshold)

        raise ValueError(f"Unknown detector mode: {mode}")

    def _detect_random(self) -> DetectionResult:
        """Null baseline: seeded Bernoulli(0.5)."""
        rng = torch.Generator()
        rng.manual_seed(self.config.get("seed", 42))
        confidence = float(torch.rand((), generator=rng).item())
        is_malignant = confidence >= self.config["classification_threshold"]
        return DetectionResult(
            is_malignant=is_malignant,
            confidence=round(confidence, 4),
            explanation=f"Random baseline (seed={self.config.get('seed', 42)}): confidence={confidence:.4f}",
        )

    def _detect_degree(self, graph: StaticGraphData, threshold: float) -> DetectionResult:
        """Degree-based structural signal detector."""
        num_nodes = graph.num_nodes
        num_edges = graph.num_edges
        edge_index = graph.edge_index

        # Signal 1: max out-degree
        out_deg = _compute_out_degrees(edge_index, num_nodes)
        max_out_degree = float(out_deg.max().item()) if num_nodes > 0 else 0.0
        deg_threshold = self._get_threshold("max_out_degree", 5.0)
        s_max_out_degree = min(max_out_degree / max(deg_threshold, 1e-6), 1.0)

        # Signal 2: graph density (directed)
        if num_nodes <= 1:
            density = 0.0
        else:
            density = num_edges / (num_nodes * (num_nodes - 1))
        density_threshold = self._get_threshold("graph_density", 0.3)
        s_density = min(density / max(density_threshold, 1e-6), 1.0)

        # Signal 3: edge count
        edge_threshold = self._get_threshold("edge_count", 10.0)
        s_edge_count = min(num_edges / max(edge_threshold, 1e-6), 1.0)

        # Weighted aggregation
        weights = self.config["weights"]
        confidence = (
            s_max_out_degree * weights["max_out_degree"]
            + s_density * weights["graph_density"]
            + s_edge_count * weights["edge_count"]
        )
        confidence = max(0.0, min(1.0, confidence))

        is_malignant = confidence >= threshold
        explanation = (
            f"Degree-based: max_out_degree={max_out_degree:.1f} (thr={deg_threshold:.1f}), "
            f"density={density:.3f} (thr={density_threshold:.3f}), "
            f"edges={num_edges} (thr={edge_threshold:.1f}) "
            f"→ confidence={confidence:.4f}"
        )
        return DetectionResult(
            is_malignant=is_malignant,
            confidence=round(confidence, 4),
            explanation=explanation,
        )

    def _detect_temporal(
        self, graph: TemporalGraphData, threshold: float
    ) -> DetectionResult:
        """Temporal pattern detector."""
        timestamps = graph.edge_timestamps
        num_events = len(timestamps)
        node_features = graph.node_features

        # Signal 1: burst score
        window = self.config.get("burst_window_seconds", 10.0)
        burst = self._compute_burst(timestamps, window)
        burst_threshold = self._get_threshold("burst_threshold", 5.0)
        s_burst = min(burst / max(burst_threshold, 1e-6), 1.0)

        # Signal 2: inter-event gap (lower gap = more suspicious)
        inter_threshold = self._get_threshold("inter_event_threshold", 1.0)
        if num_events >= 2:
            gaps = timestamps[1:] - timestamps[:-1]
            mean_gap = float(gaps.mean().item())
            if inter_threshold > 0:
                s_gap = max(0.0, 1.0 - min(mean_gap / inter_threshold, 1.0))
            else:
                s_gap = 1.0 if mean_gap == 0 else 0.0
        else:
            mean_gap = 0.0
            s_gap = 0.0

        # Signal 3: cross-agent density
        if num_events >= 1 and node_features.shape[0] >= 2:
            ca_count = 0
            for i in range(num_events):
                u = int(graph.edges_u[i].item())
                v = int(graph.edges_v[i].item())
                if u < node_features.shape[0] and v < node_features.shape[0]:
                    if _agent_role(node_features, u) != _agent_role(node_features, v):
                        ca_count += 1
            ca_density = ca_count / num_events
        else:
            ca_density = 0.0
        ca_threshold = self._get_threshold("cross_agent_threshold", 0.5)
        s_cross_agent = min(ca_density / max(ca_threshold, 1e-6), 1.0)

        # Weighted aggregation
        weights = self.config["weights"]
        confidence = (
            s_burst * weights["burst_score"]
            + s_gap * weights["inter_event_gap"]
            + s_cross_agent * weights["cross_agent_density"]
        )
        confidence = max(0.0, min(1.0, confidence))

        is_malignant = confidence >= threshold
        explanation = (
            f"Temporal: burst={burst:.1f} (thr={burst_threshold:.1f}), "
            f"mean_gap={mean_gap:.3f}s (thr={inter_threshold:.3f}s), "
            f"cross_agent={ca_density:.3f} (thr={ca_threshold:.3f}) "
            f"→ confidence={confidence:.4f}"
        )
        return DetectionResult(
            is_malignant=is_malignant,
            confidence=round(confidence, 4),
            explanation=explanation,
        )
