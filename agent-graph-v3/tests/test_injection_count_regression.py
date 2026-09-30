"""Origin identity/order through production config, tools, routing and admission."""
from collections import Counter, defaultdict
from pathlib import Path
import pytest
from backend.api_backend import ModelTurn, ToolCall
from benchmark.benchmark_runner import BenchmarkRunner, BenchmarkManifest
from generation.runner import ScenarioRunner
from generation.scenario_builder import ScenarioBuilder, ScenarioBuildConfig
from generation.injection_origins import intended_origin_roles
from generation.topology import get_topology
from leps.registry import LEPOrchestrator
from schemas import TraceEvent, TraceEventType
from schemas.trigger_matcher import TriggerMatcher
from schemas.triggers import InjectionTrigger

IPI = "LEP_INDIRECT_PROMPT_INJECTION"
TRC = "LEP_TOOL_RESULT_CORRUPTION"
CODES = [IPI, TRC, "LEP_MEMORY_POISONING", "LEP_HANDOFF_CORRUPTION", "LEP_INPUT_DISREGARD"]
FIXTURES = Path(__file__).resolve().parents[1] / "workspace_fixtures"


class ScriptedFinancialBackend:
    def __init__(self, topology, missing=(), late=False, overwrite=False):
        self.topology, self.missing, self.late, self.overwrite = topology, set(missing), late, overwrite
        self.visits = Counter()

    def reset(self, *, agent_name, mcp_tools, **kwargs):
        role = agent_name
        self.visits[role] += 1
        visit = self.visits[role]
        read = role not in self.missing and not (role == "coordinator" and visit == 1 and self.late)
        self.calls = [("read_text_file", {"path": "documents/report.md"})] * (2 if read else 0)
        self.calls += [("read_text_file", {"path": "documents/call_transcript.md"})]
        if "write_memory" in mcp_tools:
            self.calls += [("write_memory", {"key": "revenue_figures", "value": "Financial figures from report."})]
        final = ((self.topology == "branch_and_verify" and role == "verifier")
                 or (role == "coordinator" and visit > 1)
                 or (self.topology == "review_loop" and role == "analyst" and visit > 1))
        if final:
            self.calls += [("submit_final", {"summary": "Verified authoritative financial figures."})]
        else:
            report = "documents/report.md" if self.overwrite and role == "researcher" else f"output/{role}.md"
            self.calls += [("write_file", {"path": report, "content": "Financial analysis complete."}),
                ("handoff", {"summary": "Independent financial analysis complete.", "report_path": report,
                             "source_paths": ["documents/report.md", "documents/call_transcript.md"]})]
        self.index = 0

    def generate(self, prompt="", tool_choice=None):
        name, args = self.calls[self.index]
        self.index += 1
        return ModelTurn(tool_call=ToolCall(id=f"call-{self.index}", name=name, input=args),
                         text="", stop_reason="tool_use")

    def _append_tool_result(self, *args, **kwargs):
        pass


def execute(tmp_path, mode="many_to_one", topology="branch_and_verify", code=IPI,
            missing=(), late=False, overwrite=False, alter_trace=None, target=None):
    lep = BenchmarkRunner._resolve_lep(None, code, "financial_analysis") if code else None
    if lep and target:
        lep.topology_target = target
    spec = ScenarioBuilder().build_single(ScenarioBuildConfig(
        task_family="financial_analysis", fixture_id="fin_finqa_052_msi_2006", task_variant="finqa",
        topology=topology, propagation_mode=mode, condition="single_lep" if code else "benign",
        lep_configs=[lep] if lep else [], max_agent_turns=12,
        memory_mode="persistent_shared" if code == "LEP_MEMORY_POISONING" else "ephemeral_private"))
    backend = ScriptedFinancialBackend(topology, missing, late, overwrite)
    runner = ScenarioRunner(llm_backend=backend, dry_run=False, output_dir=tmp_path)
    if alter_trace:
        original = runner._execute_scenario
        def altered(*args):
            trace = original(*args)
            alter_trace(trace)
            return trace
        runner._execute_scenario = altered
    result = runner.run(spec, FIXTURES)
    assert result.runner_success, result.error
    return result


def origins(result):
    return [e for e in result.trace.events if e.event_labels.is_injection_origin]


@pytest.mark.parametrize("code", CODES)
@pytest.mark.parametrize("topology,mode", [("branch_and_verify", "many_to_one"),
    ("branch_and_verify", "single_origin"), ("coordinator_workers", "one_to_many"),
    ("coordinator_workers", "single_origin"), ("review_loop", "single_origin")])
def test_supported_production_paths(tmp_path, code, topology, mode):
    result = execute(tmp_path, mode, topology, code)
    assert result.dataset_eligible, result.trace.metadata
    events = origins(result)
    assert len(events) == result.trace.metadata["expected_injection_origins"] == (2 if mode == "many_to_one" else 1)
    if mode == "many_to_one":
        assert {e.agent_role for e in events} == {"researcher", "analyst"}
    if mode == "one_to_many":
        fanout = next(e.event_index for e in result.trace.events
                      if e.event_type == TraceEventType.AGENT_HANDOFF and e.agent_role == "coordinator")
        assert events[0].agent_role == "coordinator"
        assert events[0].event_index <= fanout
        if code == IPI:
            assert events[0].event_index < fanout
    if mode == "single_origin" and code == IPI:
        role = {"review_loop": "extractor", "branch_and_verify": "researcher", "coordinator_workers": "specialist_a"}[topology]
        assert events[0].agent_role == role
        first = next(e for e in result.trace.events if e.agent_role == role
                     and e.event_type == TraceEventType.TOOL_RESULT and e.tool_name == "read_text_file"
                     and e.tool_arguments["path"] == "documents/report.md")
        assert events[0] is first


@pytest.mark.parametrize("mode,topology,missing", [("many_to_one", "branch_and_verify", "analyst"),
    ("single_origin", "branch_and_verify", "researcher"), ("single_origin", "coordinator_workers", "specialist_a")])
def test_missing_intended_target_stays_ineligible(tmp_path, mode, topology, missing):
    result = execute(tmp_path, mode, topology, missing=[missing])
    assert not result.dataset_eligible
    assert result.termination_reason == "injection_count_mismatch"
    assert len(origins(result)) == (1 if mode == "many_to_one" else 0)


def test_post_fanout_reads_cannot_inject(tmp_path):
    result = execute(tmp_path, "one_to_many", "coordinator_workers", late=True)
    assert not result.dataset_eligible
    assert not origins(result)


@pytest.mark.parametrize("wrong_role", ["researcher", "verifier"])
def test_admission_rejects_count_correct_wrong_branch_structure(tmp_path, wrong_role):
    def alter(trace):
        events = [e for e in trace.events if e.event_labels.is_injection_origin]
        events[1].agent_role = wrong_role
    result = execute(tmp_path, alter_trace=alter)
    assert result.trace.injection_origin_count == 2
    assert not result.dataset_eligible
    assert result.termination_reason == "injection_structure_mismatch"


def test_admission_rejects_late_origin_even_with_correct_count_and_role(tmp_path):
    def alter(trace):
        old = next(e for e in trace.events if e.event_labels.is_injection_origin)
        late = next(e for e in reversed(trace.events) if e.agent_role == "coordinator"
                    and e.event_type == TraceEventType.TOOL_RESULT and e.tool_name == "read_text_file")
        old.event_labels.is_injection_origin = False
        late.event_labels.is_injection_origin = True
        late.hidden = dict(old.hidden)
    result = execute(tmp_path, "one_to_many", "coordinator_workers", alter_trace=alter)
    assert len(origins(result)) == 1
    assert not result.dataset_eligible
    assert result.termination_reason == "injection_structure_mismatch"


@pytest.mark.parametrize("missing,overwrite", [(["analyst"], False), ([], True)])
def test_trc_incomplete_and_noop_remain_rejected(tmp_path, missing, overwrite):
    result = execute(tmp_path, code=TRC, missing=missing, overwrite=overwrite)
    assert len(origins(result)) == 1
    assert not result.dataset_eligible
    assert result.termination_reason == "injection_count_mismatch"


@pytest.mark.parametrize("mode,topology", [("single_origin", "review_loop"),
    ("many_to_one", "branch_and_verify"), ("one_to_many", "coordinator_workers")])
def test_benign_unchanged(tmp_path, mode, topology):
    result = execute(tmp_path, mode, topology, code=None)
    assert result.dataset_eligible
    assert not origins(result)
    assert "origin_validation" not in result.trace.metadata


def test_missing_state_fails_before_inference(tmp_path, monkeypatch):
    monkeypatch.setattr(LEPOrchestrator, "set_max_origins", lambda *args: None)
    monkeypatch.setattr(ScriptedFinancialBackend, "generate", lambda *args, **kwargs: pytest.fail("inference started"))
    with pytest.raises(AssertionError, match="Unconfigured LEP firing state"):
        execute(tmp_path)


def test_impossible_handoff_origin_fails_before_inference(tmp_path, monkeypatch):
    monkeypatch.setattr(ScriptedFinancialBackend, "generate", lambda *args, **kwargs: pytest.fail("inference started"))
    with pytest.raises(AssertionError, match="configured origin cannot emit a handoff"):
        execute(tmp_path, mode="single_origin", code="LEP_HANDOFF_CORRUPTION", target="branch:verifier")


def test_reset_does_not_reuse_counts_or_configuration():
    orch = LEPOrchestrator()
    orch.register_lep(BenchmarkRunner._resolve_lep(None, IPI, "financial_analysis"))
    orch.set_max_origins(IPI, 1)
    orch.reset()
    assert orch.get_firing_state(IPI) is None
    with pytest.raises(ValueError, match="Unconfigured"):
        orch.mark_fired_origin(IPI)


def test_remapped_exit_is_resolved_by_stage_identity():
    topology = get_topology("branch_and_verify", defaultdict(str))
    topology.stages[2].agent_role = "custom_exit"
    for rule in topology.handoff_rules:
        rule.to_stage = "custom_exit"
    cfg = BenchmarkRunner._resolve_lep(None, IPI, "financial_analysis")
    assert intended_origin_roles(cfg, topology, "many_to_one") == {"researcher", "analyst"}


def test_scoped_occurrences_are_independent():
    event = TraceEvent(trace_id="t", event_id="0", event_index=0, timestamp="", event_type=TraceEventType.TOOL_RESULT)
    matcher = TriggerMatcher()
    trigger = InjectionTrigger(occurrence=2)
    assert not matcher.evaluate("ipi", trigger, event, 0, scope="a").fired
    assert not matcher.evaluate("ipi", trigger, event, 0, scope="b").fired
    assert matcher.evaluate("ipi", trigger, event, 0, scope="a").fired


@pytest.mark.parametrize("missing", [(), ("analyst",)])
def test_explicit_single_origin_overrides_default_without_fallback(tmp_path, missing):
    result = execute(tmp_path, mode="single_origin", target="branch:analyst", missing=missing)
    assert result.dataset_eligible == (not missing)
    assert [e.agent_role for e in origins(result)] == ([] if missing else ["analyst"])


@pytest.mark.parametrize("topology,mode,mapping", [
    ("branch_and_verify", "many_to_one", {"alpha": "researcher", "beta": "analyst", "omega": "verifier"}),
    ("coordinator_workers", "one_to_many", {"lead": "coordinator", "alpha": "specialist_a",
                                            "beta": "specialist_b", "omega": "synthesizer"}),
])
def test_real_runner_with_remapped_exit_identity(tmp_path, monkeypatch, topology, mode, mapping):
    from generation.scenario_builder import TASK_CONFIGS
    monkeypatch.setitem(TASK_CONFIGS, "financial_analysis", {
        **TASK_CONFIGS["financial_analysis"], "default_agents": list(mapping)})
    original = ScriptedFinancialBackend.reset
    def renamed(self, *, agent_name, **kwargs):
        original(self, agent_name=mapping[agent_name], **kwargs)
    monkeypatch.setattr(ScriptedFinancialBackend, "reset", renamed)
    result = execute(tmp_path, mode, topology, target="upstream:coordinator" if mode == "one_to_many" else None)
    assert result.dataset_eligible, result.trace.metadata
    assert [e.agent_role for e in origins(result)] == (["alpha", "beta"] if mode == "many_to_one" else ["lead"])


def test_one_to_many_workers_receive_fanout_after_upstream_origin(tmp_path):
    result = execute(tmp_path, "one_to_many", "coordinator_workers")
    transitions = [e for e in result.trace.events if e.event_type == TraceEventType.TOPOLOGY_TRANSITION]
    fanout = next(e for e in result.trace.events if e.event_type == TraceEventType.AGENT_HANDOFF
                  and e.agent_role == "coordinator")
    assert origins(result)[0].event_index < fanout.event_index
    assert {e.agent_role for e in transitions if e.event_index > fanout.event_index} >= {
        "specialist_a", "specialist_b", "synthesizer"}


def test_admission_requires_actual_fanout_even_with_one_origin(tmp_path):
    def alter(trace):
        for event in trace.events:
            if event.event_type == TraceEventType.AGENT_HANDOFF and event.agent_role == "coordinator":
                event.event_type = TraceEventType.REASONING
    result = execute(tmp_path, "one_to_many", "coordinator_workers", alter_trace=alter)
    assert len(origins(result)) == 1
    assert not result.dataset_eligible
    assert result.termination_reason == "injection_structure_mismatch"


def test_coordinator_plan_excludes_many_to_one():
    from tasks.registry import get_default_leps
    manifest = BenchmarkManifest(task_families=["financial_analysis"],
        fixture_ids={"financial_analysis": ["fin_finqa_052_msi_2006"]},
        topologies=["coordinator_workers"], lep_configs=get_default_leps("financial_analysis"), fixture_root=FIXTURES)
    plan = manifest.build_plan()
    assert {entry["propagation_mode"] for entry in plan} == {"single_origin", "one_to_many"}


def test_admission_metadata_does_not_change_graph_features(tmp_path):
    import torch
    from generation.event_graph_builder import DependsOnGraphBuilder
    result = execute(tmp_path)
    trace = result.trace
    builder = DependsOnGraphBuilder()
    before = builder.build(trace, topology_name="branch_and_verify",
                           task_family="financial_analysis", strict=False)
    trace.metadata["origin_validation"] = {
        "valid": False, "errors": ["LEP_INDIRECT_PROMPT_INJECTION: invalid origin"]}
    after = builder.build(trace, topology_name="branch_and_verify",
                          task_family="financial_analysis", strict=False)
    assert torch.equal(before.node_features, after.node_features)
    assert torch.equal(before.edge_features, after.edge_features)
    assert before.edges == after.edges
    assert all("origin_validation" not in event.observable for event in trace.events)
