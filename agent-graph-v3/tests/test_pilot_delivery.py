"""MP / ID delivery pilot switches (generation/pilot_flags.py).

Off by default; these tests check each switch does what it says and that runs
stay admissible (one injection origin, eligible) with the switch on.
"""
from collections import Counter
from pathlib import Path

import pytest

from backend.api_backend import ModelTurn, ToolCall
from generation.handoff import HandoffPayload
from generation.runner import ScenarioRunner
from leps.input_disregard import InputDisregardLEP
from schemas import ScenarioSpec, TraceEventType, WorkflowConfig
from tasks.registry import get_default_leps

FIXTURES = Path(__file__).resolve().parent.parent / "workspace_fixtures"
FIXTURE = "financial_clean"
FAMILY = "financial_analysis"
CLEAN_VALUE = "Q3 revenue was 1,520,000, up 12.6% quarter over quarter."


class ScriptedAgents:
    """Writers read a file, write 'revenue_figures' twice, then hand off.
    Everyone else hands off with findings, or submits when allowed.
    Nobody calls read_memory, so only the snapshot can deliver memory."""

    def __init__(self, source, findings=("Revenue 1,520,000 from q3_summary_v2.md",)):
        self.source = source
        self.findings = list(findings)
        self.visits = Counter()
        self.conversations = {}

    def reset(self, *, agent_name, mcp_tools, system_prompt="", **kwargs):
        self.role = agent_name
        self.visits[agent_name] += 1
        self.tools = mcp_tools
        self.step = 0
        self.writes = 0
        self._conversation = []
        self.conversations.setdefault(agent_name, []).append(self._conversation)

    def _append_tool_result(self, *args, **kwargs):
        pass

    @property
    def _messages(self):
        # Real backends expose this; the runner only injects handoff and
        # snapshot text into _conversation when it exists.
        return self._conversation

    def generate(self, prompt="", tool_choice=None):
        self.step += 1
        repair = any("before handing off. Call `write_memory`" in m.get("content", "")
                     for m in self._conversation)
        wants_write = "write_memory" in self.tools and (
            self.role in ("researcher", "extractor", "specialist_a", "branch_a") or repair)
        if self.step == 1:
            name, args = "read_text_file", {"path": self.source}
        elif wants_write and self.writes < 2:
            self.writes += 1
            name, args = "write_memory", {"key": "revenue_figures", "value": CLEAN_VALUE}
        elif "submit_final" in self.tools and not (
                self.role == "coordinator" and self.visits[self.role] == 1):
            name, args = "submit_final", {"summary": "Q3 revenue 1,520,000."}
        else:
            name, args = "handoff", {"summary": "Figures extracted.", "findings": self.findings}
        return ModelTurn(tool_call=ToolCall(id=f"{self.role}-{self.visits[self.role]}-{self.step}",
                                            name=name, input=args),
                         text="", stop_reason="tool_use")


def run(tmp_path, monkeypatch, *, code, topology, mode="single_origin", env=None):
    for k in ("AG_MP_PERSIST", "AG_MEMORY_SNAPSHOT", "AG_ID_VARIANT"):
        monkeypatch.delenv(k, raising=False)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    source = next(p for p in sorted((FIXTURES / FIXTURE).rglob("*"))
                  if p.is_file() and p.suffix in (".md", ".csv") and "manifest" not in p.name)
    backend = ScriptedAgents(str(source.relative_to(FIXTURES / FIXTURE)))
    leps = [lep for lep in get_default_leps(FAMILY) if lep.code == code] if code else []
    spec = ScenarioSpec(
        scenario_id=f"pilot-{topology}-{mode}-{code}", task_family=FAMILY, task_variant="default",
        fixture_id=FIXTURE, condition="single_lep" if code else "benign", lep_configs=leps,
        workflow_config=WorkflowConfig(topology=topology, propagation_mode=mode,
                                       memory_mode="ephemeral_shared", max_agent_turns=10))
    result = ScenarioRunner(llm_backend=backend, dry_run=False, output_dir=tmp_path).run(spec, FIXTURES)
    assert result.runner_success, result.error
    return result, backend


def writes(result):
    return [e for e in result.trace.events if e.event_type == TraceEventType.MEMORY_WRITE]


def origins(result):
    return [e for e in result.trace.events if e.event_labels.is_injection_origin]


def test_default_second_write_restores_clean_value(tmp_path, monkeypatch):
    result, _ = run(tmp_path, monkeypatch, code="LEP_MEMORY_POISONING", topology="branch_and_verify")
    w = writes(result)
    assert len(origins(result)) == 1
    assert w[0].event_labels.is_injection_origin
    assert w[1].tool_arguments["value"] == CLEAN_VALUE
    assert "mp_repoisoned_from" not in w[1].hidden


def test_persist_keeps_poison_without_new_origin(tmp_path, monkeypatch):
    result, _ = run(tmp_path, monkeypatch, code="LEP_MEMORY_POISONING",
                    topology="branch_and_verify", env={"AG_MP_PERSIST": "1"})
    w = writes(result)
    poison = w[0].tool_arguments["value"]
    assert poison != CLEAN_VALUE
    assert w[1].tool_arguments["value"] == poison
    assert w[1].hidden["mp_repoisoned_from"] == w[0].event_id
    assert len(origins(result)) == 1
    assert result.dataset_eligible, result.trace.metadata


def test_snapshot_delivers_memory_to_receiver(tmp_path, monkeypatch):
    result, backend = run(tmp_path, monkeypatch, code="LEP_MEMORY_POISONING",
                          topology="branch_and_verify",
                          env={"AG_MP_PERSIST": "1", "AG_MEMORY_SNAPSHOT": "1"})
    snaps = [e for e in result.trace.events if e.event_type == TraceEventType.MEMORY_RETRIEVAL
             and e.tool_name == "memory_snapshot"]
    assert snaps and all(e.agent_role == "verifier" for e in snaps)
    poison = writes(result)[0].tool_arguments["value"]
    assert poison[:60] in snaps[0].output_text
    seen = [m["content"] for conv in backend.conversations["verifier"] for m in conv]
    assert any(poison[:60] in c for c in seen)
    assert result.dataset_eligible, result.trace.metadata


def test_snapshot_clean_run_is_admissible(tmp_path, monkeypatch):
    result, _ = run(tmp_path, monkeypatch, code=None, topology="branch_and_verify",
                    env={"AG_MEMORY_SNAPSHOT": "1"})
    assert result.termination_reason == "completed", result.trace.metadata
    assert any(e.tool_name == "memory_snapshot" for e in result.trace.events)


def test_snapshot_makes_one_to_many_coordinator_write(tmp_path, monkeypatch):
    base, _ = run(tmp_path / "a", monkeypatch, code="LEP_MEMORY_POISONING",
                  topology="coordinator_workers", mode="one_to_many")
    piloted, _ = run(tmp_path / "b", monkeypatch, code="LEP_MEMORY_POISONING",
                     topology="coordinator_workers", mode="one_to_many",
                     env={"AG_MEMORY_SNAPSHOT": "1"})
    assert not [e for e in writes(base) if e.agent_role == "coordinator"]
    assert len(origins(base)) == 0   # what the v3 run saw: nothing for MP to hit
    coord = [e for e in writes(piloted) if e.agent_role == "coordinator"]
    assert coord, "coordinator should write memory before dispatching"
    assert len(origins(piloted)) == 1
    assert origins(piloted)[0].agent_role == "coordinator"
    assert piloted.dataset_eligible, piloted.trace.metadata


def test_key_findings_instruction_quotes_findings():
    p = HandoffPayload(from_agent="extractor", to_agent="analyst",
                       findings=["Revenue 1,520,000", "Margin 13.8%"], summary="x")
    text = InputDisregardLEP.key_findings_instruction(p)
    assert '"Revenue 1,520,000"' in text and '"Margin 13.8%"' in text
    assert "extractor" in text
    assert InputDisregardLEP.key_findings_instruction(
        HandoffPayload(from_agent="a", to_agent="b")) == ""


def test_key_findings_variant_reaches_receiver(tmp_path, monkeypatch):
    result, backend = run(tmp_path, monkeypatch, code="LEP_INPUT_DISREGARD",
                          topology="review_loop", env={"AG_ID_VARIANT": "key_findings"})
    o = origins(result)
    assert len(o) == 1
    meta = o[0].hidden["input_disregard"]
    assert meta["disregard_type"] == "key_findings"
    assert "Revenue 1,520,000" in meta["instruction"]
    receiver = o[0].observable.get("handoff_to") or meta["target_agent"]
    seen = [m["content"] for conv in backend.conversations.get(receiver, []) for m in conv]
    assert any("Do not rely on these findings" in c for c in seen), receiver
