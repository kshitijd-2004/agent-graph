"""Live failure classes: invocation-local reads, prose reflow, code dedent."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from generation.result_x_production import _Session
from generation.result_x_workflow import ContractError
from test_result_x_production import NativeBackend, run_pair


def session(tmp_path, text):
    publication = tmp_path / "original"
    publication.mkdir()
    (publication / "source.md").write_text(text)
    value = _Session(tmp_path / "private", Mock(return_value=True), "output/final.md")
    topology = SimpleNamespace(get_outgoing_handoffs=lambda role: [SimpleNamespace(to_stage="receiver")])

    def begin(role, incoming=None):
        value.begin(dict(ws_path=publication, stage=SimpleNamespace(agent_role=role, can_finalize=False, can_handoff=True),
                         topology=topology, handoff_from_payload=incoming))

    begin("producer")
    return value, begin


def payload(quote):
    return SimpleNamespace(from_agent="producer", extra={}, summary=json.dumps({
        "conclusion": "Supported conclusion", "reasoning": "Reasoning for the receiver",
        "intended_use": "Guide the next analysis", "evidence": [{"path": "source.md", "quote": quote}],
    }))


@pytest.mark.parametrize("copy_envelope", [False, True])
def test_received_evidence_is_not_a_local_read(tmp_path, copy_envelope):
    s, begin = session(tmp_path, "Original evidence.\n")
    s.owned.execute("read_text_file", {"path": "source.md"})
    first = payload("Original evidence.")
    s.seal(first, [])
    begin("receiver", [first])
    second = payload("Original evidence.")
    if copy_envelope:
        second.summary = first.summary
    assert s.owned.read_sources == set()
    with pytest.raises(ContractError, match="actually read"):
        s.seal(second, [first])
    # An actual local read permits a new derivation with a new identity.
    s.owned.execute("read_text_file", {"path": "source.md"})
    second.from_agent = "receiver"
    s.seal(second, [first])
    assert json.loads(second.summary)["result_id"] != json.loads(first.summary)["result_id"]
    assert json.loads(second.summary)["input_result_ids"] == [json.loads(first.summary)["result_id"]]


def test_unchanged_relay_needs_no_new_read_and_preserves_wire(tmp_path):
    s, begin = session(tmp_path, "Original evidence.\n")
    s.owned.execute("read_text_file", {"path": "source.md"})
    first = payload("Original evidence.")
    s.seal(first, [])
    begin("receiver", [first])
    relay = payload("")
    relay.summary = json.dumps({"relay_result_ids": [json.loads(first.summary)["result_id"]]})
    s.seal(relay, [first])
    assert s.owned.read_sources == set()
    assert relay.summary == first.summary
    assert s.validator.call_count == 1


@pytest.mark.parametrize("original,altered", [
    ("NAS offers **no consistent advantage**.\n", "NAS offers no consistent advantage."),
    ("NAS offers no consistent advantage.\n", "NAS is not consistently better."),
])
def test_reformatted_or_paraphrased_quotes_still_rejected(tmp_path, original, altered):
    s, _ = session(tmp_path, original)
    s.owned.execute("read_text_file", {"path": "source.md"})
    with pytest.raises(ContractError, match="quote absent"):
        s.seal(payload(altered), [])
    s.validator.assert_not_called()
    # Both intact multiline text and short verbatim excerpts remain admissible.
    s.seal(payload(original.rstrip("\n")), [])
    s.seal(payload(original.splitlines()[0].strip()), [])
    assert s.validator.call_count == 2


def test_failed_read_does_not_establish_provenance(tmp_path):
    s, _ = session(tmp_path, "Original evidence.\n")
    assert s.owned.execute("read_text_file", {"path": "missing.md"}).startswith("Error")
    assert not s.owned.read_sources
    with pytest.raises(ContractError, match="actually read"):
        s.seal(payload("Original evidence."), [])


class NoAnalystRead(NativeBackend):
    def generate(self, prompt="", tool_choice=None):
        if self.role == "analyst" and self.step == 0:
            self.step = 8  # Re-emit incoming derivation without accessing sources.
        return super().generate(prompt, tool_choice)


def test_real_runner_persists_failing_stage_read_scope_and_prompt(tmp_path):
    with pytest.raises(ContractError):
        run_pair(tmp_path, backend_class=NoAnalystRead)
    audit = json.loads((tmp_path / "pair/result_x_audit.json").read_text())
    arm = audit["arms"]["clean"]
    assert "actually read" in arm["error"]
    assert arm["stages"][0]["read_sources"] == ["documents/report.md"]
    assert arm["stages"][1]["read_sources"] == []
    prompt = arm["stages"][1]["reset"]["system_prompt"]
    for instruction in ("THIS stage invocation", "short verbatim excerpt", "do not paste",
                        "reflow prose", "dedent code", "exact source substring"):
        assert instruction in prompt
