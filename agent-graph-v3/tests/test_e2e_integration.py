#!/usr/bin/env python3
"""M1.5: End-to-end integration tests for the scenario runner."""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from schemas import (
    LEPConfig, ScenarioSpec, Trace, TraceEvent, TraceEventType, TraceVariant,
    WorkflowConfig, Trace as TraceSchema,
)
from schemas.triggers import InjectionTrigger
from generation.runner import ScenarioRunner, RunResult
from generation.scenario_builder import ScenarioBuilder, ScenarioBuildConfig
from exporters.observable_exporter import ObservableExporter
from exporters.analysis_exporter import AnalysisExporter
from exporters.prefix_exporter import PrefixExporter
from leps.registry import LEPOrchestrator, create_lep_instance

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "workspace_fixtures"

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


def make_tool_event(event_id, tool_name="read_text_file", **kw):
    from schemas.event_labels import EventLabels
    lep_injected = kw.pop("lep_injected", False)
    downstream_failure = kw.pop("downstream_failure", False)
    labels = EventLabels(
        is_injection_origin=lep_injected,
        controlled_injection=lep_injected,
        introduces_downstream_failure=downstream_failure,
    )
    return TraceEvent(
        trace_id="t1", event_id=str(event_id), event_index=event_id - 1,
        timestamp="2026-01-01T00:00:00Z", event_type=TraceEventType.TOOL_CALL,
        source_entity_id="agent_001", target_entity_id=f"tool_{tool_name}",
        tool_name=tool_name,
        event_labels=labels,
        **kw,
    )


def make_user_event():
    return TraceEvent(
        trace_id="t1", event_id="1", event_index=0,
        timestamp="2026-01-01T00:00:00Z", event_type=TraceEventType.USER_INPUT,
        source_entity_id="user", target_entity_id="system",
    )


# ── Runner tests ─────────────────────────────────────────────────────────────

def test_dry_run_scenario():
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ScenarioRunner(
            dry_run=True, max_events=20,
            output_dir=Path(tmpdir) / "ws",
        )
        wcfg = WorkflowConfig(topology="linear_2", max_events=20, max_agent_turns=10)
        spec = ScenarioSpec(
            scenario_id="test_dry_run", task_family="code_review",
            task_variant="easy", fixture_id="code_review_easy",
            workflow_config=wcfg, condition="benign",
        )
        result = runner.run(spec, FIXTURE_DIR)
        assert result.success, f"Run failed: {result.error}"
        assert result.trace.num_events > 0, "Trace is empty"
        ok("dry_run_scenario")


def test_dry_run_perturbed_scenario():
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ScenarioRunner(
            dry_run=True, max_events=20,
            output_dir=Path(tmpdir) / "ws",
        )
        lep = LEPConfig(
            code="LEP_TOOL_RESULT_CORRUPTION", name="Test Corruption",
            category="test", description="test", target_agent="researcher",
            trigger=InjectionTrigger(tool_name="read_text_file"),
        )
        wcfg = WorkflowConfig(topology="linear_2", max_events=20, max_agent_turns=10)
        spec = ScenarioSpec(
            scenario_id="test_perturbed", task_family="code_review",
            task_variant="easy", fixture_id="code_review_easy",
            workflow_config=wcfg, lep_configs=[lep], condition="single_lep",
        )
        result = runner.run(spec, FIXTURE_DIR)
        assert result.success, f"Run failed: {result.error}"
        assert result.trace.num_events > 0
        ok("dry_run_perturbed_scenario")


def test_scenario_builder_benign():
    builder = ScenarioBuilder(seed=42)
    cfg = ScenarioBuildConfig(
        task_family="code_review", fixture_id="code_review_easy",
        topology="linear_2", repetition_index=0,
    )
    spec = builder.build_benign(cfg)
    assert spec.is_benign()
    assert spec.lep_codes() == []
    assert spec.condition == "benign"
    ok("scenario_builder_benign")


def test_scenario_builder_single_lep():
    builder = ScenarioBuilder(seed=42)
    cfg = ScenarioBuildConfig(
        task_family="financial_analysis", fixture_id="financial_clean",
        topology="linear_2", repetition_index=0,
    )
    lep = LEPConfig(
        code="LEP_MEMORY_POISONING", name="Test Poison",
        category="memory", description="test", target_agent="analyst",
        trigger=InjectionTrigger(event_type="memory_write"),
    )
    spec = builder.build_single_lep(cfg, lep)
    assert not spec.is_benign()
    assert spec.lep_codes() == ["LEP_MEMORY_POISONING"]
    assert spec.condition == "single_lep"
    ok("scenario_builder_single_lep")


def test_scenario_builder_counterfactual():
    builder = ScenarioBuilder(seed=42)
    cfg = ScenarioBuildConfig(
        task_family="code_review", fixture_id="code_review_easy",
        topology="linear_2", repetition_index=0,
    )
    lep = LEPConfig(
        code="LEP_TOOL_RESULT_CORRUPTION", name="Test",
        category="tool", description="test", target_agent="inspector",
    )
    lep_spec = builder.build_single_lep(cfg, lep)
    cf_spec = builder.build_counterfactual(lep_spec)
    assert cf_spec.is_benign()
    assert cf_spec.lep_codes() == []
    ok("scenario_builder_counterfactual")


def test_scenario_convergence():
    builder = ScenarioBuilder(seed=42)
    cfg = ScenarioBuildConfig(
        task_family="financial_analysis", fixture_id="financial_clean",
        topology="parallel_merge",
    )
    lep_a = LEPConfig(code="LEP_TOOL_RESULT_CORRUPTION", name="A", category="tool",
                      description="corrupt tool", target_agent="a",
                      trigger=InjectionTrigger(tool_name="read_text_file"))
    lep_b = LEPConfig(code="LEP_MEMORY_POISONING", name="B", category="memory",
                      description="poison memory", target_agent="b",
                      trigger=InjectionTrigger(event_type="memory_write"))
    matrix = builder.build_convergence_matrix(cfg, [lep_a, lep_b])
    assert len(matrix) == 4
    conditions = [s.condition for s in matrix]
    assert "benign" in conditions
    assert "single_lep" in conditions
    assert "convergence" in conditions
    ok("scenario_convergence")


def test_scenario_serialization():
    wcfg = WorkflowConfig(topology="linear_3", model_name="test")
    lep = LEPConfig(
        code="LEP_TEST", name="Test", category="test",
        description="test", target_agent="agent",
    )
    spec = ScenarioSpec(
        scenario_id="s1", task_family="code_review", task_variant="easy",
        fixture_id="cr_easy", workflow_config=wcfg, lep_configs=[lep],
        condition="single_lep", repetition_index=2,
    )
    d = spec.to_dict()
    assert d["scenario_id"] == "s1"
    assert d["condition"] == "single_lep"
    assert len(d["lep_configs"]) == 1
    assert d["lep_configs"][0]["code"] == "LEP_TEST"

    restored = ScenarioSpec.from_dict(d)
    assert restored.scenario_id == "s1"
    assert restored.condition == "single_lep"
    assert restored.lep_codes() == ["LEP_TEST"]
    ok("scenario_serialization")


def test_lep_orchestrator():
    orch = LEPOrchestrator()
    lep = LEPConfig(
        code="LEP_TOOL_RESULT_CORRUPTION",
        name="Test", category="test",
        description="test", target_agent="researcher",
        trigger=InjectionTrigger(tool_name="read_text_file"),
    )
    orch.register_lep(lep)
    assert "LEP_TOOL_RESULT_CORRUPTION" in orch._active_leps

    event = make_tool_event(1, tool_name="read_text_file")
    results = orch.evaluate_triggers(event)
    assert "LEP_TOOL_RESULT_CORRUPTION" in results

    orch.reset()
    assert len(orch._active_leps) == 0
    ok("lep_orchestrator")


def test_lep_instance_creation():
    lep = LEPConfig(
        code="LEP_TOOL_RESULT_CORRUPTION",
        name="Test", category="test",
        description="test", target_agent="researcher",
        trigger=InjectionTrigger(tool_name="read_text_file"),
    )
    instance = create_lep_instance(lep)
    assert instance is not None
    assert hasattr(instance, "evaluate")
    assert hasattr(instance, "corrupt")
    ok("lep_instance_creation")


def test_all_lep_types_instantiate():
    from leps.registry import LEP_REGISTRY
    for code, cls in LEP_REGISTRY.items():
        lep = LEPConfig(
            code=code, name="Test", category="test",
            description="test", target_agent="agent",
        )
        instance = cls(lep)
        assert instance is not None
        assert hasattr(instance, "evaluate")
    ok("all_lep_types_instantiate")


def test_trigger_matcher_lep_integration():
    from schemas.trigger_matcher import TriggerMatcher
    matcher = TriggerMatcher()
    lep = LEPConfig(
        code="LEP_TOOL_RESULT_CORRUPTION",
        name="Test", category="tool_corruption",
        description="test", target_agent="researcher",
        trigger=InjectionTrigger(tool_name="read_text_file", occurrence=1),
    )
    trigger = lep.trigger

    event = make_tool_event(1, tool_name="read_text_file")
    d1 = matcher.evaluate(lep.code, trigger, event, 0)
    assert d1.fired

    event2 = make_tool_event(2, tool_name="read_text_file")
    d2 = matcher.evaluate(lep.code, trigger, event2, 1)
    assert not d2.fired
    ok("trigger_matcher_lep_integration")


def test_exporter_observable():
    with tempfile.TemporaryDirectory() as tmpdir:
        event = make_tool_event(1, lep_injected=True)
        trace = Trace(
            trace_id="t1", execution_id="t1",
            variant=TraceVariant.BENIGN, events=[event],
            metadata={"condition": "benign"},
        )
        exp = ObservableExporter(Path(tmpdir))
        out = exp.export_trace(trace, "test.jsonl")
        with open(out) as f:
            data = json.loads(f.readline())
        assert "hidden_benchmark_metadata" not in data
        ok("exporter_observable")


def test_exporter_analysis():
    with tempfile.TemporaryDirectory() as tmpdir:
        event = make_tool_event(1, lep_injected=True)
        trace = Trace(
            trace_id="t1", execution_id="t1",
            variant=TraceVariant.BENIGN, events=[event],
            metadata={"condition": "benign"},
        )
        exp = AnalysisExporter(Path(tmpdir))
        out = exp.export_trace(trace)
        with open(out) as f:
            data = json.load(f)
        assert "trace_metadata" in data
        assert "events" in data
        assert "labels" in data
        ok("exporter_analysis")


def test_prefix_exporter():
    with tempfile.TemporaryDirectory() as tmpdir:
        events = [make_user_event()]
        for i in range(6):
            lep = (i == 2)
            fail = (i == 4)
            evt = make_tool_event(i + 1, lep_injected=lep)
            if fail:
                evt.event_labels.introduces_downstream_failure = True
            events.append(evt)
        trace = Trace(
            trace_id="t1", execution_id="t1",
            variant=TraceVariant.MALIGNANT, events=events,
            metadata={"condition": "single_lep"},
        )
        exp = PrefixExporter(Path(tmpdir))
        records = exp.export_prefixes(trace)
        assert len(records) > 0
        end_events = [r.prefix_end_event for r in records]
        assert 3 in end_events
        assert 5 in end_events
        assert 6 in end_events
        ok("prefix_exporter")


def test_dry_run_backend():
    from generation.runner import DryRunBackend
    backend = DryRunBackend()
    # Step counter is not reset by reset() so that the deterministic
    # trajectory can span multiple agent turns.
    response = backend.generate("start")
    parsed = backend.parse_action(response)
    assert parsed is not None
    assert "action" in parsed
    ok("dry_run_backend")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("M1 END-TO-END INTEGRATION TESTS")
    print("=" * 60)

    section("Scenario Builder")
    test_scenario_builder_benign()
    test_scenario_builder_single_lep()
    test_scenario_builder_counterfactual()
    test_scenario_convergence()
    test_scenario_serialization()

    section("LEP Integration")
    test_lep_orchestrator()
    test_lep_instance_creation()
    test_all_lep_types_instantiate()
    test_trigger_matcher_lep_integration()

    section("Scenario Runner (Dry-Run)")
    test_dry_run_scenario()
    test_dry_run_perturbed_scenario()
    test_dry_run_backend()

    section("Exporters")
    test_exporter_observable()
    test_exporter_analysis()
    test_prefix_exporter()

    print()
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    if errors:
        print("\nFailures:")
        for name, msg in errors:
            print(f"  {name}: {msg}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
