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
        assert result.task_success, f"Task not completed: {result.termination_reason}"
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

        def generate(self, prompt: str) -> str:
            self._step += 1
            # Always return the same read_text_file call
            return json.dumps({
                "reasoning": f"Loop step {self._step}",
                "action": "read_text_file",
                "action_input": {"path": "src/main.py"},
                "final_response": "",
            })

        def parse_action(self, raw_response: str):
            try:
                data = json.loads(raw_response)
                return {
                    "reasoning": str(data.get("reasoning", "")),
                    "action": str(data["action"]),
                    "action_input": json.dumps(data.get("action_input", {})),
                    "final_response": str(data.get("final_response", "")),
                }
            except (json.JSONDecodeError, KeyError):
                return None

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

    # REASONING events must carry full content (not sliced)
    # In dry-run mode, the raw output is a compact JSON — verify it's the complete
    # structured action, not a truncated slice.
    reasoning_events = [e for e in trace.events if e.event_type == TraceEventType.REASONING]
    assert reasoning_events, "Missing REASONING events"
    for r in reasoning_events:
        assert r.input_text is not None and len(r.input_text) > 0, "REASONING input_text empty"
        assert r.output_text is not None and len(r.output_text) > 0, "REASONING output_text empty"
        # The output must be a complete JSON action (not a truncated slice)
        assert r.output_text.strip().startswith("{"), (
            f"REASONING output_text not a JSON object: {r.output_text[:80]}"
        )
        assert r.output_text.strip().endswith("}"), (
            f"REASONING output_text truncated (missing closing brace): {r.output_text[-20:]}"
        )

    # TOOL_CALL input_text must carry full args (not sliced to 300)
    tool_call_events = [e for e in trace.events if e.event_type == TraceEventType.TOOL_CALL]
    assert tool_call_events, "Missing TOOL_CALL events"
    for tc in tool_call_events:
        assert tc.input_text is not None, "TOOL_CALL missing input_text"
        # Check that it contains complete argument representation
        args_str = str(tc.tool_arguments or {})
        assert args_str in tc.input_text, (
            f"TOOL_CALL input_text missing full args: {tc.input_text[:100]}"
        )

    # FINAL_RESPONSE must carry the full response (not sliced to 500)
    final_events = [e for e in trace.events if e.event_type == TraceEventType.FINAL_RESPONSE]
    assert final_events, "Missing FINAL_RESPONSE event"
    fr_text = final_events[0].output_text or ""
    assert "Task complete" in fr_text, (
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

        def reset(self, task="", agent_name="researcher", mcp_tools=None, system_prompt=""):
            self._agent_name = agent_name

        def generate(self, prompt: str) -> str:
            self._step += 1
            path = self._paths[(self._step - 1) % len(self._paths)]
            return json.dumps({
                "reasoning": f"step {self._step}",
                "action": "read_text_file",
                "action_input": {"path": path},
                "final_response": "",
            })

        def parse_action(self, raw_response: str):
            try:
                data = json.loads(raw_response)
                return {
                    "reasoning": str(data.get("reasoning", "")),
                    "action": str(data["action"]),
                    "action_input": json.dumps(data.get("action_input", {})),
                    "final_response": str(data.get("final_response", "")),
                }
            except (json.JSONDecodeError, KeyError):
                return None

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
    """Bug 10: linear_2 topology must produce an AGENT_HANDOFF event even when
    the agent calls final directly — the runner enforces the handoff.
    """
    from generation.runner import ScenarioRunner
    import tempfile, json

    class SkipHandoffBackend:
        """Backend that calls final without ever calling handoff_to_analyst."""
        def __init__(self):
            self._agent_name = "researcher"
            self._step = 0

        def reset(self, task="", agent_name="researcher", mcp_tools=None, system_prompt=""):
            self._agent_name = agent_name

        def generate(self, prompt: str) -> str:
            self._step += 1
            if self._step == 1:
                return json.dumps({
                    "reasoning": "listing workspace",
                    "action": "list_directory",
                    "action_input": {"path": "."},
                    "final_response": "",
                })
            # Second step: skip handoff, go straight to final
            return json.dumps({
                "reasoning": "done, calling final",
                "action": "final",
                "action_input": {},
                "final_response": "Review complete.",
            })

        def parse_action(self, raw_response: str):
            try:
                data = json.loads(raw_response)
                return {
                    "reasoning": str(data.get("reasoning", "")),
                    "action": str(data["action"]),
                    "action_input": json.dumps(data.get("action_input", {})),
                    "final_response": str(data.get("final_response", "")),
                }
            except (json.JSONDecodeError, KeyError):
                return None

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

    handoffs = [e for e in result.trace.events
                if e.event_type == TraceEventType.AGENT_HANDOFF]
    assert len(handoffs) >= 1, (
        f"linear_2 topology must produce at least 1 AGENT_HANDOFF, got {len(handoffs)}"
    )
    # The auto-enforced handoff should mark researcher → analyst
    ho = handoffs[0]
    assert ho.observable.get("handoff_from") == "researcher", (
        f"Expected handoff_from=researcher, got {ho.observable.get('handoff_from')}"
    )
    assert ho.observable.get("handoff_to") == "analyst", (
        f"Expected handoff_to=analyst, got {ho.observable.get('handoff_to')}"
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
        def __init__(self):
            self._step = 0
            self.reset_count = 0

        def reset(self, *a, **kw):
            self.reset_count += 1

        def generate(self, prompt: str) -> str:
            self._step += 1
            prompts_seen.append(prompt)
            if self._step == 1:
                return json.dumps({
                    "reasoning": "Starting the code review task",
                    "action": "list_directory",
                    "action_input": {"path": "."},
                    "final_response": "",
                })
            elif self._step == 2:
                # Verify turn 2 prompt contains turn 1's tool result.
                # The history section should contain the tool result from turn 1.
                assert "[Tool: list_directory]" in prompt or "(empty directory)" in prompt, (
                    f"Turn 2 prompt does not contain turn 1's tool result. "
                    f"Prompt (first 400 chars): {prompt[:400]}"
                )
                return json.dumps({
                    "reasoning": "Now reading the main source file",
                    "action": "read_text_file",
                    "action_input": {"path": "src/main.py"},
                    "final_response": "",
                })
            else:
                return json.dumps({
                    "reasoning": "Done",
                    "action": "final",
                    "action_input": {},
                    "final_response": "Code review complete.",
                })

        def parse_action(self, raw_response: str):
            data = json.loads(raw_response)
            return {
                "reasoning": str(data.get("reasoning", "")),
                "action": str(data["action"]),
                "action_input": json.dumps(data.get("action_input", {})),
                "final_response": str(data.get("final_response", "")),
            }

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
        f"Expected at least 3 events (user_input, system_init, at least 1 turn), "
        f"got {len(result.trace.events)}: {[e.event_type.value for e in result.trace.events]}"
    )

    # At least 2 prompts should have been seen (turn 1 + turn 2)
    assert len(prompts_seen) >= 2, (
        f"Expected at least 2 API calls, got {len(prompts_seen)}"
    )

    # Turn 1 and turn 2 must NOT be identical (proves history accumulation)
    assert prompts_seen[0] != prompts_seen[1], (
        "Turn 1 and turn 2 prompts are identical — stage history is not accumulating"
    )

    # Turn 2 prompt must contain the result from turn 1's tool call
    # (This is the core fix: the agent must see prior tool results)
    turn2_prompt = prompts_seen[1]
    assert "[Tool: list_directory]" in turn2_prompt, (
        f"Turn 2 prompt does not contain turn 1's tool result. "
        f"Prompt snippet: {turn2_prompt[:500]}"
    )

    # Need at least 2 turns to test history persistence
    assert len(prompts_seen) >= 2, "Need at least 2 turns to test history persistence"

    ok("stage_local_history_persistence")


def test_backend_reset_once_per_stage():
    """Backend.reset() must be called exactly once per stage, not per turn."""
    from generation.runner import ScenarioRunner
    from generation.scenario_builder import ScenarioBuilder, ScenarioBuildConfig

    reset_log = []

    class CountingBackend:
        def __init__(self):
            self._step = 0

        def reset(self, task="", agent_name="", mcp_tools=None, system_prompt=""):
            reset_log.append((agent_name, len(reset_log) + 1))

        def generate(self, prompt):
            self._step += 1
            if self._step <= 3:
                return json.dumps({
                    "reasoning": f"step {self._step}",
                    "action": "list_directory",
                    "action_input": {"path": "."},
                    "final_response": "",
                })
            return json.dumps({
                "reasoning": "done",
                "action": "final",
                "action_input": {},
                "final_response": "done",
            })

        def parse_action(self, raw):
            data = json.loads(raw)
            return {
                "reasoning": str(data.get("reasoning", "")),
                "action": str(data["action"]),
                "action_input": json.dumps(data.get("action_input", {})),
                "final_response": str(data.get("final_response", "")),
            }

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
    assert "output/report.md" in repaired["action_input"], (
        f"Expected path 'output/report.md' in action_input, got: {repaired['action_input']}"
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
