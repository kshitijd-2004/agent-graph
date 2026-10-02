"""At max_review_cycles the exit stage gets one finalize-only turn instead of the run ending
with no answer."""
from pathlib import Path

import pytest

from backend.api_backend import ModelTurn, ToolCall
from generation.runner import ScenarioRunner
from schemas import ScenarioSpec, TraceEventType, WorkflowConfig

FIXTURES = Path(__file__).resolve().parent.parent / "workspace_fixtures"


class StubbornBackend:
    """Never accepts the work: hands off whenever handoff is offered, finalizes only when it
    is the one tool left. With finalize=False it never finalizes at all."""

    def __init__(self, finalize=True):
        self.finalize = finalize
        self.stages = []

    def reset(self, *, agent_name, mcp_tools, **kwargs):
        self.role, self.tools, self.step = agent_name, list(mcp_tools), 0
        self.stages.append((agent_name, self.tools))

    def _append_tool_result(self, *args, **kwargs):
        pass

    def generate(self, prompt="", tool_choice=None):
        self.step += 1
        if self.step == 1:
            name, args = "list_directory", {"path": "."}
        elif "handoff" in self.tools:
            name, args = "handoff", {"summary": "Not good enough yet, revise it."}
        elif self.finalize and "submit_final" in self.tools:
            name, args = "submit_final", {"summary": "Final answer after the review limit."}
        else:
            name, args = "list_directory", {"path": "."}
        return ModelTurn(tool_call=ToolCall(id=f"c-{len(self.stages)}-{self.step}", name=name, input=args),
                         text="", stop_reason="tool_use")


def spec(topology):
    return ScenarioSpec(scenario_id=f"cap_{topology}", task_family="code_review", task_variant="easy",
                        fixture_id="code_review_easy", condition="benign",
                        workflow_config=WorkflowConfig(topology=topology, memory_mode="none", max_agent_turns=4))


@pytest.mark.parametrize("topology", ["review_loop", "coordinator_workers"])
def test_cap_gives_exit_stage_a_final_turn(tmp_path, topology):
    backend = StubbornBackend()
    result = ScenarioRunner(llm_backend=backend, dry_run=False, output_dir=tmp_path).run(spec(topology), FIXTURES)
    assert result.runner_success, result.error
    md = result.trace.metadata
    assert md["review_cap_forced_final"] is True
    assert result.termination_reason == "completed"
    assert result.trace.events[-1].event_type == TraceEventType.FINAL_RESPONSE
    assert any(e.event_type == TraceEventType.PROTOCOL_RECOVERY for e in result.trace.events)
    last_role, last_tools = backend.stages[-1]
    assert "handoff" not in last_tools and "submit_final" in last_tools
    exit_role = "reviewer" if topology == "review_loop" else "coordinator"
    assert last_role == exit_role


def test_cap_without_a_final_still_ends_as_max_review_cycles(tmp_path):
    backend = StubbornBackend(finalize=False)
    result = ScenarioRunner(llm_backend=backend, dry_run=False, output_dir=tmp_path).run(spec("review_loop"), FIXTURES)
    assert result.termination_reason == "max_review_cycles"
    assert not result.dataset_eligible
    assert result.trace.metadata.get("review_cap_forced_final_attempted") is True


class TwoReadBackend(StubbornBackend):
    """Reproduce the live inspector/reviewer trajectory using real stage events."""

    def generate(self, prompt="", tool_choice=None):
        if self.step < 2:
            self.step += 1
            return ModelTurn(tool_call=ToolCall(
                id=f"c-{len(self.stages)}-{self.step}", name="read_text_file",
                input={"path": ["django/forms/fields.py", "manifest.json"][self.step - 1]}),
                text="", stop_reason="tool_use")
        return super().generate(prompt, tool_choice)


def django_spec(injected=False):
    from benchmark.benchmark_runner import BenchmarkRunner

    scenario = spec("review_loop")
    scenario.fixture_id = "code_review_swe_003_django_django_11880"
    scenario.task_variant = "swe_bench_verified"
    if injected:
        scenario.condition = "single_lep"
        scenario.lep_configs = [BenchmarkRunner._resolve_lep(
            None, "LEP_INDIRECT_PROMPT_INJECTION", "code_review")]
    return scenario


@pytest.mark.parametrize("injected", [False, True])
@pytest.mark.parametrize("cycle_cap,max_events,accepted,reason", [
    (4, 300, 4, "max_review_cycles"),
    (1, 100, 1, "max_review_cycles"),
    (0, 50, 0, "max_review_cycles"),
    (4, 50, 0, "review_event_budget"),
])
def test_repeated_reads_refuse_backedge_and_finalize_once(
        tmp_path, monkeypatch, injected, cycle_cap, max_events, accepted, reason):
    import generation.topology as topologies

    original = topologies.get_topology

    def configured(*args, **kwargs):
        return original(*args, **kwargs, max_review_cycles=cycle_cap)

    monkeypatch.setattr(topologies, "get_topology", configured)
    backend = TwoReadBackend()
    result = ScenarioRunner(llm_backend=backend, dry_run=False,
                            max_events=max_events, output_dir=tmp_path).run(django_spec(injected), FIXTURES)
    assert result.runner_success, result.error
    assert result.termination_reason == "completed"
    assert result.dataset_eligible
    events = result.trace.events
    assert len(events) < max_events
    assert result.trace.injection_origin_count == int(injected)
    assert result.trace.metadata["review_cycle_count"] == accepted
    assert result.trace.metadata["review_finalization_reason"] == reason
    assert result.trace.metadata["review_cap_forced_final"] is True
    assert [role for role, _ in backend.stages] == ["inspector", "reviewer"] * (accepted + 1) + ["reviewer"]
    final_only = [tools for _, tools in backend.stages if "handoff" not in tools]
    assert len(final_only) == 1
    assert "submit_final" in final_only[0]
    cap_events = [e for e in events if e.event_type == TraceEventType.PROTOCOL_RECOVERY
                  and e.observable.get("reason") == reason]
    assert len(cap_events) == 1
    after_cap = events[events.index(cap_events[0]) + 1:]
    assert not any(e.event_type in (TraceEventType.AGENT_HANDOFF, TraceEventType.TOPOLOGY_TRANSITION)
                   for e in after_cap)
    finals = [e for e in events if e.event_type == TraceEventType.FINAL_RESPONSE]
    assert len(finals) == 1
    assert finals[0] is events[-1]
    assert finals[0].output_text == "Final answer after the review limit."
    assert not any(e.event_type == TraceEventType.PROTOCOL_VIOLATION for e in events)


def test_forced_final_cannot_handoff_even_if_backend_ignores_tools(tmp_path):
    class DisobedientBackend(TwoReadBackend):
        def generate(self, prompt="", tool_choice=None):
            if "handoff" not in self.tools:
                return ModelTurn(tool_call=ToolCall(id="illegal", name="handoff",
                    input={"summary": "Try to start another cycle"}), text="", stop_reason="tool_use")
            return super().generate(prompt, tool_choice)

    backend = DisobedientBackend()
    result = ScenarioRunner(llm_backend=backend, dry_run=False,
                            max_events=50, output_dir=tmp_path).run(django_spec(), FIXTURES)
    assert result.termination_reason == "max_review_cycles"
    assert not result.dataset_eligible
    assert [r for r, _ in backend.stages] == ["inspector", "reviewer", "reviewer"]
    cap_index = next(i for i, e in enumerate(result.trace.events)
                     if e.event_type == TraceEventType.PROTOCOL_RECOVERY)
    assert not any(e.event_type == TraceEventType.AGENT_HANDOFF
                   for e in result.trace.events[cap_index + 1:])


def test_live_one_read_then_two_read_trajectory(tmp_path):
    class LivePatternBackend(TwoReadBackend):
        def generate(self, prompt="", tool_choice=None):
            # The rejected trace's initial pair used one read per stage;
            # subsequent visits used two. Without admission control the third
            # reviewer returns at event count 56, before the cycle cap of four.
            if len(self.stages) <= 2 and self.step == 1:
                return StubbornBackend.generate(self, prompt, tool_choice)
            return super().generate(prompt, tool_choice)

    backend = LivePatternBackend()
    result = ScenarioRunner(llm_backend=backend, dry_run=False,
                            max_events=50, output_dir=tmp_path).run(django_spec(True), FIXTURES)
    assert result.runner_success, result.error
    assert result.termination_reason == "completed"
    assert result.dataset_eligible
    assert result.trace.injection_origin_count == 1
    assert result.trace.metadata["review_cycle_count"] == 1
    assert result.trace.metadata["review_finalization_reason"] == "review_event_budget"
    assert [r for r, _ in backend.stages] == [
        "inspector", "reviewer", "inspector", "reviewer", "reviewer"]
    events = result.trace.events
    assert len(events) == 46
    cap_events = [e for e in events if e.event_type == TraceEventType.PROTOCOL_RECOVERY]
    assert len(cap_events) == 1
    assert cap_events[0].event_index == 36
    assert not any(e.event_type in (TraceEventType.AGENT_HANDOFF, TraceEventType.TOPOLOGY_TRANSITION)
                   for e in events[37:])
    assert sum(e.event_type == TraceEventType.FINAL_RESPONSE for e in events) == 1
    assert events[-1].output_text == "Final answer after the review limit."


def test_forced_final_overrun_still_hits_global_safety_limit(tmp_path):
    class ExpensiveFinalBackend(TwoReadBackend):
        def generate(self, prompt="", tool_choice=None):
            if "handoff" not in self.tools and self.step < 12:
                self.step += 1
                # Alternate tools to avoid the separate repeated-tool guard.
                name = "read_text_file" if self.step % 2 else "list_directory"
                return ModelTurn(tool_call=ToolCall(id=f"costly-{self.step}", name=name,
                    input={"path": "manifest.json" if self.step % 2 else "."}),
                    text="", stop_reason="tool_use")
            return super().generate(prompt, tool_choice)

    scenario = django_spec()
    scenario.workflow_config.max_agent_turns = 20
    result = ScenarioRunner(llm_backend=ExpensiveFinalBackend(), dry_run=False,
                            max_events=50, output_dir=tmp_path).run(scenario, FIXTURES)
    assert result.termination_reason == "max_events_reached"
    assert not result.dataset_eligible
