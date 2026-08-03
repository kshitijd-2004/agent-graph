#!/usr/bin/env python3
"""Minimal test runner for M1 tests (no pytest required)."""

import json
import sys
from pathlib import Path

# agent-graph-v3/ is one level up from tests/, but we want the v3 root itself
# as the package root. When running from agent-graph-v3/, we need to add the
# parent (agent-graph) to get 'agent_graph_v3' imports? No — the package is
# the v3 directory itself. Let's add v3 root to path.
V3_ROOT = Path(__file__).resolve().parent.parent  # agent-graph-v3/
sys.path.insert(0, str(V3_ROOT))

# Try both import styles
try:
    from schemas import (
        SCHEMA_VERSION, TraceEvent, Trace, TraceVariant, TraceEventType,
        TriggerMatcher, InjectionTrigger, LEPConfig, ScenarioSpec,
        WorkflowConfig, EventLabels, EdgeAnnotation, PropagationPath,
        TraceLabels, TOPOLOGIES, CONDITIONS,
    )
    from evaluators.task_evaluators import CodeReviewEvaluator, FinancialEvaluator
    from workflows.topologies import Linear2Topology, Linear3Topology, TopologyConfig, TopologyType
except ImportError:
    # Try flat imports
    from schemas_trace import (
        SCHEMA_VERSION, TraceEvent, Trace, TraceVariant, TraceEventType,
        TriggerMatcher, InjectionTrigger, LEPConfig, ScenarioSpec,
        WorkflowConfig, EventLabels, EdgeAnnotation, PropagationPath,
        TraceLabels, TOPOLOGIES, CONDITIONS,
    )
    from evaluators_code_review_evaluator import CodeReviewEvaluator
    from evaluators_financial_evaluator import FinancialEvaluator
    from workflows_linear import Linear2Topology, Linear3Topology
    from schemas_scenario import WorkflowConfig, ScenarioSpec
    from schemas_topology import TopologyConfig, TopologyType

FIXTURE_DIR = V3_ROOT / "workspace_fixtures"

passed = 0
failed = 0
errors = []


def run_test(name, fn):
    global passed, failed, errors
    try:
        fn()
        passed += 1
        print(f"  PASS  {name}")
    except Exception as e:
        failed += 1
        errors.append((name, str(e)))
        print(f"  FAIL  {name}: {e}")


def main():
    print("=" * 50)
    print("MILESTONE 1 TESTS")
    print("=" * 50)

    print("\n=== Fixture files ===")
    run_test("fixture_directories_exist", lambda: test_fixture_directories_exist(FIXTURE_DIR))
    run_test("fixture_manifest_valid", lambda: test_fixture_manifest_valid(FIXTURE_DIR))
    run_test("code_review_fixture_files", lambda: test_code_review_fixture_files(FIXTURE_DIR))
    run_test("financial_fixture_files", lambda: test_financial_fixture_files(FIXTURE_DIR))

    print("\n=== Evaluators ===")
    from evaluators.task_evaluators import CodeReviewEvaluator, FinancialEvaluator

    run_test("code_review_evaluator_empty_trace", lambda: test_code_review_evaluator_empty_trace(FIXTURE_DIR))
    run_test("financial_evaluator_empty_trace", lambda: test_financial_evaluator_empty_trace(FIXTURE_DIR))
    run_test("financial_evaluator_correct_output", lambda: test_financial_evaluator_correct_output(FIXTURE_DIR))
    run_test("financial_version_conflict_detection", lambda: test_financial_version_conflict_detection(FIXTURE_DIR))

    print("\n=== Workflow topologies ===")
    from workflows.topologies import Linear2Topology, Linear3Topology, TopologyConfig, TopologyType

    run_test("linear_2_initialize", test_linear2_initialize)
    run_test("linear_2_handoff", test_linear2_handoff)
    run_test("linear_3_initialize", test_linear3_initialize)
    run_test("linear_3_handoffs", test_linear3_handoffs)

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed")
    if errors:
        print("\nFailures:")
        for name, msg in errors:
            print(f"  {name}: {msg}")
    return failed == 0

def test_schema_version():
    assert SCHEMA_VERSION == "3.0.0"

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

def test_trigger_matcher_fires_once():
    matcher = TriggerMatcher()
    trigger = InjectionTrigger(event_type="tool_call", tool_name="read_text_file")

    evt1 = TraceEvent(
        trace_id="t1", event_id="e1", event_index=0,
        timestamp="2026-01-01T00:00:00Z", event_type=TraceEventType.TOOL_CALL,
        tool_name="read_text_file",
    )
    d1 = matcher.evaluate("lep-1", trigger, evt1, 0)
    assert d1.fired, f"Expected fired but got: {d1.reason}"
    assert d1.matched

    # Idempotency: same trigger should not fire again
    evt2 = TraceEvent(
        trace_id="t1", event_id="e2", event_index=1,
        timestamp="2026-01-01T00:00:01Z", event_type=TraceEventType.TOOL_CALL,
        tool_name="read_text_file",
    )
    d2 = matcher.evaluate("lep-1", trigger, evt2, 1)
    assert not d2.fired, f"Should not fire twice"
    assert "already fired" in d2.reason

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

def test_topology_constants():
    assert "linear_2" in TOPOLOGIES
    assert "linear_3" in TOPOLOGIES
    assert "coordinator_star" in TOPOLOGIES
    assert "benign" in CONDITIONS
    assert "single_lep" in CONDITIONS

def test_condition_constants():
    assert "benign" in CONDITIONS
    assert "convergence" in CONDITIONS
    assert "containment" in CONDITIONS

def test_fixture_directories_exist(FIXTURE_DIR):
    for fixture in ["code_review_easy", "code_review_conflicting",
                    "financial_clean", "financial_version_conflict"]:
        d = FIXTURE_DIR / fixture
        assert d.exists(), f"Missing fixture: {d}"
        assert (d / "manifest.json").exists(), f"Missing manifest in {fixture}"

def test_fixture_manifest_valid(FIXTURE_DIR):
    for fixture in ["code_review_easy", "code_review_conflicting",
                    "financial_clean", "financial_version_conflict"]:
        with open(FIXTURE_DIR / fixture / "manifest.json") as f:
            m = json.load(f)
        assert m["fixture_id"] == fixture
        assert "task_family" in m
        assert "required_files" in m
        assert "supported_leps" in m

def test_code_review_fixture_files(FIXTURE_DIR):
    for fixture in ["code_review_easy", "code_review_conflicting"]:
        d = FIXTURE_DIR / fixture
        for f in ["src/main.py", "src/utils.py", "tests/test_main.py"]:
            assert (d / f).exists(), f"Missing {f} in {fixture}"

def test_financial_fixture_files(FIXTURE_DIR):
    for fixture in ["financial_clean", "financial_version_conflict"]:
        d = FIXTURE_DIR / fixture
        assert (d / "documents/q3_summary.md").exists(), f"Missing summary in {fixture}"

def test_code_review_evaluator_empty_trace(FIXTURE_DIR):
    evaluator = CodeReviewEvaluator(fixture_dir=FIXTURE_DIR / "code_review_easy")
    trace = Trace(
        trace_id="t1a", execution_id="t1", variant=TraceVariant.BENIGN, events=[],
    )
    cfg = WorkflowConfig(topology="linear_2")
    scenario = ScenarioSpec(
        scenario_id="test", task_family="code_review", task_variant="easy",
        fixture_id="code_review_easy", workflow_config=cfg,
    )
    result = evaluator.evaluate(trace, None, scenario)
    assert not result.task_success
    assert result.factual_score == 0.0

def test_financial_evaluator_empty_trace(FIXTURE_DIR):
    evaluator = FinancialEvaluator(fixture_dir=FIXTURE_DIR / "financial_clean")
    trace = Trace(
        trace_id="t1a", execution_id="t1", variant=TraceVariant.BENIGN, events=[],
    )
    cfg = WorkflowConfig(topology="linear_2")
    scenario = ScenarioSpec(
        scenario_id="test", task_family="financial_analysis", task_variant="clean",
        fixture_id="financial_clean", workflow_config=cfg,
    )
    result = evaluator.evaluate(trace, None, scenario)
    assert not result.task_success
    assert result.factual_score == 0.0

def test_financial_evaluator_correct_output(FIXTURE_DIR):
    import tempfile, shutil
    evaluator = FinancialEvaluator(fixture_dir=FIXTURE_DIR / "financial_clean")
    cfg = WorkflowConfig(topology="linear_2")
    scenario = ScenarioSpec(
        scenario_id="test", task_family="financial_analysis", task_variant="clean",
        fixture_id="financial_clean", workflow_config=cfg,
    )
    output = ("Q3 revenue: $1,520,000\nQ3 operating: $790,000\nMarketing: $230,000\n"
              "R&D: $290,000\nTotal expenses: $1,310,000\nNet profit: $210,000\n"
              "Margin: 13.8%\nQ4 guidance: $1,850,000\nAnnual total: $5,920,000\n")
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
        assert result.factual_score > 0.8, f"Expected high factual score, got {result.factual_score}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

def test_financial_version_conflict_detection(FIXTURE_DIR):
    import tempfile, shutil
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
        assert result.downstream_failure, f"Expected downstream failure for v1 figures"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

def test_linear2_initialize():
    cfg = TopologyConfig(topology_type=TopologyType.LINEAR_2, agents=["agent_001", "agent_002"])
    topo = Linear2Topology(cfg)
    agent = topo.initialize()
    assert agent == "agent_001"

def test_linear2_handoff():
    cfg = TopologyConfig(topology_type=TopologyType.LINEAR_2, agents=["agent_001", "agent_002"])
    topo = Linear2Topology(cfg)
    topo.initialize()
    next_agent = topo.next_agent("agent_001", "handoff_to_analyst")
    assert next_agent == "agent_002"
    assert topo.is_terminal("final", 0)

def test_linear3_initialize():
    cfg = TopologyConfig(topology_type=TopologyType.LINEAR_3, agents=["agent_001", "agent_002", "agent_003"])
    topo = Linear3Topology(cfg)
    agent = topo.initialize()
    assert agent == "agent_001"

def test_linear3_handoffs():
    cfg = TopologyConfig(topology_type=TopologyType.LINEAR_3, agents=["agent_001", "agent_002", "agent_003"])
    topo = Linear3Topology(cfg)
    topo.initialize()
    next1 = topo.next_agent("agent_001", "handoff_to_analyst")
    assert next1 == "agent_002"
    next2 = topo.next_agent("agent_002", "handoff_to_verifier")
    assert next2 == "agent_003"
    assert not topo.is_terminal("handoff_to_verifier", 0)
    assert topo.is_terminal("final", 0)


if __name__ == "__main__":
    main()
