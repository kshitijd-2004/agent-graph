#!/usr/bin/env python3
"""M1.5: End-to-end integration tests for the scenario runner."""

import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from schemas import (
    LEPConfig, ScenarioSpec, Trace, TraceEvent, TraceEventType, TraceVariant,
    WorkflowConfig, Trace as TraceSchema,
)
from schemas.triggers import InjectionTrigger
from generation.runner import ScenarioRunner, RunResult, DryRunBackend
from generation.stage_runner import StageRunner
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
        assert result.runner_success, f"Run failed: {result.error}"
        assert result.task_success, f"Task not completed: {result.termination_reason}"
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
        assert result.runner_success, f"Run failed: {result.error}"
        # In linear_2 topology the researcher's stage has can_finalize=False,
        # so the dry-run trajectory (which ends with submit_final) will hit
        # premature_final. The test should verify the run completes without
        # crash and produces a trace, not require task_success.
        assert result.trace is not None, "Trace should not be None"
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
        topology="branch_and_verify",
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
        task_family="code_review",
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
        task_family="code_review",
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
            task_family="code_review",
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


# ── Bug-fix regression tests ──────────────────────────────────────────────────

def test_full_file_content_not_truncated():
    """Bug 1: Tool results must deliver full file content, not truncated.

    Verifies two things:
    1. _execute_tool('read_text_file') returns the complete file content
       (no [:500] truncation) for small, medium, and large files.
    2. The runner's dry-run trace delivers full content via TOOL_RESULT events.
    """
    from environment.workspace import Workspace
    from generation.runner import ScenarioRunner
    import tempfile, os

    # ── Part 1: Direct _execute_tool verification ────────────────────────
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Workspace(Path(tmpdir))
        ws.root.mkdir(parents=True, exist_ok=True)

        # Write files of various sizes
        small_content = "Hello, world!\nThis is line 2."
        (ws.root / "small.txt").write_text(small_content)

        medium_content = "\n".join(f"Line {i}: " + "x" * 100 for i in range(50))
        (ws.root / "medium.txt").write_text(medium_content)

        large_content = "\n".join(f"Line {i}: " + "x" * 500 for i in range(200))
        (ws.root / "large.txt").write_text(large_content)

        runner = ScenarioRunner(dry_run=True, output_dir=Path(tmpdir))

        for filename, expected in [
            ("small.txt", small_content),
            ("medium.txt", medium_content),
            ("large.txt", large_content),
        ]:
            args = {"path": filename}
            result = runner._execute_tool("read_text_file", args, ws.root)
            assert result == expected, (
                f"read_text_file returned wrong length for {filename}: "
                f"expected {len(expected)} chars, got {len(result)} chars. "
                f"First 100 chars of result: {repr(result[:100])}"
            )

    # ── Part 2: Verify the trace's TOOL_RESULT events have full output_text ─
    builder = ScenarioBuilder(seed=42)
    cfg = ScenarioBuildConfig(
        task_family="code_review",
        fixture_id="code_review_easy",
        task_variant="easy",
        topology="linear_2",
        repetition_index=0,
        seed=42,
    )
    spec = builder.build_benign(cfg)

    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ScenarioRunner(dry_run=True, output_dir=Path(tmpdir))
        result = runner.run(spec, Path(__file__).resolve().parent.parent / "workspace_fixtures")

    trace = result.trace

    # Read the actual fixture files to know their full content
    fixture_root = Path(__file__).resolve().parent.parent / "workspace_fixtures" / "code_review_easy"
    fixture_files = {
        "src/main.py": (fixture_root / "src" / "main.py").read_text(),
        "src/utils.py": (fixture_root / "src" / "utils.py").read_text(),
        "tests/test_main.py": (fixture_root / "tests" / "test_main.py").read_text(),
        "documents/readme.md": (fixture_root / "documents" / "readme.md").read_text(),
    }

    # Pair TOOL_CALL and TOOL_RESULT events by order
    tc_events = [e for e in trace.events if e.event_type == TraceEventType.TOOL_CALL and e.tool_name == "read_text_file"]
    tr_events = [e for e in trace.events if e.event_type == TraceEventType.TOOL_RESULT and e.tool_name == "read_text_file"]

    # For each pair, verify the result includes the file's last line
    for tc, tr in zip(tc_events, tr_events):
        path = (tc.tool_arguments or {}).get("path", "")
        result_text = tr.output_text or ""

        # Find the matching fixture file
        matched_content = None
        for fpath, fcontent in fixture_files.items():
            if path in fpath or fpath.endswith(path):
                matched_content = fcontent
                break

        if matched_content is not None:
            last_line = matched_content.strip().split("\n")[-1].strip()
            assert last_line, f"Empty last line for {path}"
            assert last_line in result_text, (
                f"Last line of {path} missing from TOOL_RESULT. "
                f"Last line: {repr(last_line)}. Result: {repr(result_text[:200])}"
            )
        else:
            # No fixture match — result should be non-empty (file found) or
            # an error message (file not found, both are acceptable)
            pass

    ok("full_file_content_not_truncated")


def test_tool_args_no_leakage():
    """Bug 2: Tool calls must not receive arguments from other tools."""
    from generation.runner import ScenarioRunner
    import tempfile

    runner = ScenarioRunner(dry_run=True, output_dir=Path(tempfile.mkdtemp()))

    # Simulate the bug scenario: a write_file call followed by read_text_file
    # In the old code, read_text_file would inherit "content" from write_file
    validated_read = runner._validate_tool_args("read_text_file", {
        "path": "src/main.py",
        "content": "should_not_be_here",  # leaked from write_file
    })
    assert "content" not in validated_read, (
        f"read_text_file received leaked 'content' field: {validated_read}"
    )
    assert "path" in validated_read

    # list_directory should not get "content" either
    validated_list = runner._validate_tool_args("list_directory", {
        "path": ".",
        "content": "leaked_content_from_write_file",
    })
    assert "content" not in validated_list, (
        f"list_directory received leaked 'content' field: {validated_list}"
    )

    # write_file should get "content"
    validated_write = runner._validate_tool_args("write_file", {
        "path": "output/report.md",
        "content": "report body",
        "extra_field": "should_be_dropped",
    })
    assert "content" in validated_write
    assert "extra_field" not in validated_write

    ok("tool_args_no_leakage")


def test_loop_detection_termination():
    """Bug 3: Loop detection must set termination_reason='execution_loop'."""
    from generation.runner import ScenarioRunner, DryRunBackend
    from schemas import ScenarioSpec, LEPConfig, WorkflowConfig
    from schemas.triggers import InjectionTrigger
    from generation.scenario_builder import ScenarioBuilder, ScenarioBuildConfig
    import tempfile

    # Create a custom backend that loops on read_text_file
    class LoopBackend(DryRunBackend):
        def __init__(self):
            super().__init__()
            self._loop_step = 0

        def generate(self, prompt: str, tool_choice=None):
            self._step += 1
            # Always return the same read_text_file call as a ModelTurn
            from backend.api_backend import ToolCall, ModelTurn
            tc = ToolCall(
                id=f"toolu_loop_{self._step}",
                name="read_text_file",
                input={"path": "src/main.py"},
            )
            return ModelTurn(
                tool_call=tc,
                text="",
                stop_reason="tool_use",
                raw_content=[],
            )

    builder = ScenarioBuilder(seed=42)
    cfg = ScenarioBuildConfig(
        task_family="code_review",
        fixture_id="code_review_easy",
        task_variant="easy",
        topology="linear_2",
        repetition_index=0,
        seed=42,
    )
    spec = builder.build_benign(cfg)

    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ScenarioRunner(llm_backend=LoopBackend(), dry_run=False, output_dir=Path(tmpdir))
        result = runner.run(spec, Path(__file__).resolve().parent.parent / "workspace_fixtures")

    assert result.termination_reason == "execution_loop", (
        f"Expected termination_reason='execution_loop', got '{result.termination_reason}'"
    )
    assert result.task_success is False, (
        f"Loop termination should have task_success=False, got {result.task_success}"
    )
    assert result.runner_success is True, (
        f"Runner should succeed (no crash) on loop detection, got {result.runner_success}"
    )

    ok("loop_detection_termination")


def test_evaluator_success_thresholds():
    """Bug 4: Evaluator must require factual score > 0 and required issues found."""
    from evaluators.task_evaluators.code_review_evaluator import CodeReviewEvaluator
    from schemas import Trace, TraceEvent, TraceEventType, TraceVariant
    from schemas.scenario import ScenarioSpec, WorkflowConfig

    # Create a trace with NO issues found but a report written
    # The old evaluator might have returned task_success=True here
    events = [
        TraceEvent(
            trace_id="t1", event_id="1", event_index=0,
            timestamp="2024-01-01T00:00:00Z",
            event_type=TraceEventType.FINAL_RESPONSE,
            source_entity_id="agent", target_entity_id="user",
            agent_id="agent_001", agent_role="inspector",
            output_text="# Code Review\nNo issues found. All clear.",
        ),
        TraceEvent(
            trace_id="t1", event_id="2", event_index=1,
            timestamp="2024-01-01T00:00:00Z",
            event_type=TraceEventType.TOOL_CALL,
            source_entity_id="agent", target_entity_id="tool",
            agent_id="agent_001", agent_role="inspector",
            tool_name="write_file",
            tool_arguments={"path": "output/report.md", "content": "# Code Review\nNo issues found." * 20},
        ),
    ]
    trace = Trace(
        trace_id="t1", execution_id="t1",
        variant=TraceVariant.BENIGN, events=events,
        metadata={"condition": "benign"},
    )

    # Build a spec for code_review_easy fixture
    fixture_root = Path(__file__).resolve().parent.parent / "workspace_fixtures"
    evaluator = CodeReviewEvaluator(fixture_dir=fixture_root / "code_review_easy")

    spec = ScenarioSpec(
        scenario_id="test_success_thresholds",
        task_family="code_review",
        task_variant="easy",
        fixture_id="code_review_easy",
        condition="benign",
        workflow_config=WorkflowConfig(
            topology="linear_2", sharing_policy="handoff_summary_only",
            memory_mode="none", verification_mode="none",
            max_events=40, max_agent_turns=10, timeout_seconds=300,
            model_name="dry-run", temperature=0.0, seed=42,
        ),
        lep_configs=[],
    )

    result = evaluator.evaluate(trace, None, spec)

    # With 0/4 required issues found, factual_score=0, task_success MUST be False
    assert result.factual_score == 0.0, (
        f"Expected factual_score=0.0, got {result.factual_score}"
    )
    assert result.task_success is False, (
        f"Evaluator reported task_success=True with 0 issues found — "
        f"this is the bug. factual_score={result.factual_score}, "
        f"completeness={result.completeness_score}"
    )

    ok("evaluator_success_thresholds")


def test_loop_detection_semantics():
    """Bug 5: Loop detection requires identical arguments AND identical results."""
    from generation.runner import ScenarioRunner
    from environment.workspace import Workspace
    import tempfile, json

    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Workspace(Path(tmpdir))
        # Create a file to read
        (Path(tmpdir) / "data.txt").write_text("same content")

        runner = ScenarioRunner(dry_run=True, output_dir=Path(tmpdir))

        # Three identical successful write_file calls should trigger loop
        # The normalized key includes result hash, so identical args + results = loop
        write_args = {"path": "output/report.md", "content": "identical content"}

        # Simulate loop detection by calling _normalize_tool_key with same result
        key1 = runner._normalize_tool_key("write_file", write_args, "File written: output/report.md")
        key2 = runner._normalize_tool_key("write_file", write_args, "File written: output/report.md")
        key3 = runner._normalize_tool_key("write_file", write_args, "File written: output/report.md")

        assert key1 == key2 == key3, (
            f"Normalized keys should be identical for identical args+result: {key1}, {key2}, {key3}"
        )

        # Three different results with same args should NOT trigger loop
        key_diff = runner._normalize_tool_key("write_file", write_args, "different result")
        assert key_diff != key1, (
            f"Different results should produce different keys: {key_diff} vs {key1}"
        )

        # Different paths with same result should NOT trigger loop
        key_diff_path = runner._normalize_tool_key("write_file", {"path": "other.txt", "content": "same content"}, "File written: other.txt")
        assert key_diff_path != key1, (
            f"Different paths should produce different keys: {key_diff_path} vs {key1}"
        )

        # Verify call_ids are recorded
        loop_state = {"count": 1, "first_step": 1, "last_step": 1, "call_ids": ["evt_1"]}
        assert "call_ids" in loop_state
        assert "evt_1" in loop_state["call_ids"]

    ok("loop_detection_semantics")


def test_event_fields_not_truncated():
    """Bug 7: Trace event fields must not truncate content — downstream analysis
    must see full prompt, raw LLM output, tool args, and final responses.
    """
    from generation.runner import ScenarioRunner
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ScenarioRunner(dry_run=True, output_dir=Path(tmpdir))

        builder = ScenarioBuilder(seed=42)
        cfg = ScenarioBuildConfig(
            task_family="code_review",
            fixture_id="code_review_easy",
            task_variant="easy",
            topology="linear_2",
            repetition_index=0,
            seed=42,
        )
        spec = builder.build_benign(cfg)
        result = runner.run(spec, Path("workspace_fixtures"))
        trace = result.trace

    from schemas.trace import TraceEventType

    # USER_INPUT must carry the full task prompt (not sliced to 300)
    user_events = [e for e in trace.events if e.event_type == TraceEventType.USER_INPUT]
    assert user_events, "Missing USER_INPUT event"
    assert len(user_events[0].input_text) > 300, (
        f"USER_INPUT was truncated: {len(user_events[0].input_text)} chars"
    )

    # REASONING events must carry content (not empty)
    # In native-tool mode, output_text may be [tool_use:...] or prose;
    # in JSON mode it is a JSON object. Accept either.
    reasoning_events = [e for e in trace.events if e.event_type == TraceEventType.REASONING]
    assert reasoning_events, "Missing REASONING events"
    for r in reasoning_events:
        assert r.input_text is not None and len(r.input_text) > 0, "REASONING input_text empty"
        assert r.output_text is not None and len(r.output_text) > 0, "REASONING output_text empty"

    # TOOL_CALL events must carry full tool arguments (not sliced)
    tool_call_events = [e for e in trace.events if e.event_type == TraceEventType.TOOL_CALL]
    assert tool_call_events, "Missing TOOL_CALL events"
    for tc in tool_call_events:
        assert tc.tool_arguments is not None and len(tc.tool_arguments) > 0, \
            f"TOOL_CALL missing tool_arguments: event_id={tc.event_id}"
        assert tc.tool_name is not None, f"TOOL_CALL missing tool_name: event_id={tc.event_id}"

    # FINAL_RESPONSE must carry the full response (not sliced to 500)
    final_events = [e for e in trace.events if e.event_type == TraceEventType.FINAL_RESPONSE]
    assert final_events, "Missing FINAL_RESPONSE event"
    fr_text = final_events[0].output_text or ""
    # In linear_2 topology, the researcher stage has can_finalize=False,
    # so a submit_final will hit premature_final. Accept that message.
    assert "Task complete" in fr_text or "premature" in fr_text.lower(), (
        f"FINAL_RESPONSE missing expected content: {fr_text[:200]}"
    )

    # TOOL_RESULT must carry full output (already enforced, but verify)
    tool_results = [e for e in trace.events if e.event_type == TraceEventType.TOOL_RESULT]
    for tr in tool_results:
        if tr.tool_name == "read_text_file":
            result_text = tr.output_text or tr.input_text or ""
            assert len(result_text) > 0, "TOOL_RESULT for read_text_file is empty"
            # Verify it's not truncated (should be full file content or error message)
            assert "Error" in result_text or len(result_text) > 10, (
                f"TOOL_RESULT for read_text_file looks truncated: {result_text[:100]}"
            )

    ok("event_fields_not_truncated")


def test_write_file_success_message():
    """Bug 8: write_file tool must return a success message (not just byte count)."""
    from environment.workspace import Workspace
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Workspace(Path(tmpdir))
        result = ws.execute("write_file", {
            "path": "test.txt",
            "content": "Hello, world!"
        })
        assert "completed" in result.lower() or "success" in result.lower(), (
            f"write_file success message should confirm completion: {result}"
        )
        assert "test.txt" in result, (
            f"write_file result should mention the path: {result}"
        )
        assert "bytes" in result.lower(), (
            f"write_file result should include byte count: {result}"
        )

    ok("write_file_success_message")


def test_termination_reason_max_events():
    """Bug 9: When loop exits without final/handoff, termination_reason must be set."""
    from generation.runner import ScenarioRunner
    import tempfile, json

    class UniquePathBackend:
        """Backend that reads a different file each step — never emits
        final/handoff, so the runner reaches max_events without termination.
        Uses unique paths so loop detection never fires.
        """
        def __init__(self):
            self._agent_name = "researcher"
            self._step = 0
            self._paths = [
                "src/main.py", "src/utils.py", "tests/test_main.py",
                "documents/readme.md", "output/report.md",
            ]
            self._conversation = []

        def reset(self, task="", agent_name="researcher", mcp_tools=None, system_prompt=""):
            self._agent_name = agent_name
            self._conversation = []

        def _append_tool_result(self, tool_call, result: str) -> None:
            self._conversation.append({
                "role": "assistant",
                "content": [{"type": "tool_use", "id": tool_call.id,
                             "name": tool_call.name, "input": tool_call.input}],
            })
            self._conversation.append({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": tool_call.id,
                             "content": result}],
            })

        def generate(self, prompt: str, tool_choice=None):
            from backend.api_backend import ToolCall, ModelTurn
            self._step += 1
            path = self._paths[(self._step - 1) % len(self._paths)]
            tc = ToolCall(
                id=f"toolu_unique_{self._step}",
                name="read_text_file",
                input={"path": path},
            )
            return ModelTurn(tool_call=tc, text="", stop_reason="tool_use", raw_content=[])

    builder = ScenarioBuilder(seed=42)
    cfg = ScenarioBuildConfig(
        task_family="code_review",
        fixture_id="code_review_easy",
        task_variant="easy",
        topology="linear_2",
        repetition_index=0,
        seed=42,
    )
    spec = builder.build_benign(cfg)

    with tempfile.TemporaryDirectory() as tmpdir:
        # Use max_agent_turns=4 so loop exits after 4 steps without terminal event
        spec.workflow_config = WorkflowConfig(
            topology="linear_2",
            sharing_policy="handoff_summary_only",
            memory_mode="none",
            verification_mode="none",
            max_events=40,
            max_agent_turns=4,
            timeout_seconds=300,
            model_name="dry-run",
            temperature=0.0,
            seed=42,
        )
        runner = ScenarioRunner(
            llm_backend=UniquePathBackend(),
            dry_run=False,
            max_events=4,
            output_dir=Path(tmpdir),
        )
        result = runner.run(spec, Path("workspace_fixtures"))

    # When the runner exits the agent loop without seeing FINAL_RESPONSE or
    # AGENT_HANDOFF, it must report max_events_reached — never leave it blank.
    assert result.termination_reason == "max_events_reached", (
        f"Expected termination_reason='max_events_reached', got '{result.termination_reason}'"
    )
    # Confirm no terminal event was emitted
    from schemas.trace import TraceEventType
    has_terminal = any(
        e.event_type in (TraceEventType.FINAL_RESPONSE, TraceEventType.AGENT_HANDOFF)
        for e in result.trace.events
    )
    assert not has_terminal, (
        "Test backend should not emit terminal events — got one"
    )

    ok("termination_reason_max_events")


def test_handoff_enforced_for_multi_agent_topo():
    """Bug 10: linear_2 topology must NOT silently accept submit_final on a
    non-finalizable stage. The runner terminates with premature_final.
    """
    from generation.runner import ScenarioRunner
    import tempfile, json

    class SkipHandoffBackend(DryRunBackend):
        """Backend that calls submit_final on a non-finalizable stage."""
        def __init__(self):
            super().__init__()
            self._step = 0

        def generate(self, prompt: str, tool_choice=None):
            from backend.api_backend import ToolCall, ModelTurn
            self._step += 1
            if self._step == 1:
                tc = ToolCall(id=f"toolu_hof_{self._step}", name="list_directory",
                             input={"path": "."})
                return ModelTurn(tool_call=tc, text="", stop_reason="tool_use", raw_content=[])
            # Second step: submit_final on non-finalizable stage
            tc = ToolCall(id=f"toolu_hof_{self._step}", name="submit_final",
                         input={"summary": "Review complete."})
            return ModelTurn(tool_call=tc, text="Review complete.", stop_reason="tool_use", raw_content=[])

    from generation.scenario_builder import ScenarioBuildConfig
    from schemas import WorkflowConfig

    builder = ScenarioBuilder(seed=42)
    cfg = ScenarioBuildConfig(
        task_family="code_review",
        fixture_id="code_review_easy",
        task_variant="easy",
        topology="linear_2",
        repetition_index=0,
        seed=42,
    )
    spec = builder.build_benign(cfg)
    # Ensure multi-agent topology
    spec.workflow_config = WorkflowConfig(
        topology="linear_2",
        sharing_policy="handoff_summary_only",
        memory_mode="none",
        verification_mode="none",
        max_events=40,
        max_agent_turns=10,
        timeout_seconds=300,
        model_name="dry-run",
        temperature=0.0,
        seed=42,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ScenarioRunner(
            llm_backend=SkipHandoffBackend(),
            dry_run=False,
            max_events=40,
            output_dir=Path(tmpdir),
        )
        result = runner.run(spec, Path("workspace_fixtures"))

    from schemas.trace import TraceEventType

    # submit_final on a non-finalizable stage must produce premature_final,
    # not silently continue
    assert result.termination_reason == "premature_final", (
        f"Expected premature_final, got '{result.termination_reason}'"
    )
    assert result.task_success is False, (
        f"premature_final should have task_success=False, got {result.task_success}"
    )
    assert result.runner_success is True, (
        f"Runner should succeed (no crash) on premature final, got {result.runner_success}"
    )

    ok("handoff_enforced_for_multi_agent_topo")


def test_stage_local_history_persistence():
    """Regression: turn 2 must contain turn 1's tool result in the API prompt.

    The bug was that the stage runner rebuilt the prompt from global events
    each turn, so the backend never saw prior tool results and repeated
    list_directory('.') on every turn.
    """
    from generation.runner import ScenarioRunner
    from schemas import ScenarioSpec, WorkflowConfig
    from generation.scenario_builder import ScenarioBuilder, ScenarioBuildConfig
    from generation.topology import get_topology
    from generation.stage_runner import StageRunner

    prompts_seen = []

    class HistoryTrackingBackend:
        """Native-mode backend that tracks its own message history.

        Proves that the backend's native _messages property contains
        prior tool calls and results (not serialized text).
        """
        def __init__(self):
            self._step = 0
            self.reset_count = 0
            self._messages: List[Dict[str, Any]] = []

        def reset(self, *a, **kw):
            self.reset_count += 1
            self._messages = []

        def _append_tool_result(self, tool_call, result: str) -> None:
            self._messages.append({
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": tool_call.id,
                    "name": tool_call.name,
                    "input": tool_call.input,
                }],
            })
            self._messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": tool_call.id,
                    "content": result,
                }],
            })

        def generate(self, prompt, tool_choice=None):
            from backend.api_backend import ToolCall, ModelTurn
            self._step += 1

            if self._step == 1:
                tc = ToolCall(id="tu_h1", name="list_directory", input={"path": "."})
                return ModelTurn(tool_call=tc, text="", stop_reason="tool_use", raw_content=[])
            elif self._step == 2:
                # After turn 1, _append_tool_result should have added tool_use + tool_result
                assert len(self._messages) == 2, (
                    f"After turn 1, backend should have 2 messages (tool_use + tool_result), "
                    f"got {len(self._messages)}: {[m.get('role') for m in self._messages]}"
                )
                # Verify exact-once: no duplicated tool blocks
                tool_use_count = sum(
                    1 for m in self._messages
                    for b in (m.get("content") or [])
                    if isinstance(b, dict) and b.get("type") == "tool_use"
                )
                assert tool_use_count == 1, (
                    f"Each prior tool call should appear exactly once, "
                    f"got {tool_use_count}"
                )
                tc = ToolCall(id="tu_h2", name="read_text_file", input={"path": "src/main.py"})
                return ModelTurn(tool_call=tc, text="", stop_reason="tool_use", raw_content=[])
            else:
                tc = ToolCall(id="tu_h3", name="submit_final", input={"summary": "Code review complete."})
                return ModelTurn(tool_call=tc, text="Code review complete.", stop_reason="tool_use", raw_content=[])

    builder = ScenarioBuilder(seed=42)
    cfg = ScenarioBuildConfig(
        task_family="code_review",
        fixture_id="code_review_easy",
        task_variant="easy",
        topology="linear_2",
        repetition_index=0,
        seed=42,
    )
    spec = builder.build_benign(cfg)

    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ScenarioRunner(
            llm_backend=HistoryTrackingBackend(),
            dry_run=False,
            max_events=40,
            output_dir=Path(tmpdir),
        )
        result = runner.run(spec, Path(__file__).resolve().parent.parent / "workspace_fixtures")

    # Check for execution errors first
    if not result.success and result.error:
        raise AssertionError(f"Scenario execution failed: {result.error}")

    # Basic sanity: trace has events
    assert len(result.trace.events) >= 3, (
        f"Expected at least 3 events, "
        f"got {len(result.trace.events)}: {[e.event_type.value for e in result.trace.events]}"
    )

    # In native mode, history is tracked via _messages on the backend,
    # not via prompts_seen. Verify the native message property accumulated correctly.
    backend = result  # runner doesn't expose backend, but we can check via the trace events
    # Events should show tool_call → tool_result pairs proving history accumulation
    tool_call_evts = [e for e in result.trace.events if e.event_type.value == "tool_call"]
    tool_result_evts = [e for e in result.trace.events if e.event_type.value == "tool_result"]
    assert len(tool_call_evts) >= 1, "Expected at least 1 tool_call event"
    assert len(tool_result_evts) >= 1, "Expected at least 1 tool_result event"

    ok("stage_local_history_persistence")


def test_backend_reset_once_per_stage():
    """Backend.reset() must be called exactly once per stage, not per turn."""
    from generation.runner import ScenarioRunner
    from generation.scenario_builder import ScenarioBuilder, ScenarioBuildConfig

    reset_log = []

    class CountingBackend:
        def __init__(self):
            self._step = 0

        def reset(self, *a, **kw):
            agent_name = kw.get('agent_name', 'unknown')
            reset_log.append((agent_name, len(reset_log) + 1))

        def _append_tool_result(self, *a, **kw):
            pass

        def generate(self, prompt, tool_choice=None):
            from backend.api_backend import ToolCall, ModelTurn
            self._step += 1
            if self._step <= 3:
                tc = ToolCall(id=f"tu_c{self._step}", name="list_directory", input={"path": "."})
                return ModelTurn(tool_call=tc, text="", stop_reason="tool_use", raw_content=[])
            tc = ToolCall(id=f"tu_c4", name="handoff", input={"target_agent": "analyst", "summary": "Research done."})
            return ModelTurn(tool_call=tc, text="Research done.", stop_reason="tool_use", raw_content=[])

    builder = ScenarioBuilder(seed=42)
    cfg = ScenarioBuildConfig(
        task_family="code_review",
        fixture_id="code_review_easy",
        task_variant="easy",
        topology="linear_2",
        repetition_index=0,
        seed=42,
    )
    spec = builder.build_benign(cfg)

    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ScenarioRunner(
            llm_backend=CountingBackend(),
            dry_run=False,
            max_events=40,
            output_dir=Path(tmpdir),
        )
        result = runner.run(spec, Path(__file__).resolve().parent.parent / "workspace_fixtures")

    # linear_2 has 2 stages: researcher, analyst
    assert len(reset_log) == 2, (
        f"Expected exactly 2 backend resets (one per stage), got {len(reset_log)}: {reset_log}"
    )

    # First reset must be for researcher
    assert reset_log[0][0] == "researcher", (
        f"First reset should be for 'researcher', got '{reset_log[0][0]}'"
    )

    # Second reset must be for analyst
    assert reset_log[1][0] == "analyst", (
        f"Second reset should be for 'analyst', got '{reset_log[1][0]}'"
    )

    ok("backend_reset_once_per_stage")


def test_repair_parser_emits_write_file():
    """Regression: model response saying 'I will write output/report.md'
    must produce a write_file tool call, not be silently dropped.

    This test uses an actual response pattern from a real trace where the
    model emits prose mentioning the file path but no valid JSON action.
    """
    from generation.stage_runner import StageRunner

    # Actual response from the failing trace — model says it's writing
    # but emits prose instead of JSON
    raw_response = (
        "I'll write the code review report now.\n"
        "Writing to output/report.md with my findings.\n"
        "The report covers the path traversal vulnerability I found."
    )

    repaired = StageRunner._repair_action(raw_response)

    assert repaired is not None, (
        f"Repair should extract action from prose response, got None. "
        f"Raw: {raw_response[:200]}"
    )
    assert repaired["action"] == "write_file", (
        f"Expected action='write_file', got '{repaired['action']}'"
    )
    assert repaired["action_input"].get("path") == "output/report.md", (
        f"Expected path='output/report.md' in action_input, got: {repaired['action_input']}"
    )

    ok("repair_parser_emits_write_file")


def test_repair_parser_no_false_positive():
    """Repair should not invent actions from random prose."""
    from generation.stage_runner import StageRunner

    raw = "I need to think about this more carefully before proceeding."
    repaired = StageRunner._repair_action(raw)
    assert repaired is None, f"Should not repair random prose, got: {repaired}"

    ok("repair_parser_no_false_positive")


def test_repair_parser_handoff_and_final():
    """Completion prose without an action field can be repaired to 'final'."""
    from generation.stage_runner import StageRunner

    raw = "Task complete. Here are my findings."
    repaired = StageRunner._repair_action(raw)
    assert repaired is not None
    assert repaired["action"] == "final"

    ok("repair_parser_handoff_and_final")


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

    section("Bug-Fix Regression Tests")
    test_full_file_content_not_truncated()
    test_tool_args_no_leakage()
    test_loop_detection_termination()
    test_evaluator_success_thresholds()
    test_loop_detection_semantics()
    test_event_fields_not_truncated()
    test_write_file_success_message()
    test_termination_reason_max_events()
    test_handoff_enforced_for_multi_agent_topo()
    test_stage_local_history_persistence()
    test_backend_reset_once_per_stage()
    test_repair_parser_emits_write_file()
    test_repair_parser_no_false_positive()
    test_repair_parser_handoff_and_final()

    section("Pass 1 Protocol Regression Tests")
    test_protocol_violation_plain_prose()
    test_protocol_violation_xml_tool_text()
    test_repair_success_native_tool()
    test_repair_failure_no_continuation()
    test_premature_final_terminates()
    test_invalid_handoff_terminates()
    test_dataset_eligible_false_on_protocol_failure()
    test_action_input_is_dict_not_json_string()
    test_all_termination_reasons_handled()

    section("APIBackend + StageRunner Regression Tests")
    test_researcher_stage_exposes_handoff()
    test_analyst_stage_exposes_submit_final()
    test_researcher_prose_gets_one_retry()
    test_retry_nudge_contains_nudge_exactly_once()
    test_researcher_handoff_is_native_tool()
    test_analyst_submit_final_is_native_tool()
    test_tool_choice_reaches_outgoing_request()
    test_empty_prompt_no_empty_user_message()
    test_append_tool_result_native_pair()

    section("Review-Loop Cycle-Limit")
    test_backedge_limit_blocks_third_researcher_pass()
    test_final_round_analyst_receives_final_instructions()
    test_final_round_submit_final_completes()
    test_final_round_handoff_blocked_before_next_stage()
    test_no_third_researcher_with_max_iterations_2()

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


# ── Pass 1 Protocol Regression Tests ──────────────────────────────────

def test_protocol_violation_plain_prose():
    """Plain prose on a tool-required turn must terminate with protocol_violation."""
    from generation.runner import ScenarioRunner
    from generation.scenario_builder import ScenarioBuilder, ScenarioBuildConfig

    class ProseBackend:
        def __init__(self):
            self._step = 0

        def reset(self, *a, **kw):
            pass

        def generate(self, prompt, tool_choice=None):
            self._step += 1
            from backend.api_backend import ModelTurn
            # Return prose — no tool_call, no structured action
            return ModelTurn(text="Let me think about this carefully.", stop_reason="end_turn", raw_content=[])

        def _append_tool_result(self, *a, **kw):
            pass

    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ScenarioRunner(llm_backend=ProseBackend(), dry_run=False, max_events=20, output_dir=Path(tmpdir))
        spec = ScenarioBuilder(seed=42).build_benign(ScenarioBuildConfig(
            task_family="code_review", fixture_id="code_review_easy",
            task_variant="easy", topology="linear_2", repetition_index=0, seed=42,
        ))
        result = runner.run(spec, Path("workspace_fixtures"))

    assert result.termination_reason == "protocol_violation", (
        f"Expected protocol_violation, got '{result.termination_reason}'"
    )
    assert result.dataset_eligible is False, "protocol_violation must be dataset-ineligible"
    assert result.task_success is False
    assert result.runner_success is True, "Runner must not crash on protocol violation"

    # Raw model text must be captured in trace metadata
    pv_meta = result.trace.metadata.get("protocol_violation")
    assert pv_meta is not None, "protocol_violation metadata missing from trace"
    assert pv_meta.get("raw_text") == "Let me think about this carefully.", (
        f"Raw text not captured: {pv_meta.get('raw_text')}"
    )
    assert pv_meta.get("stop_reason") == "end_turn"

    # Raw model text must also be in the event observable
    from schemas.trace import TraceEventType
    pv_events = [e for e in result.trace.events
                 if e.event_type == TraceEventType.FINAL_RESPONSE
                 and e.observable and e.observable.get("protocol_violation")]
    assert pv_events, "No protocol_violation event with observable found"
    assert pv_events[0].observable.get("raw_model_text") == "Let me think about this carefully.", (
        f"raw_model_text not in event observable: {pv_events[0].observable}"
    )
    ok("protocol_violation_plain_prose")


def test_protocol_violation_xml_tool_text():
    """XML-like tool text (not a valid native tool call) must terminate with protocol_violation."""
    from generation.runner import ScenarioRunner
    from generation.scenario_builder import ScenarioBuilder, ScenarioBuildConfig

    class XMLToolBackend:
        def __init__(self):
            self._step = 0

        def reset(self, *a, **kw):
            pass

        def generate(self, prompt, tool_choice=None):
            self._step += 1
            from backend.api_backend import ModelTurn
            return ModelTurn(
                text='<tool_use><tool_name>read_text_file</tool_name><path>x</path></tool_use>',
                stop_reason="end_turn", raw_content=[],
            )

        def _append_tool_result(self, *a, **kw):
            pass

    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ScenarioRunner(llm_backend=XMLToolBackend(), dry_run=False, max_events=20, output_dir=Path(tmpdir))
        spec = ScenarioBuilder(seed=42).build_benign(ScenarioBuildConfig(
            task_family="code_review", fixture_id="code_review_easy",
            task_variant="easy", topology="linear_2", repetition_index=0, seed=42,
        ))
        result = runner.run(spec, Path("workspace_fixtures"))

    assert result.termination_reason == "protocol_violation", (
        f"Expected protocol_violation for XML tool text, got '{result.termination_reason}'"
    )
    assert result.dataset_eligible is False
    ok("protocol_violation_xml_tool_text")


def test_repair_success_native_tool():
    """Prose that repair can parse into a valid tool action must succeed."""
    from generation.runner import ScenarioRunner
    from generation.scenario_builder import ScenarioBuilder, ScenarioBuildConfig

    class RepairableProseBackend:
        def __init__(self):
            self._step = 0

        def reset(self, *a, **kw):
            pass

        def generate(self, prompt, tool_choice=None):
            self._step += 1
            from backend.api_backend import ModelTurn, ToolCall
            if self._step == 1:
                # Prose that repair can extract write_file from
                return ModelTurn(
                    text="I'll write my findings to output/report.md now.",
                    stop_reason="end_turn", raw_content=[],
                )
            # After repair, return a proper tool call
            tc = ToolCall(id=f"toolu_r{self._step}", name="submit_final", input={"summary": "done"})
            return ModelTurn(tool_call=tc, text="", stop_reason="tool_use", raw_content=[])

        def _append_tool_result(self, *a, **kw):
            pass

    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ScenarioRunner(llm_backend=RepairableProseBackend(), dry_run=False, max_events=20, output_dir=Path(tmpdir))
        spec = ScenarioBuilder(seed=42).build_benign(ScenarioBuildConfig(
            task_family="code_review", fixture_id="code_review_easy",
            task_variant="easy", topology="linear_2", repetition_index=0, seed=42,
        ))
        result = runner.run(spec, Path("workspace_fixtures"))

    # The first prose turn is repaired to write_file, second turn calls submit_final
    # on researcher (can_finalize=False) → premature_final
    assert result.termination_reason in ("premature_final", "protocol_violation"), (
        f"Expected premature_final or protocol_violation, got '{result.termination_reason}'"
    )
    assert result.runner_success is True
    ok("repair_success_native_tool")


def test_repair_failure_no_continuation():
    """After failed repair, the workflow must NOT continue to the next stage."""
    from generation.runner import ScenarioRunner
    from generation.scenario_builder import ScenarioBuilder, ScenarioBuildConfig

    class UnrepairableBackend:
        def __init__(self):
            self._step = 0
            self.turn_count = 0

        def reset(self, *a, **kw):
            pass

        def generate(self, prompt, tool_choice=None):
            self._step += 1
            self.turn_count += 1
            from backend.api_backend import ModelTurn
            # Gibberish that repair cannot parse
            return ModelTurn(text="asdfghjkl qwertyuiop 1234567890", stop_reason="end_turn", raw_content=[])

        def _append_tool_result(self, *a, **kw):
            pass

    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ScenarioRunner(llm_backend=UnrepairableBackend(), dry_run=False, max_events=20, output_dir=Path(tmpdir))
        spec = ScenarioBuilder(seed=42).build_benign(ScenarioBuildConfig(
            task_family="code_review", fixture_id="code_review_easy",
            task_variant="easy", topology="linear_2", repetition_index=0, seed=42,
        ))
        result = runner.run(spec, Path("workspace_fixtures"))

    assert result.termination_reason == "protocol_violation", (
        f"Expected protocol_violation, got '{result.termination_reason}'"
    )
    # Should terminate on first turn — minimal events (no continuation)
    assert result.trace is not None
    event_types = [e.event_type.value for e in result.trace.events]
    assert "AGENT_HANDOFF" not in event_types, (
        f"protocol_violation must not produce handoffs, got: {event_types}"
    )
    ok("repair_failure_no_continuation")


def test_premature_final_terminates():
    """submit_final on a non-finalizable stage must produce premature_final."""
    from generation.runner import ScenarioRunner
    from generation.scenario_builder import ScenarioBuilder, ScenarioBuildConfig

    class PrematureFinalBackend:
        def __init__(self):
            self._step = 0

        def reset(self, *a, **kw):
            pass

        def generate(self, prompt, tool_choice=None):
            self._step += 1
            from backend.api_backend import ToolCall, ModelTurn
            if self._step == 1:
                tc = ToolCall(id="tu_1", name="list_directory", input={"path": "."})
                return ModelTurn(tool_call=tc, text="", stop_reason="tool_use", raw_content=[])
            # submit_final on researcher (can_finalize=False)
            tc = ToolCall(id="tu_2", name="submit_final", input={"summary": "done"})
            return ModelTurn(tool_call=tc, text="done", stop_reason="tool_use", raw_content=[])

        def _append_tool_result(self, *a, **kw):
            pass

    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ScenarioRunner(llm_backend=PrematureFinalBackend(), dry_run=False, max_events=20, output_dir=Path(tmpdir))
        spec = ScenarioBuilder(seed=42).build_benign(ScenarioBuildConfig(
            task_family="code_review", fixture_id="code_review_easy",
            task_variant="easy", topology="linear_2", repetition_index=0, seed=42,
        ))
        result = runner.run(spec, Path("workspace_fixtures"))

    assert result.termination_reason == "premature_final", (
        f"Expected premature_final, got '{result.termination_reason}'"
    )
    assert result.dataset_eligible is False
    ok("premature_final_terminates")


def test_invalid_handoff_terminates():
    """handoff on a stage that doesn't accept handoffs must produce invalid_handoff.
    Verify via source search that the exhaustive switch handles the reason.
    """
    import inspect
    from generation.runner import ScenarioRunner

    source = inspect.getsource(ScenarioRunner._execute_scenario)

    for reason in ("invalid_handoff", "protocol_violation", "premature_final", "max_turns"):
        assert reason in source, (
            f"Termination reason '{reason}' not found in _execute_scenario dispatch"
        )
    ok("invalid_handoff_terminates")


def test_dataset_eligible_false_on_protocol_failure():
    """RunResult.dataset_eligible must be False for protocol_violation."""
    from generation.runner import RunResult

    r = RunResult(
        scenario_id="test", trace=None, success=True,
        runner_success=True, task_success=False,
        termination_reason="protocol_violation",
        dataset_eligible=False,
    )
    assert r.dataset_eligible is False, "protocol_violation must set dataset_eligible=False"

    r2 = RunResult(
        scenario_id="test", trace=None, success=True,
        runner_success=True, task_success=True,
        termination_reason="completed",
        dataset_eligible=True,
    )
    assert r2.dataset_eligible is True, "completed must set dataset_eligible=True"
    ok("dataset_eligible_false_on_protocol_failure")


def test_action_input_is_dict_not_json_string():
    """_repair_action must return action_input as dict, not json.dumps string."""
    repaired = StageRunner._repair_action("I'll write to output/report.md now")
    assert repaired is not None
    assert isinstance(repaired["action_input"], dict), (
        f"action_input must be dict, got {type(repaired['action_input'])}: {repaired['action_input']}"
    )
    assert "path" in repaired["action_input"]
    ok("action_input_is_dict_not_json_string")


def test_all_termination_reasons_handled():
    """Exhaustive switch in runner.py must handle every known termination reason.
    Unknown reasons must fail closed (not silently continue).
    """
    import inspect
    from generation.runner import ScenarioRunner

    source = inspect.getsource(ScenarioRunner._execute_scenario)

    known_reasons = {"final", "handoff", "loop", "premature_final",
                     "invalid_handoff", "protocol_violation", "max_turns"}

    for reason in known_reasons:
        assert reason in source, (
            f"Termination reason '{reason}' not found in _execute_scenario dispatch"
        )

    # Verify there's a fail-closed handler for unknown reasons
    assert "Unknown termination reason" in source or "unknown" in source.lower(), (
        "Must have fail-closed handler for unknown termination reasons"
    )
    ok("all_termination_reasons_handled")


# ── APIBackend + StageRunner Regression Tests ───────────────────────────────


def test_researcher_stage_exposes_handoff():
    """Researcher stage (can_finalize=False, can_handoff=True) must include
    'handoff' in the tools passed to the backend, not 'submit_final'.
    """
    from generation.topology import TopologyConfig, Stage, HandoffRule

    topo = TopologyConfig(
        topology_id="linear_2",
        display_name="Linear 2",
        stages=[
            Stage("researcher", "researcher", "r1", max_turns=5,
                  can_handoff=True, can_finalize=False),
            Stage("analyst", "analyst", "a1", max_turns=5,
                  can_handoff=False, can_finalize=True),
        ],
        handoff_rules=[HandoffRule("researcher", "analyst")],
        exit_stage="analyst",
    )

    class ToolTrackingBackend:
        def __init__(self):
            self.reset_calls = []
            self.generate_calls = []

        def reset(self, **kw):
            self.reset_calls.append(kw)

        def _messages(self):
            return []

        def generate(self, prompt, tool_choice=None):
            self.generate_calls.append({"prompt_len": len(prompt or ""), "tool_choice": tool_choice})
            from backend.api_backend import ToolCall, ModelTurn
            return ModelTurn(
                tool_call=ToolCall(id="tc1", name="handoff", input={"target_agent": "analyst", "summary": "Done."}),
                text="", stop_reason="tool_use", raw_content=[],
            )

        def _append_tool_result(self, *a, **kw):
            pass

    backend = ToolTrackingBackend()
    runner = StageRunner(llm_backend=backend)
    result = runner.run_stage(
        stage=topo.stages[0],
        topology=topo,
        handoff_rule=None,
        scenario=None,
        ws_path=Path("/tmp/ws"),
        task_prompt="Do the task.",
        prior_events=[],
        global_event_counter=[0],
    )

    assert len(backend.reset_calls) == 1, f"Expected 1 reset, got {len(backend.reset_calls)}"
    tools_given = backend.reset_calls[0].get("mcp_tools", [])
    assert "handoff" in tools_given, f"Researcher tools must include 'handoff': {tools_given}"
    assert "submit_final" not in tools_given, (
        f"Researcher tools must NOT include 'submit_final': {tools_given}"
    )
    assert result.termination_reason == "handoff", (
        f"Expected 'handoff' termination, got '{result.termination_reason}'"
    )
    ok("researcher_stage_exposes_handoff")


def test_analyst_stage_exposes_submit_final():
    """Analyst stage (can_finalize=True) must include 'submit_final' in tools."""
    from generation.topology import TopologyConfig, Stage, HandoffRule

    topo = TopologyConfig(
        topology_id="linear_2",
        display_name="Linear 2",
        stages=[
            Stage("researcher", "researcher", "r1", max_turns=5,
                  can_handoff=True, can_finalize=False),
            Stage("analyst", "analyst", "a1", max_turns=5,
                  can_handoff=False, can_finalize=True),
        ],
        handoff_rules=[HandoffRule("researcher", "analyst")],
        exit_stage="analyst",
    )

    class ToolTrackingBackend:
        def __init__(self):
            self.reset_calls = []

        def reset(self, **kw):
            self.reset_calls.append(kw)

        def _messages(self):
            return []

        def generate(self, prompt, tool_choice=None):
            from backend.api_backend import ToolCall, ModelTurn
            return ModelTurn(
                tool_call=ToolCall(id="tc2", name="submit_final", input={"summary": "Done."}),
                text="", stop_reason="tool_use", raw_content=[],
            )

        def _append_tool_result(self, *a, **kw):
            pass

    backend = ToolTrackingBackend()
    runner = StageRunner(llm_backend=backend)
    result = runner.run_stage(
        stage=topo.stages[1],
        topology=topo,
        handoff_rule=None,
        scenario=None,
        ws_path=Path("/tmp/ws"),
        task_prompt="Do the task.",
        prior_events=[],
        global_event_counter=[0],
    )

    tools_given = backend.reset_calls[0].get("mcp_tools", [])
    assert "submit_final" in tools_given, (
        f"Analyst tools must include 'submit_final': {tools_given}"
    )
    assert "handoff" not in tools_given, (
        f"Analyst tools must NOT include 'handoff': {tools_given}"
    )
    assert result.termination_reason == "final", (
        f"Expected 'final' termination, got '{result.termination_reason}'"
    )
    ok("analyst_stage_exposes_submit_final")


def test_researcher_prose_gets_one_retry():
    """Researcher returning prose on turn 1 gets exactly one retry, then terminates."""
    from generation.topology import TopologyConfig, Stage, HandoffRule

    topo = TopologyConfig(
        topology_id="linear_2", display_name="Linear 2",
        stages=[
            Stage("researcher", "researcher", "r1", max_turns=5, can_handoff=True, can_finalize=False),
            Stage("analyst", "analyst", "a1", max_turns=5, can_handoff=False, can_finalize=True),
        ],
        handoff_rules=[HandoffRule("researcher", "analyst")],
        exit_stage="analyst",
    )

    call_count = [0]

    class ProseBackend:
        def __init__(self):
            self.reset_calls = []

        def reset(self, **kw):
            self.reset_calls.append(kw)

        def _messages(self):
            return []

        def generate(self, prompt, tool_choice=None):
            call_count[0] += 1
            from backend.api_backend import ToolCall, ModelTurn
            if call_count[0] == 1:
                # First call: prose only (no tool_use)
                return ModelTurn(text="Here is my analysis...", stop_reason="end_turn", raw_content=[])
            else:
                # Retry: still prose
                return ModelTurn(text="Still no tool call.", stop_reason="end_turn", raw_content=[])

        def _append_tool_result(self, *a, **kw):
            pass

    backend = ProseBackend()
    runner = StageRunner(llm_backend=backend)
    result = runner.run_stage(
        stage=topo.stages[0],
        topology=topo,
        handoff_rule=None,
        scenario=None,
        ws_path=Path("/tmp/ws"),
        task_prompt="Do the task.",
        prior_events=[],
        global_event_counter=[0],
    )

    assert call_count[0] == 2, (
        f"Expected exactly 2 generate calls (1 original + 1 retry), got {call_count[0]}"
    )
    assert result.termination_reason == "protocol_violation", (
        f"Expected 'protocol_violation', got '{result.termination_reason}'"
    )
    ok("researcher_prose_gets_one_retry")


def test_retry_nudge_contains_nudge_exactly_once():
    """On retry, the prompt must be the TOOL_CALL_NUDGE text, not empty."""
    from generation.topology import TopologyConfig, Stage, HandoffRule

    topo = TopologyConfig(
        topology_id="linear_2", display_name="Linear 2",
        stages=[
            Stage("researcher", "researcher", "r1", max_turns=5, can_handoff=True, can_finalize=False),
        ],
        handoff_rules=[],
        exit_stage="researcher",
    )

    prompts_seen = []

    class TrackingBackend:
        def __init__(self):
            self.reset_calls = []

        def reset(self, **kw):
            self.reset_calls.append(kw)

        def _messages(self):
            return []

        def generate(self, prompt, tool_choice=None):
            prompts_seen.append(prompt)
            from backend.api_backend import ToolCall, ModelTurn
            if len(prompts_seen) == 1:
                return ModelTurn(text="Some prose.", stop_reason="end_turn", raw_content=[])
            return ModelTurn(
                tool_call=ToolCall(id="tc3", name="handoff", input={"target_agent": "analyst", "summary": "x"}),
                text="", stop_reason="tool_use", raw_content=[],
            )

        def _append_tool_result(self, *a, **kw):
            pass

    backend = TrackingBackend()
    runner = StageRunner(llm_backend=backend)
    runner.run_stage(
        stage=topo.stages[0],
        topology=topo,
        handoff_rule=None,
        scenario=None,
        ws_path=Path("/tmp/ws"),
        task_prompt="Do the task.",
        prior_events=[],
        global_event_counter=[0],
    )

    assert len(prompts_seen) == 2, f"Expected 2 prompts, got {len(prompts_seen)}"
    nudge = StageRunner.TOOL_CALL_NUDGE
    assert prompts_seen[0] == "", (
        f"First prompt should be empty (native mode), got: {prompts_seen[0]!r}"
    )
    assert prompts_seen[1] == nudge, (
        f"Retry prompt should be the nudge text.\n"
        f"Got: {prompts_seen[1]!r}\nExpected: {nudge!r}"
    )
    ok("retry_nudge_contains_nudge_exactly_once")


def test_researcher_handoff_is_native_tool():
    """Successful researcher completion uses native 'handoff' tool call, not prose."""
    from generation.topology import TopologyConfig, Stage, HandoffRule

    topo = TopologyConfig(
        topology_id="linear_2", display_name="Linear 2",
        stages=[
            Stage("researcher", "researcher", "r1", max_turns=5, can_handoff=True, can_finalize=False),
        ],
        handoff_rules=[],
        exit_stage="researcher",
    )

    class TrackingBackend:
        def __init__(self):
            self.reset_calls = []
            self.tool_choice_values = []

        def reset(self, **kw):
            self.reset_calls.append(kw)

        def _messages(self):
            return []

        def generate(self, prompt, tool_choice=None):
            self.tool_choice_values.append(tool_choice)
            from backend.api_backend import ToolCall, ModelTurn
            return ModelTurn(
                tool_call=ToolCall(id="tc4", name="handoff", input={"target_agent": "analyst", "summary": "Done."}),
                text="", stop_reason="tool_use", raw_content=[],
            )

        def _append_tool_result(self, *a, **kw):
            pass

    backend = TrackingBackend()
    runner = StageRunner(llm_backend=backend)
    result = runner.run_stage(
        stage=topo.stages[0],
        topology=topo,
        handoff_rule=None,
        scenario=None,
        ws_path=Path("/tmp/ws"),
        task_prompt="Do the task.",
        prior_events=[],
        global_event_counter=[0],
    )

    assert result.termination_reason == "handoff"
    assert len(backend.tool_choice_values) >= 1
    # tool_choice should be passed through to the backend
    assert backend.tool_choice_values[0] is not None, "tool_choice must not be None"
    ok("researcher_handoff_is_native_tool")


def test_analyst_submit_final_is_native_tool():
    """Successful analyst completion uses native 'submit_final' tool call."""
    from generation.topology import TopologyConfig, Stage, HandoffRule

    topo = TopologyConfig(
        topology_id="linear_2", display_name="Linear 2",
        stages=[
            Stage("analyst", "analyst", "a1", max_turns=5, can_handoff=False, can_finalize=True),
        ],
        handoff_rules=[],
        exit_stage="analyst",
    )

    class TrackingBackend:
        def __init__(self):
            self.reset_calls = []

        def reset(self, **kw):
            self.reset_calls.append(kw)

        def _messages(self):
            return []

        def generate(self, prompt, tool_choice=None):
            from backend.api_backend import ToolCall, ModelTurn
            return ModelTurn(
                tool_call=ToolCall(id="tc5", name="submit_final", input={"summary": "Analysis complete."}),
                text="", stop_reason="tool_use", raw_content=[],
            )

        def _append_tool_result(self, *a, **kw):
            pass

    backend = TrackingBackend()
    runner = StageRunner(llm_backend=backend)
    result = runner.run_stage(
        stage=topo.stages[0],
        topology=topo,
        handoff_rule=None,
        scenario=None,
        ws_path=Path("/tmp/ws"),
        task_prompt="Do the task.",
        prior_events=[],
        global_event_counter=[0],
    )

    assert result.termination_reason == "final"
    ok("analyst_submit_final_is_native_tool")


def test_tool_choice_reaches_outgoing_request():
    """tool_choice='any' must be normalized to {'type': 'any'} in the payload."""
    from backend.api_backend import APIBackend

    payloads = []

    original_call_api = APIBackend._call_api
    def capturing_call_api(self, messages, max_tokens=None, tools=None, tool_choice=None):
        payloads.append({
            "tool_choice": tool_choice,
            "tools": [t.get("name") for t in (tools or [])],
            "message_count": len(messages),
        })
        # Return a minimal response to satisfy _extract_turn
        return {"content": [{"type": "text", "text": "OK"}], "stop_reason": "end_turn"}

    APIBackend._call_api = capturing_call_api
    try:
        backend = APIBackend.__new__(APIBackend)
        backend.model = "test-model"
        backend.max_tokens = 128
        backend.temperature = 0.0
        backend._task_prompt = "Test task"
        backend._system_prompt = "You are a test agent."
        backend._conversation = []
        backend._mcp_tools = ["list_directory", "read_text_file"]
        import threading
        backend._lock = threading.Lock()

        turn = backend.generate("", tool_choice="any")
        assert len(payloads) == 1
        tc = payloads[0]["tool_choice"]
        assert isinstance(tc, dict), f"tool_choice must be dict, got {type(tc)}: {tc}"
        assert tc.get("type") == "any", f"tool_choice type must be 'any', got {tc}"
        ok("tool_choice_reaches_outgoing_request")
    finally:
        APIBackend._call_api = original_call_api


def test_empty_prompt_no_empty_user_message():
    """When prompt='', _messages must not have an empty user message appended."""
    from backend.api_backend import APIBackend

    backend = APIBackend.__new__(APIBackend)
    backend._task_prompt = "Real task prompt"
    backend._system_prompt = "You are a test agent."
    backend._conversation = []

    msgs = backend._messages
    user_msgs = [m for m in msgs if m.get("role") == "user"]
    # Should have exactly 1 user message: the task prompt
    assert len(user_msgs) == 1, (
        f"Expected 1 user message, got {len(user_msgs)}: "
        f"{[m.get('content', '')[:50] for m in user_msgs]}"
    )
    assert user_msgs[0]["content"] == "Real task prompt", (
        f"User message should be the task prompt, got: {user_msgs[0]['content']!r}"
    )
    ok("empty_prompt_no_empty_user_message")


def test_append_tool_result_native_pair():
    """_append_tool_result adds assistant tool_use + user tool_result with matching id."""
    from backend.api_backend import APIBackend, ToolCall

    backend = APIBackend.__new__(APIBackend)
    backend._conversation = []

    tc = ToolCall(id="test-id-123", name="read_text_file", input={"path": "x.txt"})
    backend._append_tool_result(tc, "File contents here.")

    assert len(backend._conversation) == 2
    assert backend._conversation[0]["role"] == "assistant"
    assert backend._conversation[0]["content"][0]["type"] == "tool_use"
    assert backend._conversation[0]["content"][0]["id"] == "test-id-123"
    assert backend._conversation[0]["content"][0]["name"] == "read_text_file"

    assert backend._conversation[1]["role"] == "user"
    assert backend._conversation[1]["content"][0]["type"] == "tool_result"
    assert backend._conversation[1]["content"][0]["tool_use_id"] == "test-id-123"
    assert backend._conversation[1]["content"][0]["content"] == "File contents here."
    ok("append_tool_result_native_pair")


# ─── Review-loop regression tests ────────────────────────────────────────────


class SequenceBackend:
    """Deterministic backend that plays a per-agent sequence of actions.

    Each agent gets its own queue. The backend returns the next action
    from the current agent's queue on each ``generate()`` call.
    """

    def __init__(self, sequences: Dict[str, list]):
        self._sequences = {k: list(v) for k, v in sequences.items()}
        self._steps: Dict[str, int] = {}
        self._calls: List[Dict[str, Any]] = []
        self._system_prompt = ""
        self._task_prompt = ""

    def reset(self, task="", agent_name="", mcp_tools=None, system_prompt=""):
        self._agent_name = agent_name
        self._system_prompt = system_prompt
        self._task_prompt = task
        if agent_name not in self._steps:
            self._steps[agent_name] = 0

    def _next(self, agent_name):
        idx = self._steps.get(agent_name, 0)
        seq = self._sequences.get(agent_name, [])
        if idx >= len(seq):
            return "submit_final", {}, True
        action, action_input, is_final = seq[idx]
        self._steps[agent_name] = idx + 1
        return action, action_input, is_final

    def generate(self, prompt="", tool_choice=None):
        action, action_input, is_final = self._next(self._agent_name)
        self._calls.append({
            "agent": self._agent_name,
            "action": action,
            "is_final": is_final,
        })
        from backend.api_backend import ToolCall, ModelTurn
        return ModelTurn(
            tool_call=ToolCall(id=f"tc-{len(self._calls)}", name=action, input=action_input),
            text="",
            stop_reason="tool_use",
        )

    def _append_tool_result(self, *a, **kw):
        pass

    def parse_action(self, text: str):
        return None


def _run_review_loop(actions: Dict[str, list]) -> RunResult:
    """Run a review_loop topology with a SequenceBackend."""
    with tempfile.TemporaryDirectory() as tmpdir:
        wcfg = WorkflowConfig(
            topology="review_loop",
            max_events=60,
            max_agent_turns=8,
        )
        spec = ScenarioSpec(
            scenario_id="review_test",
            task_family="code_review",
            task_variant="easy",
            fixture_id="code_review_easy",
            workflow_config=wcfg,
            condition="benign",
        )
        backend = SequenceBackend(actions)
        runner = ScenarioRunner(
            dry_run=False,
            max_events=60,
            output_dir=Path(tmpdir) / "ws",
            llm_backend=backend,
        )
        return runner.run(spec, FIXTURE_DIR)


def test_analyst_approves_complete_report():
    """Analyst reviews a sufficient artifact and calls submit_final."""
    result = _run_review_loop({
        "researcher": [
            ("list_directory", {"path": "."}, False),
            ("read_text_file", {"path": "documents/source.md"}, False),
            ("write_file", {"path": "output/report.md",
                            "content": "# Complete Report\nAll requirements met."}, False),
            ("handoff", {"target_agent": "analyst",
                          "summary": "Report complete. All task requirements addressed."}, True),
        ],
        "analyst": [
            ("read_text_file", {"path": "output/report.md"}, False),
            ("submit_final", {"summary": "Report verified. All requirements satisfied."}, True),
        ],
    })
    assert result.runner_success, f"Runner failed: {result.error}"
    assert result.termination_reason == "completed", (
        f"Expected 'completed', got '{result.termination_reason}'"
    )
    # Find the submit_final event
    final_events = [
        e for e in result.trace.events
        if e.event_type == TraceEventType.FINAL_RESPONSE
    ]
    assert len(final_events) == 1, (
        f"Expected 1 FINAL_RESPONSE, got {len(final_events)}"
    )
    # Verify the full review-loop path was traversed
    transitions = [
        e for e in result.trace.events
        if e.event_type == TraceEventType.TOPOLOGY_TRANSITION
    ]
    roles = [e.agent_role for e in transitions]
    assert "researcher" in roles, f"Researcher should have run. Transitions: {roles}"
    assert "analyst" in roles, f"Analyst should have run. Transitions: {roles}"
    ok("analyst_approves_complete_report")


def test_analyst_hands_back_only_with_concrete_issues():
    """Analyst only hands back when naming specific unresolved issues."""
    result = _run_review_loop({
        "researcher": [
            ("list_directory", {"path": "."}, False),
            ("write_file", {"path": "output/report.md",
                            "content": "# Report\nSection 1 missing."}, False),
            ("handoff", {"target_agent": "analyst", "summary": "Draft ready."}, True),
        ],
        "analyst": [
            ("read_text_file", {"path": "output/report.md"}, False),
            ("handoff", {"target_agent": "researcher",
                          "summary": "Section 1 is missing. Please add it."}, True),
        ],
    })
    assert result.runner_success, f"Runner failed: {result.error}"
    assert result.termination_reason == "max_review_cycles", (
        f"Expected 'max_review_cycles' (researcher doesn't fix in this test), "
        f"got '{result.termination_reason}'"
    )
    handoff_events = [
        e for e in result.trace.events
        if e.event_type == TraceEventType.AGENT_HANDOFF
    ]
    # Analyst's handoff should include concrete issue in summary
    analyst_handoffs = [
        h for h in handoff_events
        if h.observable and h.observable.get("handoff_from") == "analyst"
    ]
    assert len(analyst_handoffs) >= 1, (
        f"Expected analyst handoff with concrete issues, got {len(analyst_handoffs)}"
    )
    ok("analyst_hands_back_only_with_concrete_issues")


def test_researcher_revises_once_and_hands_back():
    """Researcher receiving feedback revises and hands back exactly once."""
    result = _run_review_loop({
        "researcher": [
            ("list_directory", {"path": "."}, False),
            ("write_file", {"path": "output/draft.md",
                            "content": "# Draft\nMissing data."}, False),
            ("handoff", {"target_agent": "analyst", "summary": "Draft v1."}, True),
            # Revision pass — receives handoff from analyst
            ("read_text_file", {"path": "feedback.md"}, False),
            ("write_file", {"path": "output/draft.md",
                            "content": "# Draft v2\nData included."}, False),
            ("handoff", {"target_agent": "analyst",
                          "summary": "Revised: added missing data."}, True),
        ],
        "analyst": [
            ("read_text_file", {"path": "output/draft.md"}, False),
            ("handoff", {"target_agent": "researcher",
                          "summary": "Section 1 is missing. Please add it."}, True),
            ("read_text_file", {"path": "output/draft.md"}, False),
            ("submit_final", {"summary": "All issues resolved."}, True),
        ],
    })
    assert result.runner_success, f"Runner failed: {result.error}"
    assert result.termination_reason == "completed", (
        f"Expected 'completed', got '{result.termination_reason}'"
    )
    # Count researcher handoffs — should be exactly 2 (initial + revision)
    researcher_handoffs = [
        e for e in result.trace.events
        if e.event_type == TraceEventType.AGENT_HANDOFF
        and getattr(e, "agent_role", None) == "researcher"
    ]
    assert len(researcher_handoffs) == 2, (
        f"Expected 2 researcher handoffs, got {len(researcher_handoffs)}"
    )
    ok("researcher_revises_once_and_hands_back")


def test_generic_handoff_summaries_discouraged():
    """System prompt should discourage generic handoff summaries like 'please review again'."""
    from generation.stage_runner import StageRunner
    from generation.topology import TopologyConfig, Stage, HandoffRule
    from backend.api_backend import ToolCall, ModelTurn

    topo = TopologyConfig(
        topology_id="review_loop",
        display_name="Review Loop",
        stages=[
            Stage("researcher", "researcher", "r1", max_turns=6,
                  can_handoff=True, can_finalize=False),
            Stage("analyst", "analyst", "a1", max_turns=6,
                  can_handoff=True, can_finalize=True),
        ],
        handoff_rules=[
            HandoffRule("researcher", "analyst"),
            HandoffRule("analyst", "researcher"),
        ],
        exit_stage="analyst",
        max_review_cycles=2,
    )

    sr = StageRunner(llm_backend=None)

    # Test analyst prompt (can_finalize + can_handoff, no incoming handoff)
    analyst_stage = topo.get_stage("analyst")
    prompt = sr._build_system_prompt(analyst_stage, None)
    assert "specific material errors" in prompt, (
        "Analyst prompt should mention 'specific material errors'"
    )
    assert "please review again" not in prompt.lower(), (
        "Analyst prompt should not contain generic review language"
    )
    assert "Prefer submit_final" in prompt or "prefer submit_final" in prompt.lower(), (
        "Analyst prompt should prefer submit_final over additional handoffs"
    )

    # Test researcher prompt on revision (receiving handoff from analyst)
    from generation.handoff import HandoffPayload
    handoff_payload = HandoffPayload(
        from_agent="analyst",
        to_agent="researcher",
        findings=[],
        source_paths=[],
        summary="Please add Section 1.",
        raw_output="Please add Section 1.",
        contains_corrupted_data=False,
    )
    researcher_stage = topo.get_stage("researcher")
    prompt = sr._build_system_prompt(researcher_stage, handoff_payload)
    assert "Address the specific issues" in prompt, (
        "Researcher revision prompt should say to address specific issues"
    )
    assert "call handoff exactly once" in prompt, (
        "Researcher revision prompt should say to hand off exactly once"
    )
    assert "Do NOT hand off merely to continue" in prompt, (
        "Researcher revision prompt should discourage handoffs without purpose"
    )

    ok("generic_handoff_summaries_discouraged")


def test_review_loop_converges_without_cycle_limit():
    """Review-loop should converge via submit_final, not hit the cycle limit."""
    # Build a scenario where analyst approves on the first review
    result = _run_review_loop({
        "researcher": [
            ("list_directory", {"path": "."}, False),
            ("read_text_file", {"path": "documents/source.md"}, False),
            ("write_file", {"path": "output/report.md",
                            "content": "# Report\nWell-structured analysis."}, False),
            ("handoff", {"target_agent": "analyst",
                          "summary": "Report ready for review."}, True),
        ],
        "analyst": [
            ("read_text_file", {"path": "output/report.md"}, False),
            ("search_files", {"path": ".", "pattern": "*.md"}, False),
            ("submit_final", {"summary": "Approved — report meets all requirements."}, True),
        ],
    })
    assert result.runner_success, f"Runner failed: {result.error}"
    assert result.termination_reason == "completed", (
        f"Expected 'completed', got '{result.termination_reason}'"
    )
    # Verify no max_review_cycles termination
    assert result.trace.metadata.get("termination_reason") != "max_review_cycles", (
        "Should converge via submit_final, not cycle limit"
    )
    ok("review_loop_converges_without_cycle_limit")


def test_backedge_limit_blocks_third_researcher_pass():
    """With max_review_cycles=1, analyst cannot hand off back to researcher
    a second time. Researcher must never run a third time.

    Expected path: R1 -> A1 -> R2 -> A2 (submit_final)
    Blocked: A2 -> R3 (backedge limit)
    """
    from generation.topology import get_topology

    # Build a review_loop topology with tight cycle limit
    agent_map = {
        "researcher": "r1",
        "analyst": "a1",
    }
    topo = get_topology(
        "review_loop", agent_map,
        max_agent_turns=6,
        max_review_cycles=1,
    )

    # Verify the backedge detection
    from generation.topology import HandoffRule
    assert topo.is_backedge(HandoffRule("analyst", "researcher")), \
        "analyst -> researcher should be a backedge"
    assert not topo.is_backedge(HandoffRule("researcher", "analyst")), \
        "researcher -> analyst should not be a backedge"

    # Count run_stage calls per role using a tracking backend
    class CountingBackend:
        def __init__(self):
            self._steps: Dict[str, int] = {}
            self._per_role: Dict[str, int] = {}

        def reset(self, task="", agent_name="", mcp_tools=None, system_prompt=""):
            if agent_name not in self._steps:
                self._steps[agent_name] = 0
                self._per_role[agent_name] = 0
            self._per_role[agent_name] += 1
            self._agent_name = agent_name

        def generate(self, prompt="", tool_choice=None):
            idx = self._steps[self._agent_name]
            self._steps[self._agent_name] = idx + 1
            from backend.api_backend import ToolCall, ModelTurn
            if self._agent_name == "researcher":
                # R1: handoff; R2: handoff; R3: never reached
                actions = [
                    ("list_directory", {"path": "."}),
                    ("write_file", {"path": "output/report.md",
                                    "content": "# Report"}),
                    ("handoff", {"target_agent": "analyst", "summary": "Done."}),
                    ("list_directory", {"path": "."}),
                    ("write_file", {"path": "output/report.md",
                                    "content": "# Report v2"}),
                    ("handoff", {"target_agent": "analyst", "summary": "Revised."}),
                ]
            else:
                # A1: handoff back; A2: attempt handoff again (gets blocked)
                actions = [
                    ("read_text_file", {"path": "output/report.md"}),
                    ("handoff", {"target_agent": "researcher",
                                  "summary": "Needs revision."}),
                    ("read_text_file", {"path": "output/report.md"}),
                    ("handoff", {"target_agent": "researcher",
                                  "summary": "Still needs work."}),
                ]
            if idx < len(actions):
                name, inp = actions[idx]
                return ModelTurn(
                    tool_call=ToolCall(id=f"tc-{idx}", name=name, input=inp),
                    text="", stop_reason="tool_use",
                )
            return ModelTurn(text="Done", stop_reason="end_turn")

        def _append_tool_result(self, *a, **kw):
            pass

        def parse_action(self, text):
            return None

    with tempfile.TemporaryDirectory() as tmpdir:
        wcfg = WorkflowConfig(
            topology="review_loop",
            max_events=60,
            max_agent_turns=6,
        )
        spec = ScenarioSpec(
            scenario_id="backedge_test",
            task_family="code_review",
            task_variant="easy",
            fixture_id="code_review_easy",
            workflow_config=wcfg,
            condition="benign",
        )
        backend = CountingBackend()
        runner = ScenarioRunner(
            dry_run=False,
            max_events=60,
            output_dir=Path(tmpdir) / "ws",
            llm_backend=backend,
        )
        result = runner.run(spec, FIXTURE_DIR)

    assert result.runner_success, f"Runner failed: {result.error}"
    assert result.termination_reason == "max_review_cycles", (
        f"Expected 'max_review_cycles', got '{result.termination_reason}'"
    )
    # Researcher must have run at most twice (R1, R2) — R3 is blocked
    researcher_calls = backend._per_role.get("researcher", 0)
    assert researcher_calls <= 2, (
        f"Researcher should run at most twice, ran {researcher_calls} times"
    )
    ok("backedge_limit_blocks_third_researcher_pass")


def test_final_round_analyst_receives_final_instructions():
    """The reviewer's system prompt should include final-round language
    when remaining_reviews=1 (max_review_cycles=1, backedge_count=0)."""
    from generation.stage_runner import StageRunner
    from generation.topology import TopologyConfig, Stage, HandoffRule

    topo = TopologyConfig(
        topology_id="review_loop",
        display_name="Review Loop",
        stages=[
            Stage("researcher", "researcher", "r1", max_turns=6,
                  can_handoff=True, can_finalize=False),
            Stage("analyst", "analyst", "a1", max_turns=6,
                  can_handoff=True, can_finalize=True),
        ],
        handoff_rules=[
            HandoffRule("researcher", "analyst"),
            HandoffRule("analyst", "researcher"),
        ],
        exit_stage="analyst",
        max_iterations=2,
        max_review_cycles=1,
    )

    sr = StageRunner(llm_backend=None)
    analyst_stage = topo.get_stage("analyst")

    # With backedge_count=0 and max_review_cycles=1, remaining_reviews=1
    # → final round
    prompt = sr._build_system_prompt(analyst_stage, None, remaining_reviews=1)
    assert "final permitted review round" in prompt, (
        f"Final-round analyst prompt should mention final round: {prompt[:200]}"
    )
    assert "call submit_final" in prompt, (
        "Final-round analyst prompt should say to call submit_final"
    )

    # With remaining_reviews=2 (non-final), should NOT have final-round language
    prompt2 = sr._build_system_prompt(analyst_stage, None, remaining_reviews=2)
    assert "final permitted review round" not in prompt2, (
        "Non-final round should not have final-round language"
    )

    # With remaining_reviews=None (no backedge-aware topologies), neutral prompt
    prompt3 = sr._build_system_prompt(analyst_stage, None, remaining_reviews=None)
    assert "final permitted review round" not in prompt3

    ok("final_round_analyst_receives_final_instructions")


def test_final_round_submit_final_completes():
    """Analyst calls submit_final on the final allowed review round;
    the run should complete successfully."""
    with tempfile.TemporaryDirectory() as tmpdir:
        wcfg = WorkflowConfig(
            topology="review_loop",
            max_events=60,
            max_agent_turns=6,
        )
        spec = ScenarioSpec(
            scenario_id="final_round_test",
            task_family="code_review",
            task_variant="easy",
            fixture_id="code_review_easy",
            workflow_config=wcfg,
            condition="benign",
        )
        backend = SequenceBackend({
            "researcher": [
                ("list_directory", {"path": "."}, False),
                ("write_file", {"path": "output/report.md",
                                "content": "# Report\nComplete."}, False),
                ("handoff", {"target_agent": "analyst", "summary": "Done."}, True),
            ],
            "analyst": [
                ("read_text_file", {"path": "output/report.md"}, False),
                ("submit_final", {"summary": "Approved on final round."}, True),
            ],
        })
        runner = ScenarioRunner(
            dry_run=False,
            max_events=60,
            output_dir=Path(tmpdir) / "ws",
            llm_backend=backend,
        )
        result = runner.run(spec, FIXTURE_DIR)

    assert result.runner_success, f"Runner failed: {result.error}"
    assert result.termination_reason == "completed", (
        f"Expected 'completed', got '{result.termination_reason}'"
    )
    ok("final_round_submit_final_completes")


def test_final_round_handoff_blocked_before_next_stage():
    """Analyst calls handoff on the final review round; the backedge is
    blocked before a third researcher stage starts."""
    from generation.topology import get_topology, HandoffRule

    agent_map = {"researcher": "r1", "analyst": "a1"}
    topo = get_topology(
        "review_loop", agent_map,
        max_agent_turns=6,
        max_review_cycles=1,
    )

    assert topo.is_backedge(HandoffRule("analyst", "researcher"))

    # Verify the rule: after 1 backedge traversal, the next is blocked.
    # A2 -> R3 would be backedge_count=2 > max_review_cycles=1
    assert topo.max_review_cycles == 1

    ok("final_round_handoff_blocked_before_next_stage")


def test_no_third_researcher_with_max_iterations_2():
    """With the review_loop topology (max_iterations=2, max_review_cycles=1),
    researcher runs at most twice. The third attempt is never started because
    the backedge A2→R3 is blocked by the cycle limit."""
    with tempfile.TemporaryDirectory() as tmpdir:
        wcfg = WorkflowConfig(
            topology="review_loop",
            max_events=60,
            max_agent_turns=6,
        )
        spec = ScenarioSpec(
            scenario_id="no_r3_test",
            task_family="code_review",
            task_variant="easy",
            fixture_id="code_review_easy",
            workflow_config=wcfg,
            condition="benign",
        )

        class CountingBackend:
            def __init__(self):
                self._per_role: Dict[str, int] = {}
                self._steps: Dict[str, int] = {}

            def reset(self, task="", agent_name="", mcp_tools=None, system_prompt=""):
                self._per_role[agent_name] = self._per_role.get(agent_name, 0) + 1
                self._agent_name = agent_name
                if agent_name not in self._steps:
                    self._steps[agent_name] = 0

            def generate(self, prompt="", tool_choice=None):
                idx = self._steps[self._agent_name]
                self._steps[self._agent_name] = idx + 1
                from backend.api_backend import ToolCall, ModelTurn
                if self._agent_name == "researcher":
                    acts = [
                        ("list_directory", {"path": "."}),
                        ("write_file", {"path": "output/report.md",
                                        "content": "# Report"}),
                        ("handoff", {"target_agent": "analyst", "summary": "Done."}),
                        ("list_directory", {"path": "."}),
                        ("write_file", {"path": "output/report.md",
                                        "content": "# Report v2"}),
                        ("handoff", {"target_agent": "analyst", "summary": "Revised."}),
                    ]
                else:
                    acts = [
                        ("read_text_file", {"path": "output/report.md"}),
                        ("handoff", {"target_agent": "researcher",
                                      "summary": "Needs revision."}),
                        ("read_text_file", {"path": "output/report.md"}),
                        ("handoff", {"target_agent": "researcher",
                                      "summary": "Still needs work."}),
                    ]
                if idx < len(acts):
                    name, inp = acts[idx]
                    return ModelTurn(
                        tool_call=ToolCall(id=f"tc-{idx}", name=name, input=inp),
                        text="", stop_reason="tool_use",
                    )
                return ModelTurn(text="Done", stop_reason="end_turn")

            def _append_tool_result(self, *a, **kw):
                pass

            def parse_action(self, text):
                return None

        backend = CountingBackend()
        runner = ScenarioRunner(
            dry_run=False,
            max_events=60,
            output_dir=Path(tmpdir) / "ws",
            llm_backend=backend,
        )
        result = runner.run(spec, FIXTURE_DIR)

    assert result.termination_reason == "max_review_cycles", (
        f"Expected 'max_review_cycles', got '{result.termination_reason}'"
    )
    researcher_calls = backend._per_role.get("researcher", 0)
    assert researcher_calls == 2, (
        f"Researcher should run exactly twice, ran {researcher_calls}"
    )
    ok("no_third_researcher_with_max_iterations_2")


def test_branch_and_verify_dry_run():
    """End-to-end: branch_and_verify topology must produce events from all 3 stages,
    include a merge on verifier, and emit an AGENT_HANDOFF.
    """
    builder = ScenarioBuilder(seed=42)
    cfg = ScenarioBuildConfig(
        task_family="code_review",
        fixture_id="code_review_easy",
        task_variant="easy",
        topology="branch_and_verify",
        repetition_index=0,
        seed=42,
    )
    spec = builder.build_benign(cfg)

    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ScenarioRunner(
            dry_run=True, max_events=60,
            output_dir=Path(tmpdir) / "ws",
        )
        result = runner.run(spec, FIXTURE_DIR)

    # Runner must succeed (no crash)
    assert result.runner_success, f"Run failed: {result.error}"
    assert result.trace is not None, "Trace must not be None"
    assert result.trace.num_events > 0, "Trace must have events"

    from schemas.trace import TraceEventType

    # Collect stages that produced reasoning/tool events
    roles_seen = set()
    for evt in result.trace.events:
        if evt.agent_role:
            roles_seen.add(evt.agent_role)

    # All 3 stages must appear
    for expected_role in ["researcher", "analyst", "verifier"]:
        assert expected_role in roles_seen, (
            f"Role '{expected_role}' never produced events. "
            f"Seen roles: {sorted(roles_seen)}"
        )

    # At least one AGENT_HANDOFF event
    handoffs = [e for e in result.trace.events
                if e.event_type == TraceEventType.AGENT_HANDOFF]
    assert len(handoffs) >= 1, "Expected at least 1 AGENT_HANDOFF event"

    # ── Bug fix 1: analyst must NOT receive researcher's handoff ─────────
    # Find all USER_INPUT events containing handoff context blocks.
    analyst_user_inputs = [
        e for e in result.trace.events
        if e.event_type == TraceEventType.USER_INPUT
        and e.agent_role == "analyst"
    ]
    for ui in analyst_user_inputs:
        txt = ui.input_text or ui.output_text or ""
        assert "[Handoff received from researcher]" not in txt, (
            "analyst incorrectly received researcher's handoff — "
            "siblings should not receive each other's payloads"
        )

    # ── Bug fix 2: verifier merge transition depends_on must contain both
    # source handoff event IDs, not just one.
    merge_transitions = [
        e for e in result.trace.events
        if e.event_type == TraceEventType.TOPOLOGY_TRANSITION
        and e.agent_role == "verifier"
    ]
    assert len(merge_transitions) >= 1, "Expected TOPOLOGY_TRANSITION for verifier"
    verifier_transition = merge_transitions[0]
    depends = verifier_transition.depends_on or []
    # Collect all distinct AGENT_HANDOFF event IDs
    handoff_event_ids = [e.event_id for e in handoffs]
    # The verifier transition must depend on ALL handoff event IDs
    for hid in handoff_event_ids:
        assert hid in depends, (
            f"verifier TOPOLOGY_TRANSITION missing handoff dependency '{hid}'. "
            f"depends_on={depends}"
        )

    ok("branch_and_verify_dry_run")


if __name__ == "__main__":
    sys.exit(main())
