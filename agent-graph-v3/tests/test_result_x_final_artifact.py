"""Publication regressions using scripted production stages, never model calls."""
import json

import pytest

from generation.result_x_workflow import ContractError
from backend.api_backend import ModelTurn, ToolCall
from test_result_x_production import FINAL, REPORT, NativeBackend, run_pair
from test_result_x_evidence_sealing import session
from generation.runner import ScenarioRunner
from test_result_x_production import FIXTURES, spec, validate

CASES = [
    ("review_loop", {"source_agent": "extractor"}),
    ("review_loop", {"source_agent": "analyst"}),
    ("coordinator_workers", {}),
]


class NoArtifactBackend(NativeBackend):
    def __init__(self):
        super().__init__(stubborn=True)

    def generate(self, prompt="", tool_choice=None):
        if self.step == 5:
            self.step = 8  # Read sources and hand off, but never write a file.
        return super().generate(prompt, tool_choice)


@pytest.mark.parametrize("topology,options", CASES)
def test_live04_missing_write_stays_rejected_at_forced_final(tmp_path, topology, options):
    with pytest.raises(ContractError):
        run_pair(tmp_path, topology, "single_origin", backend_class=NoArtifactBackend, **options)
    audit = json.loads((tmp_path / "pair/result_x_audit.json").read_text())
    clean = audit["arms"]["clean"]
    assert not clean["runner_success"] and not clean["dataset_eligible"]
    assert "Final artifact was not written" in clean["error"]
    stages = clean["stages"]
    assert len(stages) > 4
    assert "handoff" not in stages[-1]["reset"]["mcp_tools"]
    assert "submit_final" in stages[-1]["reset"]["mcp_tools"]
    assert not any(c["tool"] == "write_file" for s in stages for c in s["tool_calls"])
    assert not list((tmp_path / "pair").glob(f"*/ws_*/{FINAL}"))
    assert stages[-1]["final_artifact_repair"]["attempt"] == 1
    assert sum((t["output"].get("tool_call") or {}).get("name") == "submit_final"
               for t in stages[-1]["turns"]) == 2


class CorrectingBackend(NoArtifactBackend):
    def generate(self, prompt="", tool_choice=None):
        if "handoff" not in self.tools and self.step == 9:
            self.step += 1
            output = ModelTurn(tool_call=ToolCall(id="write-final-after-correction", name="write_file",
                               input={"path": FINAL, "content": REPORT}), stop_reason="tool_use")
            self._conversation.append({"role": "assistant", "content": repr(output)})
            return output
        return super().generate(prompt, tool_choice)


class TextCorrectingBackend(CorrectingBackend):
    @property
    def _messages(self):
        raise AttributeError("scripted text backend")


@pytest.mark.parametrize("topology,options", CASES)
@pytest.mark.parametrize("backend_class", [CorrectingBackend, TextCorrectingBackend])
def test_forced_final_corrects_submit_then_write_then_submit(tmp_path, topology, options, backend_class):
    pair, audit, _ = run_pair(tmp_path, topology, "single_origin", backend_class=backend_class, **options)
    for arm, result in (("clean", pair.clean), ("input_disregard", pair.input_disregard)):
        assert result.runner_success and result.dataset_eligible
        assert result.trace.metadata["review_cap_forced_final"]
        last = audit["arms"][arm]["stages"][-1]
        assert last["reset"]["system_prompt"].startswith("FORCED FINALIZATION:")
        actions = [t["output"]["tool_call"]["name"] for t in last["turns"]]
        assert actions[-3:] == ["submit_final", "write_file", "submit_final"]
        repair = last["final_artifact_repair"]["instruction"]
        assert "has not been written" in repair and FINAL in repair
        assert any(repair in str(t["request"]["input"]) for t in last["turns"])
        events = [e for e in result.trace.events
                  if e.observable.get("reason") == "result_x_missing_final_artifact"]
        assert len(events) == 1 and not events[0].event_labels.is_injection_origin
        assert len([e for e in result.trace.events if e.event_type.value == "final_response"]) == 1
    assert pair.clean.trace.injection_origin_count == 0
    assert pair.input_disregard.trace.injection_origin_count == 1
    files = list((tmp_path / "pair").glob(f"*/ws_*/{FINAL}"))
    assert len(files) == 2 and all(p.read_text() == REPORT for p in files)


@pytest.mark.parametrize("forced,allow", [(False, True), (True, False)])
def test_no_repair_outside_forced_final_or_without_remaining_budget(tmp_path, forced, allow):
    s, _ = session(tmp_path, "Original source")
    s.forced_finalization = forced
    with pytest.raises(ContractError, match="not written"):
        s.submit_or_repair({}, allow_repair=allow)
    assert s.final_repairs == 0 and s.pending_publication is None


def test_wrong_path_is_not_repaired_or_published(tmp_path):
    s, _ = session(tmp_path, "Original source")
    s.forced_finalization = True
    with pytest.raises(ContractError, match="must match"):
        s.submit_or_repair({"report_path": "output/other.md"}, allow_repair=True)
    assert s.final_repairs == 0 and s.pending_publication is None


@pytest.mark.parametrize("turn_cap", [6, 7])
def test_real_forced_final_requires_budget_for_both_write_and_submit(tmp_path, turn_cap):
    scenario = spec("review_loop", "single_origin")
    scenario.workflow_config.max_agent_turns = turn_cap
    runner = ScenarioRunner(dry_run=False, max_events=700, output_dir=tmp_path)
    with pytest.raises(ContractError):
        runner.run_result_x_pair(scenario, FIXTURES, backend_factory=CorrectingBackend,
                                 validator=validate, final_path=FINAL, pair_name="pair")
    arm = json.loads((tmp_path / "pair/result_x_audit.json").read_text())["arms"]["clean"]
    last = arm["stages"][-1]
    assert "handoff" not in last["reset"]["mcp_tools"]
    assert "Final artifact was not written" in arm["error"]
    assert "final_artifact_repair" not in last
    assert len(last["turns"]) == 6
