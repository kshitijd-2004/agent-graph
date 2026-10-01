"""Live-03 regressions: source reflow, omitted publication path, truncated calls."""
import json
from pathlib import Path

import pytest

from backend.api_backend import ModelTurn
from backend.hf_backend import HFBackend
from generation.result_x_evidence import source_quote
from generation.result_x_production import ResultXStageRunner
from generation.result_x_workflow import ContractError
from test_result_x_evidence_sealing import session, payload
from test_result_x_production import CASES, FIXTURES, FINAL, NativeBackend, run_pair


@pytest.mark.parametrize("source,quote", [
    ("Words on\nseveral lines.", "Words on several lines."),
    ("a\t b\r\nc", "a b c"),
    ("    def copy(self):\n        return self\n", "def copy(self):\n    return self"),
])
def test_whitespace_only_recovery_returns_original_span(source, quote):
    restored = source_quote(source, quote, "source.md")
    assert restored in source
    assert restored.split() == quote.split()
    assert source_quote(source, restored, "source.md") == restored


@pytest.mark.parametrize("quote", ["Gain is 41%.", "Gain was 40%.", "Gain is **40%**.",
                                  "Gainis 40%.", "Gain is 4 0%.", "gain is 40%.",
                                  "Gain is 40%. Not supported", "Gain is 40%…"])
def test_no_facts_words_markup_or_token_boundaries_changed(quote):
    assert source_quote("Gain is 40%.", quote, "source.md") is None


def test_python_indentation_restored_but_literal_whitespace_not_changed():
    source = 'def f():\n    return "a  b"\n'
    assert source_quote(source, 'def f():\n  return "a  b"', "source.py") == source.rstrip()
    assert source_quote(source, 'def f():\n  return "a b"', "source.py") is None


@pytest.mark.parametrize("pair,paper,quote", [
    ("04", "a", "We evaluate Neural Architecture Search (NAS) on low-resource natural language processing tasks. Our experiments across 12 benchmark datasets show that NAS-optimized models achieve a **40% improvement** in F1 score over hand-designed architectures when training data is limited (fewer than 5,000 examples)."),
    ("04", "b", "We replicate and extend prior work on Neural Architecture Search (NAS) for low-resource NLP. Our results show that **NAS offers no consistent advantage** over carefully hand-designed architectures. On 8 of 12 benchmark datasets, a manually tuned transformer model matches or exceeds NAS-optimized variants."),
    ("07", "a", "Our experiments across 12 benchmark datasets show that NAS-optimized models achieve a **40% improvement** in F1 score over hand-designed architectures when training data is limited (fewer than 5,000 examples)."),
    ("07", "b", "On 8 of 12 benchmark datasets, a manually tuned transformer model matches or exceeds NAS-optimized variants."),
])
def test_exact_live_research_quotes_seal_only_after_local_source_read(tmp_path, pair, paper, quote):
    source = (FIXTURES / "research_conflicting" / f"documents/paper_{paper}.md").read_text()
    assert quote not in source  # This is the actual formatting failure.
    s, _ = session(tmp_path, source)
    with pytest.raises(ContractError, match="actually read"):
        s.seal(payload(quote), [])
    s.owned.execute("read_text_file", {"path": "source.md"})
    candidate = payload(quote)
    s.seal(candidate, [])
    restored = json.loads(candidate.summary)["evidence"][0]["quote"]
    assert restored in source and restored.split() == quote.split()
    assert s.validator.call_args.args[0].evidence[0].quote == restored
    assert s.current["evidence_normalizations"][0]["submitted_quote"] == quote


@pytest.mark.parametrize("report_path", [None, "", "output/final.md"])
def test_omitted_final_path_uses_only_written_configured_file(tmp_path, report_path):
    s, _ = session(tmp_path, "Original source")
    args = {} if report_path is None else {"report_path": report_path}
    with pytest.raises(ContractError, match="not written"):
        s.submit(args)
    s.owned.execute("write_file", {"path": s.final_path, "content": "Final analysis"})
    s.submit(args)
    s.publish()
    assert (s.publication / s.final_path).read_text() == "Final analysis"


def test_wrong_path_and_previous_stage_artifact_cannot_be_published(tmp_path):
    s, begin = session(tmp_path, "Original source")
    s.owned.execute("write_file", {"path": s.final_path, "content": "Earlier analysis"})
    with pytest.raises(ContractError, match="must match"):
        s.submit({"report_path": "elsewhere.md"})
    begin("receiver")
    with pytest.raises(ContractError, match="not written"):
        s.submit({})
    assert s.pending_publication is None


class FormattingBackend(NativeBackend):
    def generate(self, prompt="", tool_choice=None):
        result = super().generate(prompt, tool_choice)
        call = result.tool_call
        if call.name == "handoff":
            draft = json.loads(call.input["summary"])
            for evidence in draft["evidence"]:
                evidence["quote"] = evidence["quote"].replace(" ", "  ")
            call.input["summary"] = json.dumps(draft)
        if call.name == "submit_final":
            del call.input["report_path"]
        self._conversation[-1]["content"] = repr(result)
        return result


@pytest.mark.parametrize("topology,mode", CASES)
def test_real_runner_canonical_identity_matching_and_publication(tmp_path, topology, mode):
    pair, audit, _ = run_pair(tmp_path / "formatted", topology, mode, backend_class=FormattingBackend)
    reference, baseline, _ = run_pair(tmp_path / "exact", topology, mode)
    for arm, result, expected in (("clean", pair.clean, reference.clean),
                                  ("input_disregard", pair.input_disregard, reference.input_disregard)):
        assert result.runner_success and result.dataset_eligible
        assert result.trace.injection_origin_count == expected.trace.injection_origin_count
        assert audit["arms"][arm]["results"] == baseline["arms"][arm]["results"]
        assert not any(row.get("protocol_repairs") for row in audit["arms"][arm]["stages"])
    outputs = list((tmp_path / "formatted/pair").glob(f"*/ws_*/{FINAL}"))
    assert len(outputs) == 2 and outputs[0].read_bytes() == outputs[1].read_bytes()


# Same failure class as live 02: finish_reason=length, no parsed tool call,
# unterminated text-form wrapper with repeated escaped newlines. Never repaired
# by parsing/filling missing fields; only a subsequent complete call can proceed.
TRUNCATED = '<tool_call>\n{"name":"handoff","arguments":{"summary":"{\\"reasoning\\":\\"' + '\\\\n' * 1700


class TruncatedBackend(NativeBackend):
    def __init__(self, persistent=False):
        super().__init__()
        self.persistent = persistent

    def reset(self, **kwargs):
        super().reset(**kwargs)
        self.emitted_bad = False

    def generate(self, prompt="", tool_choice=None):
        # Fail on the revision invocation, after the reverse handoff delivery.
        if self.role == "extractor" and self.visits[self.role] == 2 and self.step == 8:
            if self.persistent or not self.emitted_bad:
                self.emitted_bad = True
                self._conversation.append({"role": "assistant", "content": TRUNCATED})
                return HFBackend._extract_turn(self, {"choices": [{
                    "finish_reason": "length", "message": {"content": TRUNCATED}}]})
            assert prompt == ResultXStageRunner.TOOL_CALL_NUDGE
        return super().generate(prompt, tool_choice)


@pytest.mark.parametrize("persistent", [True, False])
def test_live02_truncation_eligibility_requires_actual_recovery(tmp_path, persistent):
    pair, audit, _ = run_pair(tmp_path, backend_class=TruncatedBackend,
                             backend_kwargs={"persistent": persistent}, source_agent="analyst")
    for arm, result in (("clean", pair.clean), ("input_disregard", pair.input_disregard)):
        assert result.dataset_eligible is not persistent
        assert result.termination_reason == ("protocol_violation" if persistent else "completed")
        row = audit["arms"][arm]["stages"][2]
        bad = [t for t in row["turns"] if t["output"].get("stop_reason") == "length"]
        assert len(bad) == (2 if persistent else 1)
        assert all(t["output"]["tool_call"] is None for t in bad)
        assert ResultXStageRunner.TOOL_CALL_NUDGE in [t["request"]["prompt"] for t in row["turns"]]
    assert pair.clean.trace.injection_origin_count == 0
    assert pair.input_disregard.trace.injection_origin_count == 1
    assert pair.input_disregard.trace.metadata["origin_validation"]["valid"]
