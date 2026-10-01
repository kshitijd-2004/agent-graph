"""Matched ID contracts through real ScenarioRunner/StageRunner and native tools."""
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, replace
import json
from pathlib import Path

import pytest

from backend.api_backend import ModelTurn, ToolCall
from benchmark.benchmark_runner import BenchmarkRunner
from generation.result_x_workflow import ContractError
from generation.runner import ScenarioRunner
from generation.scenario_builder import ScenarioBuilder, ScenarioBuildConfig
from schemas import TraceEventType
from scripts.validate_result_x_contract import derive, validate, FINAL, FIXTURE_ID

FIXTURES = Path(__file__).resolve().parents[1] / "workspace_fixtures"
SOURCE = (FIXTURES / FIXTURE_ID / "documents/report.md").read_bytes()
DRAFT, REPORT = derive({"documents/report.md": SOURCE})
CASES = [("review_loop", "single_origin"), ("branch_and_verify", "single_origin"),
         ("branch_and_verify", "many_to_one"), ("coordinator_workers", "single_origin"),
         ("coordinator_workers", "one_to_many")]


class NativeBackend:
    """Real native-message interface; deterministic analysis, no condition flag."""
    def __init__(self, *, relay=False, invalid=False, final_path=FINAL, stubborn=False):
        self.visits = Counter()
        self.generated = Counter()
        self.relay, self.invalid, self.final_path, self.stubborn = relay, invalid, final_path, stubborn

    @property
    def _messages(self):
        return [{"role": "system", "content": self.system}] + self._conversation

    def reset(self, *, agent_name, mcp_tools, system_prompt, task):
        self.role, self.tools, self.system = agent_name, mcp_tools, system_prompt
        self.visits[agent_name] += 1
        self._conversation = [{"role": "user", "content": task}]
        self.step = 0

    def _append_tool_result(self, call, result):
        self._conversation.append({"role": "tool", "content": result, "tool_call_id": call.id})

    def generate(self, prompt="", tool_choice=None):
        self.generated[self.role] += 1
        # Probes run before local writes. Original task sources remain readable.
        actions = [
            ("read_text_file", {"path": "output/duplicate.md"}),
            ("read_text_file", {"path": FINAL}),
            ("read_text_file", {"path": "../0/output/duplicate.md"}),
            ("search_files", {"path": ".", "pattern": "../*"}),
            ("read_text_file", {"path": "documents/report.md"}),
            ("write_file", {"path": "output/duplicate.md", "content": DRAFT.reasoning}),
            ("write_file", {"path": "documents/report.md", "content": "private source overwrite: " + DRAFT.reasoning}),
            ("write_file", {"path": FINAL, "content": REPORT}),
        ]
        if self.step < len(actions):
            name, args = actions[self.step]
        elif "submit_final" in self.tools and (not self.stubborn or "handoff" not in self.tools):
            name, args = "submit_final", {"summary": DRAFT.conclusion, "report_path": self.final_path}
        else:
            draft = asdict(DRAFT)
            if self.invalid:
                draft["conclusion"] = "Analysis complete."
            received = []
            for msg in self._conversation:
                try:
                    obj = json.loads(msg.get("content", ""))
                    if isinstance(obj, dict) and obj.get("result_id"):
                        received.append(obj["result_id"])
                except (TypeError, ValueError):
                    pass
            if self.relay and received:
                draft = {"relay_result_ids": received}
            name, args = "handoff", {"summary": json.dumps(draft), "target_agent": "",
                                     "report_path": FINAL, "source_paths": ["documents/report.md"]}
        self.step += 1
        output = ModelTurn(tool_call=ToolCall(id=f"call-{self.step}", name=name, input=args),
                           text="", stop_reason="tool_use")
        self._conversation.append({"role": "assistant", "content": repr(output)})
        return output


def spec(topology, mode, *, occurrence=1, target=None, source_agent=None, memory="ephemeral_private"):
    lep = deepcopy(BenchmarkRunner._resolve_lep(None, "LEP_INPUT_DISREGARD", "financial_analysis"))
    lep.trigger.occurrence = occurrence
    lep.trigger.source_agent = source_agent
    if target:
        lep.topology_target = target
    return ScenarioBuilder().build_single(ScenarioBuildConfig(
        task_family="financial_analysis", fixture_id=FIXTURE_ID, task_variant="finqa",
        topology=topology, propagation_mode=mode, condition="single_lep", lep_configs=[lep],
        memory_mode=memory, max_agent_turns=14))


def run_pair(tmp_path, topology="review_loop", mode="single_origin", *, backend_class=NativeBackend,
             backend_kwargs=None, **spec_kwargs):
    backends = []
    def factory():
        backend = backend_class(**(backend_kwargs or {}))
        backends.append(backend)
        return backend
    runner = ScenarioRunner(dry_run=False, max_events=700, output_dir=tmp_path)
    pair = runner.run_result_x_pair(spec(topology, mode, **spec_kwargs), FIXTURES,
                                   backend_factory=factory, validator=validate,
                                   final_path=FINAL, pair_name="pair")
    return pair, json.loads(pair.audit_path.read_text()), backends


@pytest.mark.parametrize("topology,mode", CASES)
def test_real_production_matched_contract(tmp_path, topology, mode):
    pair, audit, backends = run_pair(tmp_path, topology, mode)
    clean, injected = pair.clean, pair.input_disregard
    for result in (clean, injected):
        assert result.runner_success, result.error
        assert result.termination_reason == "completed", result.trace.metadata
        assert result.dataset_eligible, result.trace.metadata
        assert result.trace.labels.task_success
        assert result.trace.labels.factual_score == 1.0
        assert result.trace.events[-1].event_type == TraceEventType.FINAL_RESPONSE
        assert result.trace.metadata["communication_contract"] == "result-x/production-v1"
    assert clean.trace.injection_origin_count == 0
    assert injected.trace.injection_origin_count == (2 if mode == "many_to_one" else 1)
    assert injected.trace.metadata["prefix_replayed_turns"] > 0
    assert sum(backends[1].generated.values()) < sum(backends[0].generated.values())
    clean_rows, id_rows = [audit["arms"][a]["stages"] for a in ("clean", "input_disregard")]
    assert [r["role"] for r in clean_rows] == [r["role"] for r in id_rows]
    deliveries = []
    for c, i in zip(clean_rows, id_rows):
        assert c["reset"] == i["reset"]
        assert c["incoming"] == [{**e, "instruction": ""} for e in i["incoming"]]
        instructions = [e["instruction"] for e in i["incoming"] if e["instruction"]]
        assert c["turns"][0]["request"]["input"] == [m for m in i["turns"][0]["request"]["input"]
                                                        if m["content"] not in instructions]
        for e in i["incoming"]:
            x = json.loads(e["wire"])
            assert x["reasoning"] == DRAFT.reasoning
            assert x["evidence"] == asdict(DRAFT)["evidence"] or x["evidence"] == list(asdict(DRAFT)["evidence"])
            assert x["producer"] and x["result_id"] and x["scope"] and x["intended_use"]
            if e["instruction"]:
                assert x["producer"] in e["instruction"] and x["result_id"] in e["instruction"]
                deliveries.append((i["role"], x))
        for row in (c, i):
            calls = row["tool_calls"]
            assert all(call["output"].startswith("Error:") for call in calls[:4])
            assert calls[4]["output"].encode() == SOURCE
            assert "write_file" in row["reset"]["mcp_tools"]
            assert not any(e.get("instruction") for e in c["incoming"])
    assert len(deliveries) == (3 if mode == "one_to_many" else 2 if mode == "many_to_one" else 1)
    if mode == "one_to_many":
        assert len({x["result_id"] for _, x in deliveries}) == 1
        assert {role for role, _ in deliveries} == {"specialist_a", "specialist_b", "synthesizer"}
        returned = [json.loads(e["wire"]) for e in id_rows[-1]["incoming"]]
        assert len(returned) == 3
        assert len({x["producer"] for x in returned}) == 3
        assert all(x["input_result_ids"] == [deliveries[0][1]["result_id"]] for x in returned)
    if mode == "many_to_one":
        assert {x["producer"] for _, x in deliveries} == {"researcher", "analyst"}
        assert len({x["result_id"] for _, x in deliveries}) == 2
    reports = list(tmp_path.glob("pair/*/ws_*/output/financial_summary.md"))
    assert len(reports) == 2
    assert all(p.read_text() == REPORT for p in reports)
    assert not list(tmp_path.glob("pair/*/ws_*/output/duplicate.md"))


@pytest.mark.parametrize("occurrence,origin_role", [(1, "extractor"), (1, "analyst"), (2, "extractor")])
def test_review_forward_reverse_and_revision_origins(tmp_path, occurrence, origin_role):
    pair, audit, _ = run_pair(tmp_path, occurrence=occurrence, source_agent=origin_role)
    origins = [e for e in pair.input_disregard.trace.events if e.event_labels.is_injection_origin]
    assert len(origins) == 1 and origins[0].agent_role == origin_role
    rows = audit["arms"]["input_disregard"]["stages"]
    assert [r["role"] for r in rows] == ["extractor", "analyst", "extractor", "analyst"]
    inputs = [json.loads(row["incoming"][0]["wire"]) for row in rows[1:]]
    assert len({x["result_id"] for x in inputs}) == 3
    assert inputs[1]["input_result_ids"] == [inputs[0]["result_id"]]
    assert inputs[2]["input_result_ids"] == [inputs[1]["result_id"]]
    assert sum(bool(e["instruction"]) for r in rows for e in r["incoming"]) == 1
    assert pair.input_disregard.dataset_eligible


def test_relay_preserves_original_identity_and_does_not_extend_instruction(tmp_path):
    pair, audit, _ = run_pair(tmp_path, backend_kwargs={"relay": True})
    rows = audit["arms"]["input_disregard"]["stages"]
    delivered = [e for r in rows for e in r["incoming"]]
    assert len({e["wire"] for e in delivered}) == 1
    assert all(json.loads(e["wire"])["producer"] == "extractor" for e in delivered)
    assert sum(bool(e["instruction"]) for e in delivered) == 1
    assert pair.input_disregard.dataset_eligible


@pytest.mark.parametrize("topology,mode,target,producer", [
    ("branch_and_verify", "single_origin", "branch:analyst", "analyst"),
    ("coordinator_workers", "single_origin", "worker:specialist_b", "specialist_b"),
])
def test_selected_single_origin_names_exact_producer(tmp_path, topology, mode, target, producer):
    pair, audit, _ = run_pair(tmp_path, topology, mode, target=target)
    targeted = [json.loads(e["wire"]) for r in audit["arms"]["input_disregard"]["stages"]
                for e in r["incoming"] if e["instruction"]]
    assert len(targeted) == 1 and targeted[0]["producer"] == producer
    assert pair.input_disregard.dataset_eligible


@pytest.mark.parametrize("backend_kwargs,match", [({"invalid": True}, "Semantic validator"),
                                                  ({"final_path": "output/wrong.md"}, "submit_final")])
def test_contract_failures_never_publish_or_admit(tmp_path, backend_kwargs, match):
    with pytest.raises(ContractError):
        run_pair(tmp_path, backend_kwargs=backend_kwargs)
    audit = json.loads((tmp_path / "pair/result_x_audit.json").read_text())
    assert match in audit["arms"]["clean"]["error"]
    assert not list(tmp_path.glob("pair/*/ws_*/output/*.md"))


@pytest.mark.parametrize("memory", ["ephemeral_shared", "persistent_shared"])
def test_shared_memory_is_rejected_not_silently_disabled(tmp_path, memory):
    with pytest.raises(ContractError, match="not silently disabled"):
        run_pair(tmp_path, memory=memory)


def test_other_leps_cannot_enter_result_x_path(tmp_path):
    runner = ScenarioRunner(output_dir=tmp_path)
    scenario = spec("review_loop", "single_origin")
    for code in ("LEP_TOOL_RESULT_CORRUPTION", "LEP_INDIRECT_PROMPT_INJECTION", "LEP_MEMORY_POISONING", "LEP_HANDOFF_CORRUPTION"):
        scenario.lep_configs[0].code = code
        with pytest.raises(ContractError, match="exactly one LEP_INPUT_DISREGARD"):
            runner.run_result_x_pair(scenario, FIXTURES, backend_factory=NativeBackend,
                validator=validate, final_path=FINAL, pair_name=code)


class TextBackend:
    """Legacy text-prompt interface, still using production native tool returns."""
    def __init__(self, **kwargs):
        self.delegate = NativeBackend(**kwargs)
        self.generated = self.delegate.generated

    def reset(self, **kwargs):
        self.delegate.reset(**kwargs)

    def generate(self, prompt="", tool_choice=None):
        return self.delegate.generate(prompt, tool_choice)

    def _append_tool_result(self, *args):
        self.delegate._append_tool_result(*args)


@pytest.mark.parametrize("topology,mode", CASES)
def test_text_backend_exact_boundary_difference(tmp_path, topology, mode):
    pair, audit, _ = run_pair(tmp_path, topology, mode, backend_class=TextBackend)
    assert pair.input_disregard.dataset_eligible
    for c, i in zip(audit["arms"]["clean"]["stages"], audit["arms"]["input_disregard"]["stages"]):
        normalized = i["turns"][0]["request"]["input"]
        for e in i["incoming"]:
            if e["instruction"]:
                normalized = normalized.replace("\n[User] " + e["instruction"], "", 1)
        assert normalized == c["turns"][0]["request"]["input"]


@pytest.mark.parametrize("topology,mode", CASES)
def test_real_hf_backend_with_cpu_transport(tmp_path, topology, mode):
    from backend.hf_backend import HFBackend

    class OfflineHF(HFBackend):
        def __init__(self):
            super().__init__(model="cpu-transport-only", base_url="http://unused.invalid/v1")
            self.script = NativeBackend()
            self.generated = self.script.generated

        def reset(self, **kwargs):
            super().reset(**kwargs)
            self.script.reset(**kwargs)

        def _call_api(self, messages, **kwargs):
            assert kwargs["tools"]  # unchanged real tool schemas
            self.script._conversation = deepcopy(messages)
            turn = self.script.generate()
            tc = turn.tool_call
            return {"choices": [{"finish_reason": "tool_calls", "message": {
                "content": None, "tool_calls": [{"id": tc.id, "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.input)}}]}}]}

    pair, _, _ = run_pair(tmp_path, topology, mode, backend_class=OfflineHF)
    assert pair.clean.dataset_eligible and pair.input_disregard.dataset_eligible


def test_forced_final_retains_contract_without_changing_cap(tmp_path):
    pair, audit, _ = run_pair(tmp_path, backend_kwargs={"stubborn": True})
    for result in (pair.clean, pair.input_disregard):
        assert result.dataset_eligible
        assert result.trace.metadata["review_cap_forced_final"]
        assert result.trace.metadata["review_cycle_count"] == 4
    rows = audit["arms"]["input_disregard"]["stages"]
    assert rows[-1]["role"] == "analyst"
    assert "handoff" not in rows[-1]["reset"]["mcp_tools"]
    assert rows[-1]["incoming"]  # full revision envelope survives finalize-only turn
    assert sum(bool(e["instruction"]) for r in rows for e in r["incoming"]) == 1


def test_refused_reverse_boundary_is_not_admitted_as_delivered_id(tmp_path, monkeypatch):
    import generation.topology as topologies
    original = topologies.get_topology
    monkeypatch.setattr(topologies, "get_topology", lambda *a, **kw: original(*a, **kw, max_review_cycles=0))
    pair, audit, _ = run_pair(tmp_path, source_agent="analyst")
    assert pair.clean.dataset_eligible
    assert pair.input_disregard.trace.injection_origin_count == 1
    assert pair.input_disregard.termination_reason == "completed"
    assert not pair.input_disregard.dataset_eligible
    assert pair.input_disregard.trace.metadata["result_x_undelivered_origins"]
    assert not any(e["instruction"] for row in audit["arms"]["input_disregard"]["stages"] for e in row["incoming"])


def test_nondeterministic_producer_is_replayed_not_resampled(tmp_path):
    import uuid
    produced = []

    class VariableProducer(NativeBackend):
        def generate(self, *args, **kwargs):
            turn = super().generate(*args, **kwargs)
            if turn.tool_call.name == "handoff":
                data = json.loads(turn.tool_call.input["summary"])
                data["intended_use"] += " Run nonce: " + uuid.uuid4().hex
                turn.tool_call.input["summary"] = json.dumps(data)
                self._conversation[-1]["content"] = repr(turn)
                produced.append(data["intended_use"])
            return turn

    def validator(candidate, sources):
        return validate(replace(candidate, intended_use=DRAFT.intended_use), sources)

    runner = ScenarioRunner(dry_run=False, max_events=700, output_dir=tmp_path)
    pair = runner.run_result_x_pair(spec("branch_and_verify", "many_to_one"), FIXTURES,
        backend_factory=VariableProducer, validator=validator, final_path=FINAL, pair_name="pair")
    assert pair.input_disregard.dataset_eligible
    assert len(produced) == 2  # one model derivation per branch, none resampled in ID
    audit = json.loads(pair.audit_path.read_text())
    clean = audit["arms"]["clean"]["stages"][-1]["incoming"]
    injected = audit["arms"]["input_disregard"]["stages"][-1]["incoming"]
    assert [e["wire"] for e in clean] == [e["wire"] for e in injected]


def test_missing_id_origin_remains_ineligible(tmp_path):
    pair, _, _ = run_pair(tmp_path, occurrence=100)
    assert pair.clean.dataset_eligible
    assert not pair.input_disregard.dataset_eligible
    assert pair.input_disregard.trace.injection_origin_count == 0
    assert pair.input_disregard.trace.metadata["prefix_replayed_turns"] > 0


def test_original_source_recomputation_uses_actual_tool_result(tmp_path):
    class Recomputing(NativeBackend):
        def reset(self, **kwargs):
            super().reset(**kwargs)
            self.recomputed = False

        def _append_tool_result(self, call, result):
            super()._append_tool_result(call, result)
            if call.name == "read_text_file" and call.input.get("path") == "documents/report.md":
                actual, report = derive({"documents/report.md": result.encode()})
                assert actual == DRAFT and report == REPORT
                self.recomputed = True

        def generate(self, *args, **kwargs):
            turn = super().generate(*args, **kwargs)
            if turn.tool_call.name in ("handoff", "submit_final"):
                assert self.recomputed
            return turn

    pair, _, _ = run_pair(tmp_path, "branch_and_verify", "many_to_one", backend_class=Recomputing)
    assert pair.clean.trace.labels.task_success and pair.input_disregard.trace.labels.task_success


def test_tool_errors_do_not_leak_arm_specific_host_roots(tmp_path):
    class DirectoryRead(NativeBackend):
        def generate(self, *args, **kwargs):
            turn = super().generate(*args, **kwargs)
            if self.step == 1:
                turn.tool_call.input = {"path": "."}
                self._conversation[-1]["content"] = repr(turn)
            return turn
    pair, audit, _ = run_pair(tmp_path, backend_class=DirectoryRead)
    assert pair.input_disregard.dataset_eligible
    for arm in audit["arms"].values():
        for row in arm["stages"]:
            assert str(tmp_path) not in row["tool_calls"][0]["output"]


def test_no_memory_configuration_is_preserved_in_both_arms(tmp_path):
    pair, audit, _ = run_pair(tmp_path, memory="none")
    assert pair.input_disregard.dataset_eligible
    for arm in audit["arms"].values():
        for row in arm["stages"]:
            assert "read_memory" not in row["reset"]["mcp_tools"]
    assert audit["arms"]["clean"]["source_sha256"] == audit["arms"]["input_disregard"]["source_sha256"]


def test_reusing_backend_between_arms_is_rejected(tmp_path):
    backend = NativeBackend()
    with pytest.raises(ContractError, match="fresh backend"):
        ScenarioRunner(dry_run=False, max_events=700, output_dir=tmp_path).run_result_x_pair(
            spec("branch_and_verify", "single_origin"), FIXTURES, backend_factory=lambda: backend,
            validator=validate, final_path=FINAL, pair_name="pair")
