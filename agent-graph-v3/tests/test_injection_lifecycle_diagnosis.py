"""Production-runner regression: configured budgets survive all stage resets.

No model/network calls. Uses the benchmark's family-specific LEP resolver,
real stage execution, tool dispatch, mutation, labeling, and admission.
"""
from pathlib import Path

import pytest

from backend.api_backend import ModelTurn, ToolCall
from benchmark.benchmark_runner import BenchmarkRunner
from generation.runner import ScenarioRunner
from leps.registry import LEPOrchestrator
from schemas import ScenarioSpec, WorkflowConfig


class FinancialBackend:
    def __init__(self, target_reads):
        self.target_reads = target_reads

    def reset(self, *, agent_name, **kwargs):
        role = agent_name
        self.calls = [("read_text_file", {"path": "documents/report.md"})] * self.target_reads
        self.calls += [("read_text_file", {"path": "documents/call_transcript.md"})]
        if role == "verifier":
            self.calls += [("submit_final", {"summary": "Verified the authoritative figures."})]
        else:
            report = f"output/{role}.md"
            self.calls += [
                ("write_file", {"path": report, "content": "Independent financial analysis."}),
                ("handoff", {"target_agent": "verifier", "summary": "Independent analysis complete.",
                             "report_path": report, "source_paths": ["documents/report.md",
                                                                      "documents/call_transcript.md"]}),
            ]
        self.index = 0

    def generate(self, prompt="", tool_choice=None):
        name, args = self.calls[self.index]
        self.index += 1
        return ModelTurn(tool_call=ToolCall(id=f"call-{self.index}", name=name, input=args),
                         text="", stop_reason="tool_use")

    def _append_tool_result(self, *args, **kwargs):
        pass


@pytest.mark.parametrize("code,reads,actual", [
    ("LEP_INDIRECT_PROMPT_INJECTION", 1, 2),
    ("LEP_INDIRECT_PROMPT_INJECTION", 2, 2),
    ("LEP_TOOL_RESULT_CORRUPTION", 1, 2),
])
def test_real_runner_budget_and_occurrence(tmp_path, monkeypatch, code, reads, actual):
    configurations = []
    evaluations = []
    configure = LEPOrchestrator.set_max_origins
    evaluate = LEPOrchestrator.evaluate_for_boundary

    def record_configuration(self, lep_code, count):
        configurations.append((id(self), lep_code, count))
        return configure(self, lep_code, count)

    def check_configuration(self, event, tool_result=""):
        evaluations.append(id(self))
        assert self.get_firing_state(code).max_origins == 2
        return evaluate(self, event, tool_result)

    monkeypatch.setattr(LEPOrchestrator, "set_max_origins", record_configuration)
    monkeypatch.setattr(LEPOrchestrator, "evaluate_for_boundary", check_configuration)
    # _resolve_lep does not read runner state; use exactly the production resolver.
    lep = BenchmarkRunner._resolve_lep(None, code, "financial_analysis")
    spec = ScenarioSpec(scenario_id="lifecycle-diagnosis", task_family="financial_analysis",
                        task_variant="finqa", fixture_id="fin_finqa_052_msi_2006",
                        condition="single_lep", lep_configs=[lep],
                        workflow_config=WorkflowConfig(topology="branch_and_verify",
                            propagation_mode="many_to_one", max_agent_turns=10))
    root = Path(__file__).resolve().parents[1] / "workspace_fixtures"
    result = ScenarioRunner(llm_backend=FinancialBackend(reads), dry_run=False,
                            output_dir=tmp_path).run(spec, root)
    assert result.runner_success, result.error
    assert result.trace.metadata["expected_injection_origins"] == 2
    assert result.trace.injection_origin_count == actual
    assert result.dataset_eligible == (actual == 2)
    assert result.termination_reason == ("completed" if actual == 2 else "injection_count_mismatch")
    assert len(configurations) == 1
    assert set(evaluations) == {configurations[0][0]}
    origins = [e.agent_role for e in result.trace.events if e.event_labels.is_injection_origin]
    assert origins == (["analyst"] if actual == 1 else ["researcher", "analyst"])
