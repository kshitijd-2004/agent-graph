"""Fan-in workers must each offer a genuine memory-write injection boundary."""
from collections import Counter
from pathlib import Path

import pytest

from backend.api_backend import ModelTurn, ToolCall
from benchmark.benchmark_runner import BenchmarkManifest
from generation.injection_origins import expected_injection_origins
from generation.runner import ScenarioRunner
from schemas import ScenarioSpec, TraceEventType, WorkflowConfig
from tasks.registry import get_default_leps


FIXTURES = Path(__file__).resolve().parent.parent / "workspace_fixtures"
CASES = [("code_review", "code_review_easy"),
         ("code_review", "code_review_conflicting"),
         ("financial_analysis", "financial_clean"),
         ("financial_analysis", "financial_version_conflict"),
         ("research_synthesis", "research_conflicting")]
MEMORY = "LEP_MEMORY_POISONING"


class ReluctantMemoryWriter:
    """A reader writes only after the production handoff guard requests it."""
    def __init__(self, source, writes_per_worker=2):
        self.source = source
        self.writes_per_worker = writes_per_worker
        self.prompts = {}

    def reset(self, *, agent_name, mcp_tools, system_prompt="", **kwargs):
        self.role = agent_name
        self.tools = mcp_tools
        self.step = self.writes = 0
        self._conversation = []
        self.prompts[agent_name] = system_prompt

    def _append_tool_result(self, *args, **kwargs):
        pass

    def generate(self, prompt="", tool_choice=None):
        self.step += 1
        repair = any("before handing off. Call `write_memory`" in m.get("content", "")
                     for m in self._conversation)
        if self.step == 1:
            name, args = "read_text_file", {"path": self.source}
        elif repair and self.writes < self.writes_per_worker:
            self.writes += 1
            name, args = "write_memory", {
                "key": f"{self.role}_findings_{self.writes}",
                "value": "Findings independently checked against source documents.",
            }
        elif "submit_final" in self.tools:
            name, args = "submit_final", {"summary": "Independent review complete."}
        else:
            name, args = "handoff", {"summary": "Source findings ready for verification."}
        return ModelTurn(tool_call=ToolCall(id=f"{self.role}-{self.step}", name=name, input=args),
                         text="", stop_reason="tool_use")


def execute(tmp_path, family, fixture, mode="many_to_one", code=MEMORY,
            topology="branch_and_verify", writes_per_worker=2):
    source = next(p for p in sorted((FIXTURES / fixture).rglob("*"))
                  if p.is_file() and p.suffix in (".md", ".py", ".csv")
                  and "manifest" not in p.name)
    backend = ReluctantMemoryWriter(
        str(source.relative_to(FIXTURES / fixture)), writes_per_worker=writes_per_worker)
    leps = [lep for lep in get_default_leps(family) if lep.code == code] if code else []
    spec = ScenarioSpec(scenario_id=f"{fixture}-{mode}-{code}", task_family=family,
        task_variant="default", fixture_id=fixture, condition="single_lep" if code else "benign",
        lep_configs=leps, workflow_config=WorkflowConfig(topology=topology,
            propagation_mode=mode, memory_mode="ephemeral_shared", max_agent_turns=10))
    result = ScenarioRunner(llm_backend=backend, dry_run=False, output_dir=tmp_path).run(spec, FIXTURES)
    assert result.runner_success, result.error
    assert result.termination_reason == "completed", result.trace.metadata
    assert result.dataset_eligible, result.trace.metadata
    origins = [e for e in result.trace.events if e.event_labels.is_injection_origin]
    expected = expected_injection_origins(condition=spec.condition,
        lep_codes=[code] if code else [], propagation_mode=mode, topology=topology)
    assert len(origins) == expected
    assert result.trace.metadata["actual_injection_origins"] == expected
    assert result.trace.metadata["expected_injection_origins"] == expected
    return result.trace, origins, backend


@pytest.mark.parametrize("family,fixture", CASES)
def test_each_fan_in_worker_fires_once_and_is_admitted(tmp_path, family, fixture):
    trace, origins, backend = execute(tmp_path, family, fixture)
    assert len(origins) == len({e.event_id for e in origins}) == 2
    assert len({e.agent_role for e in origins}) == 2
    assert all(e.event_type == TraceEventType.MEMORY_WRITE for e in origins)
    writes = Counter(e.agent_role for e in trace.events if e.event_type == TraceEventType.MEMORY_WRITE)
    assert writes == {e.agent_role: 2 for e in origins}
    for origin in origins:
        assert "store your own findings" in backend.prompts[origin.agent_role]
    assert trace.metadata["termination_reason"] != "injection_count_mismatch"


@pytest.mark.parametrize("mode,topology", [("single_origin", "branch_and_verify"),
                                          ("one_to_many", "coordinator_workers")])
def test_other_memory_modes_still_fire_once(tmp_path, mode, topology):
    _, origins, backend = execute(tmp_path, *CASES[0], mode=mode, topology=topology)
    assert len(origins) == 1
    assert all("store your own findings" not in prompt for prompt in backend.prompts.values())


def test_other_lep_keeps_distinct_worker_origins(tmp_path):
    _, origins, _ = execute(tmp_path, *CASES[0], code="LEP_HANDOFF_CORRUPTION")
    assert len({e.agent_role for e in origins}) == 2
    assert all(e.event_type == TraceEventType.AGENT_HANDOFF for e in origins)


def test_clean_fan_in_uses_same_memory_contract_without_injection(tmp_path):
    trace, origins, _ = execute(tmp_path, *CASES[0], code=None)
    assert not origins
    writes = Counter(e.agent_role for e in trace.events if e.event_type == TraceEventType.MEMORY_WRITE)
    assert sorted(writes.values()) == [2, 2]


def test_selective_regeneration_plan_has_only_five_memory_cells():
    # Invoke the CLI separately per family: its cross-family LEP-code dedup
    # otherwise retains only the first family's task-specific configuration.
    plan = []
    for family in dict.fromkeys(family for family, _ in CASES):
        manifest = BenchmarkManifest(topologies=["branch_and_verify"],
            task_families=[family],
            fixture_ids={family: [fixture for f, fixture in CASES if f == family]},
            lep_configs=[lep for lep in get_default_leps(family) if lep.code == MEMORY],
            propagation_modes=["many_to_one"], num_repetitions=1, num_benign_repetitions=0,
            fixture_root=FIXTURES)
        plan.extend(manifest.build_plan())
    assert len(plan) == 5
    assert {entry["fixture_id"] for entry in plan} == {fixture for _, fixture in CASES}
    assert all(entry["lep_codes"] == [MEMORY] and not entry["is_baseline"]
               and entry["execution_variant"] == "memory_enabled" for entry in plan)


def test_research_fan_in_injects_with_one_write_per_worker(tmp_path):
    trace, origins, _ = execute(
        tmp_path, "research_synthesis", "research_conflicting", writes_per_worker=1)
    writes = [e for e in trace.events if e.event_type == TraceEventType.MEMORY_WRITE]
    assert Counter(e.agent_role for e in writes) == {"researcher": 1, "synthesizer": 1}
    assert {e.event_id for e in origins} == {e.event_id for e in writes}
