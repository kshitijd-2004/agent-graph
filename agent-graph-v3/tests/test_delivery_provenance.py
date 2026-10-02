"""Delivery, completed input processing, and record versions stay distinct."""
import copy
import hashlib
import json
from collections import defaultdict

import pytest
import torch

import test_injection_count_regression as production
from backend.errors import BackendError
from generation.event_graph_builder import DependsOnGraphBuilder
from generation.stage_runner import StageRunner
from generation.topology import get_topology
from memory.memory_store import MemoryRecord, MemoryStore
from generation.feature_schema import OBSERVABLE_NODE_FEATURE_DIM
from schemas import TraceEvent


def test_two_record_exact_match_and_irrelevant_query():
    store = MemoryStore()
    store.add(MemoryRecord(id="a", key="revenue_figures", value="profit rose"))
    store.add(MemoryRecord(id="b", key="acquisition", value="assets fell"))
    assert [r.id for r, _ in store.retrieve("revenue_figures")] == ["a"]
    assert store.retrieve("security vulnerabilities") == []


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("mode,topology", [("single_origin", "branch_and_verify"),
                                          ("one_to_many", "coordinator_workers")])
def test_handoff_records_sent_content_and_processing(tmp_path, monkeypatch, mode, topology, native):
    if native:
        class Native(production.ScriptedFinancialBackend):
            def reset(self, **kwargs):
                super().reset(**kwargs)
                self._conversation = [{"role": "user", "content": kwargs.get("task", "task")}]

            @property
            def _messages(self):
                return self._conversation

            def _append_tool_result(self, tc, content):
                self._conversation.append({"role": "tool", "content": content})

        monkeypatch.setattr(production, "ScriptedFinancialBackend", Native)
    result = production.execute(tmp_path, mode, topology, "LEP_HANDOFF_CORRUPTION")
    assert result.dataset_eligible
    origin = production.origins(result)[0]
    assert hashlib.md5(origin.output_text.encode()).hexdigest()[:12] == origin.hidden["perturbed_hash"]
    events = result.trace.events
    deliveries = [e for e in events if e.event_labels.receives_perturbed_info]
    assert deliveries
    assert all(not e.event_labels.consumes_perturbed_info for e in deliveries)
    consumed = [e for e in events if e.event_labels.consumes_perturbed_info]
    assert consumed
    assert all(e.event_type.value in {"reasoning", "final_response"} for e in consumed)
    assert all(e.hidden["input_processing_evidence"] and e.event_labels.inferred_propagation for e in consumed)
    if mode == "single_origin":
        assert not any(e.agent_role == "analyst" for e in consumed)
        assert any(e.event_type.value == "final_response" for e in consumed)
    else:
        assert {"specialist_a", "specialist_b", "synthesizer"} <= {e.agent_role for e in consumed}


def test_tool_delivery_does_not_taint_generated_handoffs(tmp_path):
    result = production.execute(tmp_path, "single_origin", code=production.TRC)
    origin = production.origins(result)[0]
    assert origin.event_labels.receives_perturbed_info
    assert not origin.event_labels.consumes_perturbed_info
    consumed = [e for e in result.trace.events if e.event_labels.consumes_perturbed_info]
    assert consumed and {e.agent_role for e in consumed} == {origin.agent_role}
    assert all(not e.event_labels.forwards_perturbed_info for e in result.trace.events)


@pytest.mark.parametrize("code", [production.TRC, "LEP_HANDOFF_CORRUPTION"])
def test_delivery_followed_by_backend_failure_is_not_consumption(tmp_path, monkeypatch, code):
    class Failure(production.ScriptedFinancialBackend):
        def reset(self, **kwargs):
            super().reset(**kwargs)
            self.role = kwargs["agent_name"]

        def generate(self, *args, **kwargs):
            if ((code == production.TRC and self.index == 1)
                    or (code == "LEP_HANDOFF_CORRUPTION" and self.role == "verifier")):
                raise BackendError("connection refused", "backend_connection_error")
            return super().generate(*args, **kwargs)
    monkeypatch.setattr(production, "ScriptedFinancialBackend", Failure)
    result = production.execute(tmp_path, "single_origin", code=code)
    assert result.termination_reason == "backend_connection_error"
    assert any(e.event_labels.receives_perturbed_info for e in result.trace.events)
    assert not any(e.event_labels.consumes_perturbed_info for e in result.trace.events)


@pytest.mark.parametrize("overwrite", [False, True])
def test_memory_record_versions_not_ever_poisoned_keys(tmp_path, monkeypatch, overwrite):
    class MemoryReader(production.ScriptedFinancialBackend):
        def reset(self, **kwargs):
            super().reset(**kwargs)
            index = next(i for i, (name, _) in enumerate(self.calls) if name == "write_memory")
            extra = []
            if overwrite:
                extra.append(("write_memory", {"key": "revenue_figures", "value": "Clean corrected financial findings."}))
            extra.append(("read_memory", {"query": "revenue_figures", "top_k": 10}))
            self.calls[index + 1:index + 1] = extra
    monkeypatch.setattr(production, "ScriptedFinancialBackend", MemoryReader)
    result = production.execute(tmp_path, "single_origin", code="LEP_MEMORY_POISONING")
    assert result.dataset_eligible
    origin = production.origins(result)[0]
    assert origin.event_labels.stores_perturbed_info
    deliveries = [e for e in result.trace.events if e.event_labels.receives_perturbed_info]
    if overwrite:
        assert deliveries == []
        assert not any(e.event_labels.consumes_perturbed_info for e in result.trace.events)
    else:
        assert deliveries
        for event in deliveries:
            assert event.hidden["delivered_origins"] == [origin.event_id]
            assert origin.event_id in event.depends_on
        assert any(e.event_labels.consumes_perturbed_info for e in result.trace.events)


def test_unread_poison_is_stored_not_consumed(tmp_path):
    result = production.execute(tmp_path, "single_origin", code="LEP_MEMORY_POISONING")
    assert production.origins(result)[0].event_labels.stores_perturbed_info
    assert not any(e.event_labels.receives_perturbed_info or e.event_labels.consumes_perturbed_info
                   for e in result.trace.events)


def test_same_key_two_poisoned_writers_keep_distinct_origins(tmp_path, monkeypatch):
    class Reader(production.ScriptedFinancialBackend):
        def reset(self, **kwargs):
            super().reset(**kwargs)
            if kwargs["agent_name"] == "verifier":
                self.calls.insert(0, ("read_memory", {"query": "revenue_figures", "top_k": 10}))
    monkeypatch.setattr(production, "ScriptedFinancialBackend", Reader)
    result = production.execute(tmp_path, "many_to_one", code="LEP_MEMORY_POISONING")
    origins = {e.event_id for e in production.origins(result)}
    assert len(origins) == 2
    delivery = next(e for e in result.trace.events if e.event_labels.receives_perturbed_info)
    assert set(delivery.hidden["delivered_origins"]) == origins
    assert set(delivery.depends_on) == origins


def test_exit_fallback_returns_remapped_role():
    topology = get_topology("branch_and_verify", defaultdict(str))
    topology.stage_by_id["verifier"].agent_role = "reviewer"
    assert StageRunner(None)._find_next_agent_role("reviewer", topology) == ["reviewer"]


def test_private_provenance_does_not_change_detector_inputs(tmp_path):
    trace = production.execute(tmp_path, "single_origin", code=production.TRC).trace
    clean = copy.deepcopy(trace)
    for event in clean.events:
        event.hidden.clear()
        for key, value in vars(event.event_labels).items():
            if isinstance(value, bool):
                setattr(event.event_labels, key, False)
    builder = DependsOnGraphBuilder()
    original, stripped = [builder.build(t, "branch_and_verify", "financial_analysis") for t in (trace, clean)]
    assert original.edges == stripped.edges
    assert torch.equal(original.node_features[:, :OBSERVABLE_NODE_FEATURE_DIM],
                       stripped.node_features[:, :OBSERVABLE_NODE_FEATURE_DIM])


@pytest.mark.parametrize("topology,mode", [("branch_and_verify", "many_to_one"),
                                          ("coordinator_workers", "one_to_many")])
def test_disregard_instruction_follows_incoming_edges(tmp_path, topology, mode):
    result = production.execute(tmp_path, mode, topology, "LEP_INPUT_DISREGARD")
    assert result.dataset_eligible
    origins = production.origins(result)
    for origin in origins:
        instruction = origin.hidden["input_disregard"]["instruction"]
        deliveries = [e for e in result.trace.events if origin.event_id in e.hidden.get("delivered_origins", [])]
        assert deliveries
        for delivered in deliveries:
            processed = [e for e in result.trace.events if e.agent_role == delivered.agent_role
                         and any(x["origin_event_id"] == origin.event_id
                                 for x in e.hidden.get("input_processing_evidence", []))]
            assert processed
            assert any(instruction in (e.input_text or "") for e in processed)


@pytest.mark.parametrize("code", [production.TRC, production.IPI, "LEP_HANDOFF_CORRUPTION", "LEP_MEMORY_POISONING", "LEP_INPUT_DISREGARD"])
def test_private_provenance_survives_json_roundtrip(tmp_path, code):
    result = production.execute(tmp_path, "single_origin", code=code)
    for event in result.trace.events:
        restored = TraceEvent.from_dict(json.loads(json.dumps(event.to_dict())))
        assert restored.hidden == event.hidden
        assert restored.event_labels == event.event_labels
        assert restored.depends_on == event.depends_on
        assert restored.output_text == event.output_text


def test_exact_tool_artifact_copy_reaches_recipient(tmp_path, monkeypatch):
    class Copier(production.ScriptedFinancialBackend):
        def reset(self, **kwargs):
            super().reset(**kwargs)
            self.artifact = ""

        def _append_tool_result(self, tc, content):
            if tc.name == "read_text_file" and not self.artifact:
                self.artifact = content

        def generate(self, *args, **kwargs):
            turn = super().generate(*args, **kwargs)
            if turn.tool_call.name == "handoff":
                turn.tool_call.input["summary"] = self.artifact
            return turn
    monkeypatch.setattr(production, "ScriptedFinancialBackend", Copier)
    result = production.execute(tmp_path, "single_origin", code=production.TRC)
    origin = production.origins(result)[0]
    handoff = next(e for e in result.trace.events if e.event_type.value == "agent_handoff")
    assert handoff.hidden["forwarded_origins"] == [origin.event_id]
    assert handoff.hidden["copy_evidence"][0]["basis"] == "exact_artifact_match"
    assert handoff.event_labels.inferred_propagation
    assert any(e.agent_role == "verifier" and e.event_labels.consumes_perturbed_info
               for e in result.trace.events)


def test_retrieval_miss_has_no_delivery(tmp_path, monkeypatch):
    class Reader(production.ScriptedFinancialBackend):
        def reset(self, **kwargs):
            super().reset(**kwargs)
            if kwargs["agent_name"] == "verifier":
                self.calls.insert(0, ("read_memory", {"query": "quasarxyz", "top_k": 3}))
    monkeypatch.setattr(production, "ScriptedFinancialBackend", Reader)
    result = production.execute(tmp_path, "single_origin", code="LEP_MEMORY_POISONING")
    retrieval = next(e for e in result.trace.events if e.event_type.value == "memory_retrieval")
    assert "No memory records" in retrieval.output_text
    assert not retrieval.event_labels.receives_perturbed_info
    assert not any(e.event_labels.consumes_perturbed_info for e in result.trace.events)


def test_poison_beyond_returned_prefix_is_stored_not_delivered(tmp_path, monkeypatch):
    from leps.memory_poisoning import MemoryPoisoningLEP
    clean = "Financial figures checked. " * 20
    original_poison = MemoryPoisoningLEP.poison
    def suffix_poison(self, *args, **kwargs):
        result = original_poison(self, *args, **kwargs)
        result.memory_value = clean + " Forged amount 999999."
        return result
    class Reader(production.ScriptedFinancialBackend):
        def reset(self, **kwargs):
            super().reset(**kwargs)
            for name, args in self.calls:
                if name == "write_memory":
                    args["value"] = clean
            if kwargs["agent_name"] == "verifier":
                self.calls.insert(0, ("read_memory", {"query": "revenue_figures", "top_k": 10}))
    monkeypatch.setattr(MemoryPoisoningLEP, "poison", suffix_poison)
    monkeypatch.setattr(production, "ScriptedFinancialBackend", Reader)
    result = production.execute(tmp_path, "single_origin", code="LEP_MEMORY_POISONING")
    assert production.origins(result)[0].event_labels.stores_perturbed_info
    read = next(e for e in result.trace.events if e.event_type.value == "memory_retrieval")
    assert read.depends_on and "Forged amount" not in read.output_text
    assert not read.event_labels.receives_perturbed_info
    assert not any(e.event_labels.consumes_perturbed_info for e in result.trace.events)


def test_training_loader_preserves_private_provenance(tmp_path):
    # Exercise the actual loader function without importing optional GNN
    # training dependencies (torch_geometric is not installed in this runner).
    import ast
    from pathlib import Path
    from typing import Any
    path = Path(__file__).resolve().parents[1] / "training/pipeline.py"
    tree = ast.parse(path.read_text())
    loader = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef) and node.name == "_dict_to_trace")
    module = ast.Module(body=[loader], type_ignores=[])
    namespace = {"Any": Any}
    exec(compile(module, str(path), "exec"), namespace)
    trace = production.execute(tmp_path, "single_origin", code="LEP_HANDOFF_CORRUPTION").trace
    restored = namespace["_dict_to_trace"](None, json.loads(json.dumps(trace.to_dict())))
    assert restored.labels == trace.labels
    for before, after in zip(trace.events, restored.events):
        assert after.hidden == before.hidden
        assert after.observable == before.observable
        assert after.event_labels == before.event_labels


def test_tool_artifact_stored_in_memory_keeps_origin(tmp_path, monkeypatch):
    original_build = production.ScenarioBuilder.build_single
    def shared_spec(self, config):
        spec = original_build(self, config)
        spec.workflow_config.memory_mode = "ephemeral_shared"
        return spec
    class Writer(production.ScriptedFinancialBackend):
        def reset(self, **kwargs):
            super().reset(**kwargs)
            self.artifact = ""

        def _append_tool_result(self, call, content):
            if call.name == "read_text_file" and not self.artifact:
                self.artifact = content

        def generate(self, *args, **kwargs):
            turn = super().generate(*args, **kwargs)
            if turn.tool_call.name == "write_memory":
                turn.tool_call.input["value"] = self.artifact
            return turn
    monkeypatch.setattr(production.ScenarioBuilder, "build_single", shared_spec)
    monkeypatch.setattr(production, "ScriptedFinancialBackend", Writer)
    result = production.execute(tmp_path, "single_origin", code=production.TRC)
    origin = production.origins(result)[0]
    stored = next(e for e in result.trace.events if e.event_labels.stores_perturbed_info)
    assert stored.hidden["stored_origins"] == [origin.event_id]
    assert stored.event_labels.inferred_propagation
    assert stored.tool_arguments["value"].strip() == origin.output_text.strip()
    assert stored.hidden["copy_evidence"][0]["basis"] == "exact_artifact_match"
