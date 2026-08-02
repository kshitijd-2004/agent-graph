#!/usr/bin/env python3
"""M1 milestone tests — no external dependencies."""

import json
import os
import sys
import tempfile
from pathlib import Path

# Add v3 root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

passed = 0
failed = 0
errors = []


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


# ── Imports ──────────────────────────────────────────────────────────────────
from schemas import (
    SCHEMA_VERSION, TraceEvent, Trace, TraceVariant, TraceEventType,
    TriggerMatcher, InjectionTrigger, LEPConfig, ScenarioSpec,
    WorkflowConfig, EventLabels, PropagationPath, TraceLabels,
    TOPOLOGIES, CONDITIONS,
)
from evaluators.task_evaluators import CodeReviewEvaluator, FinancialEvaluator
from workflows.topologies import (
    Linear2Topology, Linear3Topology, TopologyConfig, TopologyType,
)

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "workspace_fixtures"


# ── Test functions ───────────────────────────────────────────────────────────

def test_schema_version():
    assert SCHEMA_VERSION == "3.0.0", f"Got {SCHEMA_VERSION}"
    ok("schema_version")


def test_trace_event_creation():
    evt = TraceEvent(
        trace_id="t1", event_id="e1", event_index=0,
        timestamp="2026-01-01T00:00:00Z", event_type=TraceEventType.TOOL_CALL,
        source_entity_id="agent_001", target_entity_id="tool_read",
        agent_id="agent_001", agent_role="researcher",
        tool_name="read_text_file",
        tool_arguments={"path": "src/main.py"},
        observable={"tool_name": "read_text_file"},
        hidden={"lep_id": "LEP_TEST"},
        event_labels=EventLabels(is_injection_origin=True, controlled_injection=True),
    )
    assert evt.event_type == TraceEventType.TOOL_CALL
    assert evt.event_labels.is_injection_origin
    assert evt.event_labels.controlled_injection
    assert evt.hidden["lep_id"] == "LEP_TEST"
    assert evt.observable["tool_name"] == "read_text_file"
    ok("trace_event_creation")


def test_trace_creation():
    evt = TraceEvent(
        trace_id="t1", event_id="e1", event_index=0,
        timestamp="2026-01-01T00:00:00Z", event_type=TraceEventType.USER_INPUT,
    )
    trace = Trace(
        trace_id="t1a", execution_id="t1", variant=TraceVariant.BENIGN, events=[evt],
    )
    assert trace.num_events == 1
    assert trace.is_benign
    assert trace.schema_version == "3.0.0"
    ok("trace_creation")


def test_trigger_matcher_fires_once():
    matcher = TriggerMatcher()
    trigger = InjectionTrigger(event_type="tool_call", tool_name="read_text_file")

    evt1 = TraceEvent(
        trace_id="t1", event_id="e1", event_index=0,
        timestamp="2026-01-01T00:00:00Z", event_type=TraceEventType.TOOL_CALL,
        tool_name="read_text_file",
    )
    d1 = matcher.evaluate("lep-1", trigger, evt1, 0)
    assert d1.fired, f"Expected fired but: {d1.reason}"
    assert d1.matched

    evt2 = TraceEvent(
        trace_id="t1", event_id="e2", event_index=1,
        timestamp="2026-01-01T00:00:01Z", event_type=TraceEventType.TOOL_CALL,
        tool_name="read_text_file",
    )
    d2 = matcher.evaluate("lep-1", trigger, evt2, 1)
    assert not d2.fired, "Should not fire twice"
    assert "already fired" in d2.reason
    ok("trigger_matcher_fires_once")


def test_scenario_spec():
    cfg = WorkflowConfig(topology="linear_3", model_name="test")
    spec = ScenarioSpec(
        scenario_id="s1", task_family="code_review", task_variant="easy",
        fixture_id="code_review_easy", workflow_config=cfg, condition="benign",
    )
    assert spec.is_benign()
    assert not spec.is_perturbed()
    assert spec.lep_codes() == []

    spec2 = ScenarioSpec(
        scenario_id="s2", task_family="code_review", task_variant="easy",
        fixture_id="code_review_easy", workflow_config=cfg,
        lep_configs=[LEPConfig(code="LEP_TEST", name="Test", category="test",
                               target_agent="researcher", description="test",
                               trigger=InjectionTrigger())],
        condition="single_lep",
    )
    assert not spec2.is_benign()
    assert spec2.is_perturbed()
    assert spec2.lep_codes() == ["LEP_TEST"]
    ok("scenario_spec")


def test_topology_constants():
    assert "linear_2" in TOPOLOGIES
    assert "linear_3" in TOPOLOGIES
    assert "coordinator_star" in TOPOLOGIES
    assert "benign" in CONDITIONS
    assert "convergence" in CONDITIONS
    ok("topology_constants")


def test_fixture_directories_exist():
    for fixture in ["code_review_easy", "code_review_conflicting",
                    "financial_clean", "financial_version_conflict"]:
        d = FIXTURE_DIR / fixture
        assert d.exists(), f"Missing fixture: {d}"
        assert (d / "manifest.json").exists(), f"Missing manifest: {fixture}"
    ok("fixture_directories_exist")


def test_fixture_manifest_valid():
    for fixture in ["code_review_easy", "code_review_conflicting",
                    "financial_clean", "financial_version_conflict"]:
        with open(FIXTURE_DIR / fixture / "manifest.json") as f:
            m = json.load(f)
        assert m["fixture_id"] == fixture
        assert "task_family" in m
        assert "required_files" in m
        assert "supported_leps" in m
    ok("fixture_manifest_valid")


def test_code_review_fixture_files():
    for fixture in ["code_review_easy", "code_review_conflicting"]:
        d = FIXTURE_DIR / fixture
        for f in ["src/main.py", "src/utils.py", "tests/test_main.py"]:
            assert (d / f).exists(), f"Missing {f} in {fixture}"
    ok("code_review_fixture_files")


def test_financial_fixture_files():
    for fixture in ["financial_clean", "financial_version_conflict"]:
        d = FIXTURE_DIR / fixture
        assert (d / "documents/q3_summary.md").exists(), f"Missing summary in {fixture}"
    ok("financial_fixture_files")


def test_code_review_evaluator_empty_trace():
    evaluator = CodeReviewEvaluator(fixture_dir=FIXTURE_DIR / "code_review_easy")
    trace = Trace(trace_id="t1a", execution_id="t1", variant=TraceVariant.BENIGN, events=[])
    cfg = WorkflowConfig(topology="linear_2")
    scenario = ScenarioSpec(
        scenario_id="test", task_family="code_review", task_variant="easy",
        fixture_id="code_review_easy", workflow_config=cfg,
    )
    result = evaluator.evaluate(trace, None, scenario)
    assert not result.task_success, f"Should not succeed: {result.evaluator_notes}"
    assert result.factual_score == 0.0
    ok("code_review_evaluator_empty_trace")


def test_financial_evaluator_empty_trace():
    evaluator = FinancialEvaluator(fixture_dir=FIXTURE_DIR / "financial_clean")
    trace = Trace(trace_id="t1a", execution_id="t1", variant=TraceVariant.BENIGN, events=[])
    cfg = WorkflowConfig(topology="linear_2")
    scenario = ScenarioSpec(
        scenario_id="test", task_family="financial_analysis", task_variant="clean",
        fixture_id="financial_clean", workflow_config=cfg,
    )
    result = evaluator.evaluate(trace, None, scenario)
    assert not result.task_success
    assert result.factual_score == 0.0
    ok("financial_evaluator_empty_trace")


def test_financial_evaluator_correct_output():
    evaluator = FinancialEvaluator(fixture_dir=FIXTURE_DIR / "financial_clean")
    cfg = WorkflowConfig(topology="linear_2")
    scenario = ScenarioSpec(
        scenario_id="test", task_family="financial_analysis", task_variant="clean",
        fixture_id="financial_clean", workflow_config=cfg,
    )
    # Format output to match the evaluator's regex patterns exactly
    output = "Q3 revenue: 1520000\n"
    output += "operating: 790000\n"
    output += "marketing: 230000\n"
    output += "R&D: 290000\n"
    output += "total expenses: 1310000\n"
    output += "net profit: 210000\n"
    output += "margin: 13.8%\n"
    output += "Q4 guidance: 1850000\n"
    output += "annual total: 5920000\n"

    tmpdir = Path(tempfile.mkdtemp())
    try:
        p = tmpdir / "output"
        p.mkdir(parents=True)
        (p / "financial_summary.md").write_text(output)
        class WS:
            root = tmpdir
        ws = WS()
        trace = Trace(trace_id="t1a", execution_id="t1", variant=TraceVariant.BENIGN, events=[])
        result = evaluator.evaluate(trace, ws, scenario)
        assert result.task_success, f"Expected success: {result.evaluator_notes}"
        assert result.factual_score > 0.8, f"Score too low: {result.factual_score}"
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
    ok("financial_evaluator_correct_output")


def test_financial_version_conflict_detection():
    evaluator = FinancialEvaluator(fixture_dir=FIXTURE_DIR / "financial_version_conflict")
    cfg = WorkflowConfig(topology="linear_2")
    scenario = ScenarioSpec(
        scenario_id="test", task_family="financial_analysis",
        task_variant="version_conflict",
        fixture_id="financial_version_conflict", workflow_config=cfg,
    )
    wrong_output = "Q3 revenue: $1,480,000\nQ4: $1,720,000\nOperating: $820,000\n"
    tmpdir = Path(tempfile.mkdtemp())
    try:
        p = tmpdir / "output"
        p.mkdir(parents=True)
        (p / "q3_analysis.md").write_text(wrong_output)
        class WS:
            root = tmpdir
        ws = WS()
        trace = Trace(trace_id="t1a", execution_id="t1", variant=TraceVariant.BENIGN, events=[])
        result = evaluator.evaluate(trace, ws, scenario)
        assert result.downstream_failure, "Expected downstream failure for v1 figures"
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
    ok("financial_version_conflict_detection")


def test_linear2_topology():
    cfg = TopologyConfig(topology_type=TopologyType.LINEAR_2,
                         agents=["agent_001", "agent_002"])
    topo = Linear2Topology(cfg)
    assert topo.initialize() == "agent_001"
    topo.initialize()
    assert topo.next_agent("agent_001", "handoff_to_analyst") == "agent_002"
    assert topo.is_terminal("final", 0)
    assert not topo.is_terminal("handoff_to_analyst", 0)
    ok("linear2_topology")


def test_linear3_topology():
    cfg = TopologyConfig(topology_type=TopologyType.LINEAR_3,
                         agents=["agent_001", "agent_002", "agent_003"])
    topo = Linear3Topology(cfg)
    assert topo.initialize() == "agent_001"
    topo.initialize()
    assert topo.next_agent("agent_001", "handoff_to_analyst") == "agent_002"
    assert topo.next_agent("agent_002", "handoff_to_verifier") == "agent_003"
    assert topo.is_terminal("final", 0)
    ok("linear3_topology")


def test_propagation_path():
    pp = PropagationPath(
        path_id="pp1",
        origin_event_id="e1",
        terminal_event_id="e5",
        event_ids=["e1", "e2", "e3", "e4", "e5"],
        path_length=5,
    )
    assert pp.path_length == 5
    assert pp.recovered is False
    ok("propagation_path")


def test_trace_labels():
    tl = TraceLabels(
        task_success=False, downstream_failure=True,
        lep_exposed=True, lep_consumed=True, lep_propagated=True,
        propagation_depth=3,
    )
    assert tl.downstream_failure
    assert tl.propagation_depth == 3
    assert tl.task_success is False
    ok("trace_labels")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 55)
    print("MILESTONE 1 TEST SUITE")
    print("=" * 55)

    section("Schema (M1.1-M1.2)")
    test_schema_version()
    test_trace_event_creation()
    test_trace_creation()
    test_trigger_matcher_fires_once()
    test_scenario_spec()
    test_topology_constants()
    test_propagation_path()
    test_trace_labels()

    section("Fixtures (M1.4)")
    test_fixture_directories_exist()
    test_fixture_manifest_valid()
    test_code_review_fixture_files()
    test_financial_fixture_files()

    section("Evaluators (M1.4-M1.6a)")
    test_code_review_evaluator_empty_trace()
    test_financial_evaluator_empty_trace()
    test_financial_evaluator_correct_output()
    test_financial_version_conflict_detection()

    section("Workflow Topologies (M1.3)")
    test_linear2_topology()
    test_linear3_topology()

    print()
    print("=" * 55)
    print(f"Results: {passed} passed, {failed} failed")
    if errors:
        print("\nFailures:")
        for name, msg in errors:
            print(f"  {name}: {msg}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
