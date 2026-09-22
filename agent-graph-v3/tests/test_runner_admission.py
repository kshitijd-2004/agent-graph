"""Execution-mode handoff contracts and completed-trace dataset admission."""
from pathlib import Path

import pytest

from backend.api_backend import ModelTurn, ToolCall
from benchmark.benchmark_runner import BenchmarkManifest, BenchmarkRunner
from benchmark.propagation_analysis import PropagationAnalyzer
from generation.runner import ScenarioRunner
from schemas import LEPConfig, ScenarioSpec, Trace, TraceEvent, TraceEventType, TraceVariant, WorkflowConfig

FIXTURES = Path(__file__).resolve().parent.parent / "workspace_fixtures"


class CoordinationBackend:
    """Follow exposed tools, attempting a writer handoff before any memory write."""

    def __init__(self):
        self.stages = []

    def reset(self, *, agent_name, mcp_tools, **kwargs):
        self.role = agent_name
        self.tools = list(mcp_tools)
        self.step = 0
        self.stages.append((agent_name, self.tools))

    def _append_tool_result(self, *args, **kwargs):
        pass

    def generate(self, prompt="", tool_choice=None):
        self.step += 1
        if self.role in ("inspector", "extractor"):
            if "write_memory" in self.tools and self.step == 2:
                name, args = "write_memory", {"key": "analysis_notes", "value": "Q3 revenue: $1,520,000"}
            else:
                name, args = "handoff", {"summary": "Findings ready for independent review."}
        elif self.step == 1:
            name, args = (("read_memory", {"query": "analysis notes"}) if "read_memory" in self.tools
                          else ("list_directory", {"path": "."}))
        elif "submit_final" in self.tools:
            name, args = "submit_final", {"summary": "Review complete."}
        else:
            name, args = "handoff", {"summary": "Verify source figures before finalizing."}
        assert name in self.tools
        return ModelTurn(tool_call=ToolCall(id=f"call-{len(self.stages)}-{self.step}", name=name, input=args),
                         text="", stop_reason="tool_use")


def scenario(mode="ephemeral_private"):
    return ScenarioSpec(scenario_id="admission", task_family="code_review", task_variant="easy",
                        fixture_id="code_review_easy", condition="benign",
                        workflow_config=WorkflowConfig(topology="review_loop", memory_mode=mode,
                                                       max_agent_turns=6))


@pytest.mark.parametrize("mode", ["none", "ephemeral_private", "ephemeral_shared", "persistent_shared"])
def test_handoff_contract_and_downstream_completion(tmp_path, mode):
    backend = CoordinationBackend()
    result = ScenarioRunner(llm_backend=backend, dry_run=False, output_dir=tmp_path).run(scenario(mode), FIXTURES)
    assert result.runner_success, result.error
    assert result.termination_reason == "completed"
    assert result.dataset_eligible
    assert result.trace.metadata["dataset_eligible"] is True
    assert [role for role, _ in backend.stages] == ["inspector", "reviewer", "inspector", "reviewer"]
    assert sum(e.event_type == TraceEventType.AGENT_HANDOFF for e in result.trace.events) == 3
    assert result.trace.events[-1].event_type == TraceEventType.FINAL_RESPONSE
    repairs = [e for e in result.trace.events if e.observable.get("reason") == "missing_write_memory_before_handoff"]
    shared = mode in ("ephemeral_shared", "persistent_shared")
    for _, tools in backend.stages:
        assert ("write_memory" in tools) == shared
        assert ("read_memory" in tools) == shared
    if shared:
        assert len(repairs) == 2  # One blocked attempt on each writer pass.
        assert sum(e.event_type == TraceEventType.MEMORY_WRITE for e in result.trace.events) == 2
        assert sum(e.event_type == TraceEventType.MEMORY_RETRIEVAL for e in result.trace.events) == 2
    else:
        assert not repairs
        assert not any(e.event_type == TraceEventType.MEMORY_WRITE for e in result.trace.events)


def event(index, kind):
    return TraceEvent(trace_id="admission_a", event_id=str(index), event_index=index,
                      timestamp="2026-01-01T00:00:00Z", event_type=kind, agent_role="reviewer")


def execute_trace(tmp_path, monkeypatch, reason, events, spec=None):
    trace = Trace(trace_id="admission_a", execution_id="admission", variant=TraceVariant.BENIGN,
                  events=events, metadata={"termination_reason": reason})
    runner = ScenarioRunner(output_dir=tmp_path)
    monkeypatch.setattr(runner, "_execute_scenario", lambda *args: trace)
    return runner.run(spec or scenario(), FIXTURES)


@pytest.mark.parametrize("reason", ["max_review_cycles", "max_events_reached", "max_turns", "execution_loop",
                                    "protocol_violation", "premature_final", "invalid_handoff", "unknown", "handoff"])
@pytest.mark.parametrize("has_final", [False, True])
def test_abnormal_whole_run_is_ineligible_even_with_an_earlier_final(tmp_path, monkeypatch, reason, has_final):
    events = [event(0, TraceEventType.FINAL_RESPONSE)] if has_final else []
    result = execute_trace(tmp_path, monkeypatch, reason, events)
    assert result.runner_success
    assert result.termination_reason == reason
    assert not result.dataset_eligible
    assert not result.task_success
    assert result.trace.metadata["dataset_eligible"] is False


@pytest.mark.parametrize("kinds", [[], [TraceEventType.AGENT_HANDOFF],
    [TraceEventType.FINAL_RESPONSE, TraceEventType.TOOL_CALL],
    [TraceEventType.FINAL_RESPONSE, TraceEventType.FINAL_RESPONSE]])
def test_completed_metadata_cannot_admit_invalid_terminal_state(tmp_path, monkeypatch, kinds):
    result = execute_trace(tmp_path, monkeypatch, "completed", [event(i, kind) for i, kind in enumerate(kinds)])
    assert not result.dataset_eligible
    assert result.termination_reason == "invalid_terminal_state"


@pytest.mark.parametrize("injected", [False, True])
def test_completed_lep_admission_requires_injection_not_correct_answer(tmp_path, monkeypatch, injected):
    spec = scenario()
    spec.condition = "single_lep"
    spec.lep_configs = [LEPConfig(code="LEP_TOOL_RESULT_CORRUPTION", name="Tool corruption",
                                category="injection", description="Corrupt tool output", task_family="code_review")]
    origin = event(0, TraceEventType.TOOL_RESULT)
    origin.event_labels.is_injection_origin = injected
    final = event(1, TraceEventType.FINAL_RESPONSE)
    final.output_text = "No issues."  # Fails the code-review oracle; still an admissible completed LEP execution.
    result = execute_trace(tmp_path, monkeypatch, "completed", [origin, final], spec)
    assert result.dataset_eligible == injected
    assert not result.task_success


def benchmark_entry():
    # Exercise execution/admission independently of manifest plan construction.
    return {"scenario_id": "admission", "task_family": "code_review", "condition": "benign",
            "fixture_id": "code_review_easy", "topology": "review_loop", "execution_variant": "standard",
            "lep_codes": [], "propagation_mode": "single_origin", "repetition_index": 0}


def test_incomplete_benign_trace_is_audited_outside_clean_reference_candidates(tmp_path):
    manifest = BenchmarkManifest(topologies=["review_loop"], task_families=["code_review"],
                                 propagation_modes=["single_origin"], output_dir=tmp_path, fixture_root=FIXTURES)
    benchmark = BenchmarkRunner(manifest)
    entry = benchmark_entry()
    # The existing canned dry-run trajectory exhausts review cycles without a final.
    record = benchmark._execute(entry)
    assert record.success
    assert record.termination_reason == "max_review_cycles"
    assert not record.dataset_eligible
    assert Path(record.trace_path).parent == tmp_path / "rejected_traces"
    assert not list((tmp_path / "traces").glob("*_trace.json"))
    clean_refs, lep_traces = PropagationAnalyzer(tmp_path)._load_and_group_traces()
    assert clean_refs == {}
    assert lep_traces == {}
    assert record.to_dict()["dataset_eligible"] is False


def test_completed_benchmark_trace_is_a_clean_reference_candidate(tmp_path):
    manifest = BenchmarkManifest(topologies=["review_loop"], task_families=["code_review"],
                                 propagation_modes=["single_origin"], dry_run=False,
                                 output_dir=tmp_path, fixture_root=FIXTURES)
    benchmark = BenchmarkRunner(manifest, llm_backend=CoordinationBackend())
    record = benchmark._execute(benchmark_entry())
    assert record.success, record.error
    assert record.dataset_eligible
    assert Path(record.trace_path).parent == tmp_path / "traces"
    refs, _ = PropagationAnalyzer(tmp_path)._load_and_group_traces()
    assert sum(ref.num_runs for ref in refs.values()) == 1
