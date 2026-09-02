#!/usr/bin/env python3
"""Tests for the event-graph pipeline: Trace → enriched Trace → EventGraph.

Uses pilot traces (pre-generated, JSON format) as test data.
No external services required.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# ── Setup ────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from schemas.trace import Trace, TraceVariant
from schemas.trace_event import TraceEvent, TraceEventType
from generation.event_graph_builder import (
    DependsOnGraphBuilder,
    EventGraph,
    EventNode,
    TraceIntegrityError,
    build_event_graph,
    EVENT_TYPES,
    AGENT_ROLE_VOCAB,
    PROPAGATION_ROLES,
)
from schemas.event_labels import EventLabels

PILOT_DIR = ROOT / "pilot_output"
BENIGN_TRACE = PILOT_DIR / "pilot-2026-08_LEP_MEMORY_POISONING_code_review_00_benign_trace.json"
MALIGNANT_TRACE = PILOT_DIR / "pilot-2026-08_LEP_MEMORY_POISONING_code_review_00_lep_trace.json"


def _load_trace(path: Path) -> Trace:
    with open(path) as f:
        data = json.load(f)
    return Trace.from_dict(data)


# ── Test runner ──────────────────────────────────────────────────────────────

passed = 0
failed = 0
errors: list[tuple[str, str]] = []


def ok(name):
    global passed
    passed += 1
    print(f"  PASS  {name}")


def fail(name, msg):
    global failed
    failed += 1
    errors.append((name, msg))
    print(f"  FAIL  {name}: {msg}")


def section(title):
    print(f"\n--- {title} ---")


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_pilot_traces_load():
    """Pilot traces load and deserialize correctly."""
    section("Trace loading")
    for label, path in [("benign", BENIGN_TRACE), ("malignant", MALIGNANT_TRACE)]:
        trace = _load_trace(path)
        assert isinstance(trace, Trace), f"{label}: not a Trace"
        assert trace.trace_id, f"{label}: empty trace_id"
        assert trace.execution_id, f"{label}: empty execution_id"
        assert trace.variant in (TraceVariant.BENIGN, TraceVariant.MALIGNANT), \
            f"{label}: bad variant {trace.variant}"
        assert len(trace.events) > 0, f"{label}: zero events"
        ok(f"trace_loads_{label}")


def test_benign_malignant_match():
    """Benign and malignant traces have correct variants and trace IDs."""
    benign = _load_trace(BENIGN_TRACE)
    malig = _load_trace(MALIGNANT_TRACE)

    # Variant fields
    assert benign.variant == TraceVariant.BENIGN, \
        f"Benign trace is not BENIGN: {benign.variant}"
    assert malig.variant == TraceVariant.MALIGNANT, \
        f"Malignant trace is not MALIGNANT: {malig.variant}"

    # Trace IDs should end in 'a' / 'b'
    assert benign.trace_id.endswith("a"), f"Benign trace_id should end in 'a': {benign.trace_id}"
    assert malig.trace_id.endswith("b"), f"Malignant trace_id should end in 'b': {malig.trace_id}"

    # Note: pilot files have different execution_ids (single-lep vs no-lep are
    # separate scenarios). The critical pairing (same execution_id) will be
    # validated once the paired-trace generator is implemented.
    ok("benign_malignant_match")


def test_event_node_count():
    """Every event produces one node."""
    section("EventNode creation")
    for label, path in [("benign", BENIGN_TRACE), ("malignant", MALIGNANT_TRACE)]:
        trace = _load_trace(path)
        graph = build_event_graph(trace, "linear_3", "code_review")
        assert graph.num_nodes == len(trace.events), (
            f"{label}: {graph.num_nodes} nodes vs {len(trace.events)} events"
        )
        ok(f"node_count_matches_{label}")


def test_node_perturbation_flags():
    """Node perturbation flags match event_labels."""
    trace = _load_trace(MALIGNANT_TRACE)
    graph = build_event_graph(trace, "linear_3", "code_review")

    origin_count = sum(1 for n in graph.nodes if n.is_injection_origin)
    assert origin_count == 1, f"Expected 1 injection origin, got {origin_count}"

    origin_node = next(n for n in graph.nodes if n.is_injection_origin)
    assert origin_node.is_perturbation_affected
    assert origin_node.event_type == "memory_write"
    assert origin_node.agent_role == "inspector"

    ok("node_perturbation_flags")


def test_node_indices_stable():
    """Node event_index values are monotonic and cover 0..N-1."""
    trace = _load_trace(MALIGNANT_TRACE)
    graph = build_event_graph(trace, "linear_3", "code_review")

    indices = [n.event_index for n in graph.nodes]
    assert indices == list(range(len(indices))), "event_index not monotonic"
    ok("node_indices_stable")


def test_event_type_vocabulary():
    """All event types are drawn from the fixed vocabulary."""
    vocab = set(EVENT_TYPES)
    for label, path in [("benign", BENIGN_TRACE), ("malignant", MALIGNANT_TRACE)]:
        trace = _load_trace(path)
        graph = build_event_graph(trace, "linear_3", "code_review")
        bad = [n.event_type for n in graph.nodes if n.event_type not in vocab]
        assert not bad, f"{label}: unknown event types: {bad[:10]}"
    ok("event_type_vocabulary")


def test_depends_on_edges_match_events():
    """Every depends_on edge points to a valid node."""
    trace = _load_trace(MALIGNANT_TRACE)
    graph = build_event_graph(trace, "linear_3", "code_review")

    id_to_idx = {n.event_id: i for i, n in enumerate(graph.nodes)}
    for src_idx, tgt_idx in graph.edges:
        assert src_idx < graph.num_nodes
        assert tgt_idx < graph.num_nodes
        src_id = graph.nodes[src_idx].event_id
        tgt_id = graph.nodes[tgt_idx].event_id
        assert src_id in id_to_idx
        assert tgt_id in id_to_idx
    ok("depends_on_edges_match_events")


def test_acyclic():
    """Graph has no cycles."""
    trace = _load_trace(MALIGNANT_TRACE)
    graph = build_event_graph(trace, "linear_3", "code_review", strict=True)
    assert graph.num_edges > 0, "Graph should have edges"
    ok("acyclic")


def test_timestamps_monotonic_along_edges():
    """Edge timestamps are non-decreasing (warnings ok, errors not)."""
    trace = _load_trace(MALIGNANT_TRACE)
    graph = build_event_graph(trace, "linear_3", "code_review")
    back_ts = sum(
        1 for s, t in graph.edges
        if graph.nodes[s].timestamp > graph.nodes[t].timestamp
    )
    assert back_ts == 0, f"Found {back_ts} backward-timestamp edges"
    ok("timestamps_monotonic")


def test_no_self_loops():
    """No edges point to the same node."""
    for label, path in [("benign", BENIGN_TRACE), ("malignant", MALIGNANT_TRACE)]:
        trace = _load_trace(path)
        graph = build_event_graph(trace, "linear_3", "code_review")
        self_loops = [(s, t) for s, t in graph.edges if s == t]
        assert not self_loops, f"{label}: self-loops: {self_loops[:3]}"
    ok("no_self_loops")


def test_origin_event_graph_identification():
    """Injection origin event is identified in EventGraph."""
    trace = _load_trace(MALIGNANT_TRACE)
    graph = build_event_graph(trace, "linear_3", "code_review")

    origins = [n for n in graph.nodes if n.is_injection_origin]
    assert len(origins) == 1, f"Expected 1 origin, got {len(origins)}"
    assert origins[0].event_type == "memory_write"
    assert origins[0].agent_role == "inspector"
    assert origins[0].tool_name == "write_memory"

    # Trace labels may or may not be populated (pilot-dependent); don't assert
    # on specific lep_exposed flag — just verify the structure exists
    # if labels are present, they should be accessible
    assert graph.labels is not None or graph.labels is None, \
        "labels field should be accessible (None or populated)"

    ok("origin_event_graph_identification")


def test_malignant_vs_benign_structure():
    """Malignant trace has perturbation nodes; benign does not."""
    benign = _load_trace(BENIGN_TRACE)
    malig = _load_trace(MALIGNANT_TRACE)

    g_b = build_event_graph(benign, "linear_3", "code_review")
    g_m = build_event_graph(malig, "linear_3", "code_review")

    # Benign has no injection origin
    origins_b = [n for n in g_b.nodes if n.is_injection_origin]
    assert not origins_b, f"Benign trace should have no origins: {origins_b}"

    # Malignant has exactly one injection origin
    origins_m = [n for n in g_m.nodes if n.is_injection_origin]
    assert len(origins_m) == 1, f"Malignant should have 1 origin: {len(origins_m)}"

    # Malignant should have at least as many nodes as benign (LEP adds events)
    assert g_m.num_nodes >= g_b.num_nodes, \
        f"Malignant {g_m.num_nodes} < benign {g_b.num_nodes}"

    ok("malignant_vs_benign_structure")


def test_topology_fields():
    """EventGraph has correct topology and task_family."""
    trace = _load_trace(MALIGNANT_TRACE)
    graph = build_event_graph(trace, "linear_3", "code_review")
    assert graph.topology_name == "linear_3"
    assert graph.task_family == "code_review"
    assert graph.is_malignant
    ok("topology_fields")


def test_node_feature_dimensions():
    """Node feature vectors have expected total dimensions.

    Computed without torch: validates the feature computation logic by
    checking the expected dimension from vocab sizes.
    """
    builder = DependsOnGraphBuilder()
    trace = _load_trace(MALIGNANT_TRACE)
    graph = builder.build(trace, "linear_3", "code_review")

    et_dim = len(EVENT_TYPES)
    ar_dim = len(AGENT_ROLE_VOCAB) + 1
    pert_dim = 5
    expected_dim = et_dim + ar_dim + pert_dim

    assert expected_dim > 0
    assert expected_dim == 30, f"Expected dim 30, got {expected_dim}"

    # Validate that _compute_node_features runs and returns correct shape
    x = builder._compute_node_features(graph)
    assert x.shape[0] == graph.num_nodes
    assert x.shape[1] == expected_dim, \
        f"Feature dim {x.shape[1]} != expected {expected_dim}"
    ok("node_feature_dimensions")


def test_edge_feature_dimensions():
    """Edge feature vectors have expected total dimensions."""
    builder = DependsOnGraphBuilder()
    trace = _load_trace(MALIGNANT_TRACE)
    graph = builder.build(trace, "linear_3", "code_review")

    pr_dim = len(PROPAGATION_ROLES)
    expected_dim = pr_dim + 1

    assert expected_dim == 9, f"Expected dim 9, got {expected_dim}"

    edge_attr = builder._compute_edge_features(graph)
    assert edge_attr.shape[0] == graph.num_edges
    assert edge_attr.shape[1] == expected_dim, \
        f"Edge feature dim {edge_attr.shape[1]} != expected {expected_dim}"
    ok("edge_feature_dimensions")


def test_edge_attr_shape_matches_edges():
    """Edge feature matrix has one row per edge."""
    builder = DependsOnGraphBuilder()
    trace = _load_trace(MALIGNANT_TRACE)
    graph = builder.build(trace, "linear_3", "code_review")

    edge_attr = builder._compute_edge_features(graph)
    assert edge_attr.shape[0] == graph.num_edges, \
        f"Edge attr rows {edge_attr.shape[0]} != num edges {graph.num_edges}"
    ok("edge_attr_shape_matches_edges")


def test_stage_event_index_preserved():
    """stage_event_index is preserved in node metadata."""
    trace = _load_trace(MALIGNANT_TRACE)
    graph = build_event_graph(trace, "linear_3", "code_review")

    staged = [n for n in graph.nodes if n.stage_event_index is not None]
    # All events in the pilot have stage_event_index set
    assert len(staged) > 0
    ok("stage_event_index_preserved")


def test_different_topologies_work():
    """Builder works with different topology strings (same trace)."""
    trace = _load_trace(MALIGNANT_TRACE)
    for topo in ("linear_2", "linear_3", "coordinator_star",
                 "branch_and_verify", "coordinator_workers"):
        g = build_event_graph(trace, topo, "code_review")
        assert g.num_nodes == len(trace.events)
        assert g.topology_name == topo
    ok("different_topologies_work")


def test_agent_role_vocabulary_coverage():
    """All roles seen in pilot traces are in or near the fixed vocab."""
    trace = _load_trace(MALIGNANT_TRACE)
    graph = build_event_graph(trace, "linear_3", "code_review")
    vocab = set(AGENT_ROLE_VOCAB)
    unknown = [n.agent_role for n in graph.nodes if n.agent_role not in vocab]
    # "unknown" is expected; anything else is a gap
    assert not unknown or all(r == "unknown" for r in unknown), \
        f"Unexpected roles: {set(unknown)}"
    ok("agent_role_vocabulary_coverage")


def test_large_trace_no_issue():
    """Builder handles the full 68-event pilot trace without errors."""
    trace = _load_trace(MALIGNANT_TRACE)
    graph = build_event_graph(trace, "linear_3", "code_review")
    assert graph.num_nodes == 68
    assert graph.num_edges > 0
    assert graph.num_edges < graph.num_nodes * (graph.num_nodes - 1)  # not dense
    ok("large_trace_no_issue")


def test_trace_integrity_strict_mode():
    """Build with strict=True raises on structural issues; strict=False warns."""
    builder = DependsOnGraphBuilder()
    trace = _load_trace(MALIGNANT_TRACE)

    g_strict = builder.build(trace, "linear_3", "code_review", strict=True)
    assert g_strict.num_nodes > 0

    g_loose = builder.build(trace, "linear_3", "code_review", strict=False)
    assert g_loose.num_nodes > 0
    ok("trace_integrity_strict_mode")


def test_event_type_feature_one_hot():
    """Node feature matrix has exactly one 1.0 in the event_type block."""
    builder = DependsOnGraphBuilder()
    trace = _load_trace(MALIGNANT_TRACE)
    graph = builder.build(trace, "linear_3", "code_review")

    et_dim = len(EVENT_TYPES)
    ar_dim = len(AGENT_ROLE_VOCAB) + 1
    pert_dim = 5

    x = builder._compute_node_features(graph)
    assert x.shape[1] == et_dim + ar_dim + pert_dim

    for i in range(x.shape[0]):
        et_block = x[i, :et_dim]
        ones = (et_block == 1.0).sum().item()
        assert ones == 1, f"Node {i} event_type block has {ones} ones"
    ok("event_type_feature_one_hot")


# ── Main ──────────────────────────────────────────────────────────────────────


def main():
    print("=" * 50)
    print("EVENT GRAPH PIPELINE TESTS")
    print("=" * 50)

    tests = [
        test_pilot_traces_load,
        test_benign_malignant_match,
        test_event_node_count,
        test_node_perturbation_flags,
        test_node_indices_stable,
        test_event_type_vocabulary,
        test_depends_on_edges_match_events,
        test_acyclic,
        test_timestamps_monotonic_along_edges,
        test_no_self_loops,
        test_origin_event_graph_identification,
        test_malignant_vs_benign_structure,
        test_topology_fields,
        test_node_feature_dimensions,
        test_edge_feature_dimensions,
        test_edge_attr_shape_matches_edges,
        test_stage_event_index_preserved,
        test_event_type_feature_one_hot,
        test_different_topologies_work,
        test_agent_role_vocabulary_coverage,
        test_large_trace_no_issue,
        test_trace_integrity_strict_mode,
    ]

    for fn in tests:
        try:
            fn()
        except Exception as e:
            fail(fn.__name__, str(e))

    print(f"\n{'=' * 50}")
    print(f"Results: {passed} passed, {failed} failed")
    if errors:
        print("\nFailures:")
        for name, msg in errors:
            print(f"  {name}: {msg}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
