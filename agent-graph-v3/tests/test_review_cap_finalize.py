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
