"""Tests for HeuristicDetector and feature-schema leakage prevention."""

from __future__ import annotations

import sys
from pathlib import Path

# ── Setup ────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import torch

from generation.feature_schema import (
    AGENT_ROLE_SLICE,
    EVENT_TYPE_SLICE,
    LEAKAGE_COLUMNS,
    OBSERVABLE_NODE_FEATURE_DIM,
    PERTURBATION_FLAG_COLUMNS,
)
from generation.heuristic_detector import (
    DetectionResult,
    HeuristicDetector,
    _agent_role,
    _compute_out_degrees,
    _percentile,
)
from encoder import StaticGraphData, TemporalGraphData


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_static_graph(
    num_nodes: int,
    edges: list[tuple[int, int]],
    feature_dim: int = OBSERVABLE_NODE_FEATURE_DIM,
) -> StaticGraphData:
    """Create a minimal StaticGraphData with zero node features."""
    x = torch.zeros(num_nodes, feature_dim)
    if num_nodes > 0 and edges:
        edge_index = torch.tensor(edges, dtype=torch.long).t()
    else:
        edge_index = torch.empty((2, 0), dtype=torch.long)
    return StaticGraphData(
        x=x,
        edge_index=edge_index,
        edge_attr=torch.empty((len(edges), 0), dtype=torch.float),
        y=torch.tensor([0.0]),
        trace_id="t1",
        execution_id="e1",
        num_nodes=num_nodes,
        num_edges=len(edges),
    )


def _make_temporal_graph(
    num_nodes: int,
    edges: list[tuple[int, int]],
    timestamps: list[float],
    feature_dim: int = OBSERVABLE_NODE_FEATURE_DIM,
) -> TemporalGraphData:
    """Create a minimal TemporalGraphData with zero node features."""
    x = torch.zeros(num_nodes, feature_dim)
    if edges:
        edges_u = torch.tensor([e[0] for e in edges], dtype=torch.long)
        edges_v = torch.tensor([e[1] for e in edges], dtype=torch.long)
        edge_timestamps = torch.tensor(timestamps, dtype=torch.float)
    else:
        edges_u = torch.empty(0, dtype=torch.long)
        edges_v = torch.empty(0, dtype=torch.long)
        edge_timestamps = torch.empty(0, dtype=torch.float)
    return TemporalGraphData(
        edges_u=edges_u,
        edges_v=edges_v,
        edge_timestamps=edge_timestamps,
        edge_features=torch.empty((len(edges), 0), dtype=torch.float),
        event_types=torch.empty(0, dtype=torch.long),
        num_nodes=num_nodes,
        trace_id="t1",
        execution_id="e1",
        label=0.0,
        node_features=x,
    )


def _make_node_features_with_roles(
    num_nodes: int, roles: list[int]
) -> torch.Tensor:
    """Create node_features with one-hot agent_role encoding.

    roles[i] is the agent_role index (0–11) for node i.
    """
    x = torch.zeros(num_nodes, OBSERVABLE_NODE_FEATURE_DIM)
    for i, role in enumerate(roles):
        if 0 <= role < 13:
            x[i, 11 + role] = 1.0
    return x


# ── Tests ────────────────────────────────────────────────────────────────────


def test_observable_feature_dim():
    """Detector-visible node features are 24 dims, not 29."""
    assert OBSERVABLE_NODE_FEATURE_DIM == 24
    assert len(LEAKAGE_COLUMNS) == 5
    assert 24 in LEAKAGE_COLUMNS
    assert 28 in LEAKAGE_COLUMNS


def test_random_determinism():
    """Same seed produces identical predictions."""
    d1 = HeuristicDetector(HeuristicDetector.RANDOM)
    d2 = HeuristicDetector(HeuristicDetector.RANDOM)
    r1 = [d1.detect(None) for _ in range(10)]
    r2 = [d2.detect(None) for _ in range(10)]
    for r1_i, r2_i in zip(r1, r2):
        assert r1_i.confidence == r2_i.confidence
        assert r1_i.is_malignant == r2_i.is_malignant


def test_random_range():
    """Random confidence always in [0.0, 1.0]."""
    d = HeuristicDetector(HeuristicDetector.RANDOM)
    for _ in range(20):
        r = d.detect(None)
        assert 0.0 <= r.confidence <= 1.0
        assert isinstance(r.is_malignant, bool)


def test_degree_empty_graph():
    """Zero-node graph: confidence 0.0."""
    d = HeuristicDetector(HeuristicDetector.DEGREE_BASED)
    g = _make_static_graph(0, [])
    r = d.detect(g)
    assert r.confidence == 0.0
    assert r.is_malignant is False


def test_degree_single_node():
    """One node, no edges: low confidence."""
    d = HeuristicDetector(HeuristicDetector.DEGREE_BASED)
    g = _make_static_graph(1, [])
    r = d.detect(g)
    assert r.confidence == 0.0
    assert r.is_malignant is False


def test_degree_star_graph():
    """One hub with 10 leaves: high max_out_degree."""
    d = HeuristicDetector(HeuristicDetector.DEGREE_BASED)
    edges = [(0, i) for i in range(1, 11)]
    g = _make_static_graph(11, edges)
    r = d.detect(g)
    # max_out_degree = 10, threshold = 5 → score = 1.0 for that signal
    assert r.confidence > 0.3
    assert "max_out_degree=10" in r.explanation


def test_degree_dense_graph():
    """Near-complete graph: high density."""
    d = HeuristicDetector(HeuristicDetector.DEGREE_BASED)
    n = 10
    edges = [(i, j) for i in range(n) for j in range(n) if i != j]
    g = _make_static_graph(n, edges)
    r = d.detect(g)
    # density = 90/(10*9) = 1.0, threshold = 0.3 → score = 1.0 for that signal
    assert r.confidence > 0.2
    assert "density=1.000" in r.explanation


def test_temporal_empty():
    """Zero events: confidence 0.0."""
    d = HeuristicDetector(HeuristicDetector.TEMPORAL)
    g = _make_temporal_graph(3, [], [])
    r = d.detect(g)
    assert r.confidence == 0.0
    assert r.is_malignant is False


def test_temporal_single_edge():
    """One edge: low confidence (below classification threshold)."""
    d = HeuristicDetector(HeuristicDetector.TEMPORAL)
    x = _make_node_features_with_roles(2, [0, 0])  # same role
    g = _make_temporal_graph(2, [(0, 1)], [0.0])
    g.node_features = x
    r = d.detect(g)
    # burst=1 → s_burst=0.2, ca_density=0/1=0 → s_ca=0, s_gap=0
    # confidence = 0.2*0.4 + 0 + 0 = 0.08 < 0.5
    assert r.confidence < 0.5
    assert r.is_malignant is False


def test_temporal_burst():
    """Many events within a short window: burst_score high."""
    d = HeuristicDetector(HeuristicDetector.TEMPORAL)
    n = 10
    edges = [(i, i + 1) for i in range(n - 1)]
    timestamps = [float(i) * 0.5 for i in range(n - 1)]  # all within 5s, window=10s
    g = _make_temporal_graph(n, edges, timestamps)
    r = d.detect(g)
    # burst = 9 events in 10s window, threshold = 5 → score high
    assert r.confidence > 0.3


def test_temporal_cross_agent():
    """Edges spanning different agent roles: cross_agent_density high."""
    d = HeuristicDetector(HeuristicDetector.TEMPORAL)
    n = 4
    edges = [(0, 1), (1, 2), (2, 3)]
    timestamps = [0.0, 1.0, 2.0]
    x = _make_node_features_with_roles(n, [0, 1, 2, 3])  # all different roles
    g = _make_temporal_graph(n, edges, timestamps)
    g.node_features = x
    r = d.detect(g)
    # cross_agent_density = 3/3 = 1.0, threshold = 0.5 → score = 2.0 → clamped to 1.0
    assert r.confidence > 0.2


def test_fit_calibrates_thresholds():
    """fit() sets thresholds from clean distribution."""
    # Build 5 clean graphs with known metrics
    clean_graphs = []
    for i in range(5):
        n = 5
        edges = [(j, (j + 1) % n) for j in range(n)]
        g = _make_static_graph(n, edges)
        clean_graphs.append(g)

    d = HeuristicDetector(HeuristicDetector.DEGREE_BASED)
    d.fit(clean_graphs)

    # After fit, calibrated thresholds should be set
    assert d._calibrated_thresholds is not None
    assert "max_out_degree" in d._calibrated_thresholds
    assert "graph_density" in d._calibrated_thresholds
    assert "edge_count" in d._calibrated_thresholds


def test_fit_does_not_use_test_data():
    """Calibrated thresholds should not change after seeing test graphs."""
    clean_graphs = [_make_static_graph(5, [(0, 1), (1, 2), (2, 3), (3, 4)])]
    d = HeuristicDetector(HeuristicDetector.DEGREE_BASED)
    d.fit(clean_graphs)
    thresholds_before = dict(d._calibrated_thresholds)

    # Pass test graphs — should not change thresholds
    test_graphs = [_make_static_graph(10, [(i, i + 1) for i in range(9)])]
    d.fit(test_graphs)
    thresholds_after = dict(d._calibrated_thresholds)

    assert thresholds_before == thresholds_after


def test_custom_weights():
    """Boosting one weight changes confidence."""
    d = HeuristicDetector(HeuristicDetector.DEGREE_BASED)
    edges = [(0, i) for i in range(1, 6)]
    g = _make_static_graph(6, edges)

    r_default = d.detect(g)
    d_custom = HeuristicDetector(
        {
            "mode": "degree",
            "classification_threshold": 0.5,
            "weights": {
                "max_out_degree": 1.0,  # all weight on this signal
                "graph_density": 0.0,
                "edge_count": 0.0,
            },
            "thresholds": d.config["thresholds"],
        }
    )
    r_custom = d_custom.detect(g)
    # max_out_degree = 5, threshold = 5 → score = 1.0, confidence = 1.0
    assert r_custom.confidence == 1.0
    assert r_default.confidence < r_custom.confidence


def test_presets_are_distinct():
    """RANDOM, DEGREE_BASED, TEMPORAL produce different confidences."""
    g_deg = _make_static_graph(5, [(0, 1), (1, 2), (2, 3), (3, 4)])
    g_temp = _make_temporal_graph(5, [(0, 1), (1, 2), (2, 3), (3, 4)], [0.0, 1.0, 2.0, 3.0])

    d_random = HeuristicDetector(HeuristicDetector.RANDOM)
    d_degree = HeuristicDetector(HeuristicDetector.DEGREE_BASED)
    d_temporal = HeuristicDetector(HeuristicDetector.TEMPORAL)

    r_r = d_random.detect(None)
    r_d = d_degree.detect(g_deg)
    r_t = d_temporal.detect(g_temp)

    # Random should be in [0,1]; degree/temporal are deterministic
    assert 0.0 <= r_r.confidence <= 1.0
    # Degree and temporal use different signals on same topology
    # They may coincidentally match, but their explanations differ
    assert "Degree-based" in r_d.explanation
    assert "Temporal" in r_t.explanation


def test_encoder_strips_leakage():
    """StaticGraphData.x has 24 dims, edge_attr is empty."""
    from encoder import _strip_perturbation_columns

    # 29-dim raw features
    raw = torch.zeros(3, 29)
    raw[0, 0] = 1.0  # event_type
    raw[0, 11] = 1.0  # agent_role
    raw[0, 24] = 1.0  # perturbation flag — should be stripped

    clean = _strip_perturbation_columns(raw)
    assert clean.shape == (3, 24)
    # Perturbation column should be all zeros
    assert clean[:, 23].sum().item() == 0.0
    # Event type and agent role preserved
    assert clean[0, 0].item() == 1.0
    assert clean[0, 11].item() == 1.0


def test_agent_role_decoder():
    """_agent_role decodes argmax of role slice correctly."""
    x = torch.zeros(3, OBSERVABLE_NODE_FEATURE_DIM)
    x[0, 11 + 3] = 1.0  # role index 3
    x[1, 11 + 7] = 1.0  # role index 7
    x[2, 11 + 0] = 1.0  # role index 0
    assert _agent_role(x, 0) == 3
    assert _agent_role(x, 1) == 7
    assert _agent_role(x, 2) == 0


def test_percentile():
    """_percentile computes correct values."""
    vals = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert _percentile(vals, 0) == 1.0
    assert _percentile(vals, 50) == 3.0
    assert _percentile(vals, 100) == 5.0
    # 95th percentile of 5 values
    p95 = _percentile(vals, 95)
    assert 4.0 <= p95 <= 5.0


def test_degree_directed_density():
    """Graph density for directed graph: E / (N*(N-1)), not 2E/..."""
    d = HeuristicDetector(HeuristicDetector.DEGREE_BASED)
    n = 4
    # Complete directed graph: N*(N-1) = 12 edges
    edges = [(i, j) for i in range(n) for j in range(n) if i != j]
    g = _make_static_graph(n, edges)
    r = d.detect(g)
    # density = 12/12 = 1.0, not 2.0
    assert "density=1.000" in r.explanation


def test_fit_empty_clean_list():
    """fit() with empty list should not crash."""
    d = HeuristicDetector(HeuristicDetector.DEGREE_BASED)
    d.fit([])
    assert d._calibrated_thresholds is not None


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    tests = [
        test_observable_feature_dim,
        test_random_determinism,
        test_random_range,
        test_degree_empty_graph,
        test_degree_single_node,
        test_degree_star_graph,
        test_degree_dense_graph,
        test_temporal_empty,
        test_temporal_single_edge,
        test_temporal_burst,
        test_temporal_cross_agent,
        test_fit_calibrates_thresholds,
        test_fit_does_not_use_test_data,
        test_custom_weights,
        test_presets_are_distinct,
        test_encoder_strips_leakage,
        test_agent_role_decoder,
        test_percentile,
        test_degree_directed_density,
        test_fit_empty_clean_list,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  PASS  {test.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {test.__name__}: {e}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed out of {len(tests)} tests")
    if failed > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
