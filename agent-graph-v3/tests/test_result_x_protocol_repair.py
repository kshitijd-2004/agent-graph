"""CPU-only rejected-draft corrections; no backend/API/model inference."""
from copy import deepcopy
import json

import pytest

from generation.result_x_workflow import ContractError
from test_result_x_evidence_sealing import session, payload
from generation.runner import ScenarioRunner
from test_result_x_production import CASES, FIXTURES, FINAL, NativeBackend, run_pair, spec, validate


def corrupt(draft, kind):
    if kind == "missing_intended_use":
        del draft["intended_use"]
    else:
        draft["evidence"][0]["quote"] += " (paraphrased)"


@pytest.mark.parametrize("kind", ["missing_intended_use", "nonverbatim_quote"])
def test_repair_never_fills_or_seals_invalid_content(tmp_path, kind):
    s, _ = session(tmp_path, "Original evidence.\n")
    s.owned.execute("read_text_file", {"path": "source.md"})
    bad = payload("Original evidence.")
    draft = json.loads(bad.summary)
    corrupt(draft, kind)
    bad.summary = json.dumps(draft)
    before = deepcopy(bad)
    message = s.seal_or_repair(bad, [], allow_repair=True)
    assert "ALL required" in message and "THIS invocation" in message
    assert bad == before and not s.results
    s.validator.assert_not_called()
    # Only a new producer-authored candidate can complete the handoff.
    corrected = payload("Original evidence.")
    assert s.seal_or_repair(corrected, [], allow_repair=True) is None
    assert json.loads(corrected.summary)["intended_use"] == "Guide the next analysis"
    assert len(s.results) == 1
    s.validator.assert_called_once()


@pytest.mark.parametrize("allow_repair", [True, False])
def test_single_retry_budget_and_no_extra_turns(tmp_path, allow_repair):
    s, _ = session(tmp_path, "Original evidence.\n")
    s.owned.execute("read_text_file", {"path": "source.md"})
    bad = payload("Altered evidence.")
    if allow_repair:
        assert s.seal_or_repair(bad, [], allow_repair=True)
    with pytest.raises(ContractError, match="quote absent"):
        s.seal_or_repair(bad, [], allow_repair=allow_repair)
    assert not s.results
    assert s.protocol_repairs == int(allow_repair)


@pytest.mark.parametrize("failure", ["unread", "semantic_rejection", "semantic_exception"])
def test_provenance_and_semantic_failures_are_not_retried(tmp_path, failure):
    s, _ = session(tmp_path, "Original evidence.\n")
    if failure != "unread":
        s.owned.execute("read_text_file", {"path": "source.md"})
    if failure == "semantic_rejection":
        s.validator.return_value = False
    if failure == "semantic_exception":
        s.validator.side_effect = RuntimeError("review failed")
    with pytest.raises(ContractError):
        s.seal_or_repair(payload("Original evidence."), [], allow_repair=True)
    assert s.protocol_repairs == 0 and not s.results


class RepairBackend(NativeBackend):
    def __init__(self, kind="missing_intended_use", persistent=False):
        super().__init__()
        self.kind, self.persistent = kind, persistent

    def reset(self, **kwargs):
        super().reset(**kwargs)
        self.bad_emitted = False

    def generate(self, prompt="", tool_choice=None):
        result = super().generate(prompt, tool_choice)
        call = result.tool_call
        if call.name == "handoff" and (self.persistent or not self.bad_emitted):
            draft = json.loads(call.input["summary"])
            corrupt(draft, self.kind)
            call.input["summary"] = json.dumps(draft)
            self.bad_emitted = True
            self._conversation[-1]["content"] = repr(result)
        return result


@pytest.mark.parametrize("topology,mode", CASES)
@pytest.mark.parametrize("kind", ["missing_intended_use", "nonverbatim_quote"])
def test_real_runner_correction_preserves_matching_identity_and_origins(tmp_path, topology, mode, kind):
    pair, audit, _ = run_pair(tmp_path / "repaired", topology, mode,
                             backend_class=RepairBackend, backend_kwargs={"kind": kind})
    baseline, baseline_audit, _ = run_pair(tmp_path / "valid", topology, mode)
    for arm, result, original in (("clean", pair.clean, baseline.clean),
                                  ("input_disregard", pair.input_disregard, baseline.input_disregard)):
        assert result.runner_success and result.dataset_eligible
        assert result.trace.injection_origin_count == original.trace.injection_origin_count
        assert audit["arms"][arm]["results"] == baseline_audit["arms"][arm]["results"]
        repairs = [e for e in result.trace.events if e.observable.get("reason") == "result_x_draft"]
        assert repairs and all(not e.event_labels.is_injection_origin for e in repairs)
        for row in audit["arms"][arm]["stages"]:
            if not row.get("protocol_repairs"):
                continue
            assert len(row["protocol_repairs"]) == 1
            message = row["protocol_repairs"][0]["instruction"]
            assert any(any(m.get("content") == message for m in turn["request"]["input"])
                       for turn in row["turns"])
    # Prefix retries replay through the unchanged mechanism; no new origin for
    # rejected drafts, and only the separate instruction differs at delivery.
    assert pair.input_disregard.trace.metadata["prefix_replayed_turns"] > 0


@pytest.mark.parametrize("kind", ["missing_intended_use", "nonverbatim_quote"])
def test_real_runner_repeated_invalid_candidate_fails_closed(tmp_path, kind):
    with pytest.raises(ContractError):
        run_pair(tmp_path, backend_class=RepairBackend,
                 backend_kwargs={"kind": kind, "persistent": True})
    audit = json.loads((tmp_path / "pair/result_x_audit.json").read_text())
    arm = audit["arms"]["clean"]
    assert not arm["results"] and not arm["runner_success"]
    stage = arm["stages"][0]
    assert len(stage["protocol_repairs"]) == 1
    assert sum((t["output"].get("tool_call") or {}).get("name") == "handoff"
               for t in stage["turns"]) == 2


class TextRepairBackend(RepairBackend):
    @property
    def _messages(self):
        raise AttributeError("text-only deterministic backend")


@pytest.mark.parametrize("kind", ["missing_intended_use", "nonverbatim_quote"])
def test_text_backend_correction_and_replay(tmp_path, kind):
    pair, audit, _ = run_pair(tmp_path, backend_class=TextRepairBackend,
                             backend_kwargs={"kind": kind})
    assert pair.clean.runner_success and pair.input_disregard.runner_success
    for arm in audit["arms"].values():
        row = arm["stages"][0]
        message = row["protocol_repairs"][0]["instruction"]
        assert any(message in turn["request"]["input"] for turn in row["turns"])


def test_real_runner_last_turn_does_not_extend_budget(tmp_path):
    scenario = spec("review_loop", "single_origin")
    scenario.workflow_config.max_agent_turns = 9
    runner = ScenarioRunner(dry_run=False, max_events=700, output_dir=tmp_path)
    with pytest.raises(ContractError):
        runner.run_result_x_pair(scenario, FIXTURES, backend_factory=RepairBackend,
                                 validator=validate, final_path=FINAL, pair_name="pair")
    arm = json.loads((tmp_path / "pair/result_x_audit.json").read_text())["arms"]["clean"]
    assert len(arm["stages"][0]["turns"]) == 9
    assert "protocol_repairs" not in arm["stages"][0]
    assert not arm["results"]
