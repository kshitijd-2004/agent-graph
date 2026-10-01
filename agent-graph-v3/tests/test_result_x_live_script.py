"""Plan and wiring tests only: never execute the live seven-pair matrix."""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import run_result_x_live_validation as live


def test_exact_seven_pair_matrix():
    expected = [
        ("financial_analysis", "review_loop", "single_origin", "extractor", 1, None),
        ("code_review", "review_loop", "single_origin", "reviewer", 1, None),
        ("financial_analysis", "review_loop", "single_origin", "extractor", 2, None),
        ("research_synthesis", "branch_and_verify", "single_origin", None, 1, "branch:synthesizer"),
        ("financial_analysis", "branch_and_verify", "many_to_one", None, 1, None),
        ("code_review", "coordinator_workers", "single_origin", None, 1, "worker:specialist_b"),
        ("research_synthesis", "coordinator_workers", "one_to_many", None, 1, "upstream:coordinator"),
    ]
    assert [(p.family, p.topology, p.mode, p.source_agent, p.occurrence, p.topology_target) for p in live.PAIRS] == expected
    assert len({p.name for p in live.PAIRS}) == 7
    fixtures = {"financial_analysis": "fin_finqa_052_msi_2006", "code_review": "code_review_swe_003_django_django_11880",
                "research_synthesis": "research_conflicting"}
    finals = {"financial_analysis": "output/financial_summary.md", "code_review": "output/code_review_report.md",
              "research_synthesis": "output/research_synthesis.md"}
    for p in live.PAIRS:
        assert p.fixture == fixtures[p.family] and p.final_path == finals[p.family]
        scenario = live.scenario_for(p, "served_model", 0.23)
        cfg = scenario.workflow_config
        assert cfg.memory_mode == "ephemeral_private"
        assert cfg.max_events == 300 and cfg.max_agent_turns == 40
        assert cfg.model_name == "served_model" and cfg.temperature == 0.23
        assert scenario.lep_configs[0].trigger.source_agent == p.source_agent
        assert scenario.lep_configs[0].trigger.occurrence == p.occurrence
        assert scenario.lep_configs[0].topology_target == p.topology_target
    # Source-filter edits must not pollute the registry for a subsequent pair.
    assert live.scenario_for(live.PAIRS[0], "m", 0.4).lep_configs[0].trigger.occurrence == 1


def test_default_only_prints_plan_without_backends_files_or_execution(monkeypatch, capsys, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("Default plan must not create a backend or run pairs")
    monkeypatch.setattr(live, "HFBackend", forbidden)
    monkeypatch.setattr(live, "ScenarioRunner", forbidden)
    output = tmp_path / "unused"
    assert live.main(["--output", str(output)]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["will_execute"] is False and len(plan["pairs"]) == 7
    assert not output.exists()


def test_backend_factory_fixes_explicit_decoding_settings_across_arms(monkeypatch):
    monkeypatch.setenv("LLM_VLLM_TEMPERATURE", "1.5")
    monkeypatch.setenv("LLM_VLLM_MAX_TOKENS", "12")
    factory = live.backend_factory("fixed_model", "http://unused.invalid/v1", 0.4, 4096)
    a, b = factory(), factory()
    assert a is not b
    assert (a.model, a.base_url, a.temperature, a.max_tokens) == (b.model, b.base_url, b.temperature, b.max_tokens)
    assert a.temperature == 0.4 and a.max_tokens == 4096


@pytest.mark.parametrize("args", [["--run"], ["--run", "--model", "x", "--base-url", "http://unused/v1"]])
def test_execution_requires_explicit_configuration(args):
    with pytest.raises(SystemExit):
        live.main(args)


@pytest.mark.parametrize("fail_first", [False, True])
def test_wiring_preserves_artifacts_and_reports_failure_without_retries(tmp_path, monkeypatch, fail_first):
    calls = []
    class StubRunner:
        def __init__(self, **kwargs):
            assert kwargs["max_events"] == 300 and kwargs["dry_run"] is False
            self.output = kwargs["output_dir"]

        def run_result_x_pair(self, scenario, fixture_root, **kwargs):
            calls.append((scenario, kwargs))
            assert callable(kwargs["validator"]) and callable(kwargs["backend_factory"])
            folder = self.output / kwargs["pair_name"]
            folder.mkdir()
            # Clearly artificial artifact sentinels; no stages or model calls.
            (folder / "clean_trace.json").write_text('{"unit_test_sentinel": "clean"}')
            (folder / "result_x_audit.json").write_text('{"arms": {}}')
            if fail_first and len(calls) == 1:
                raise ValueError("simulated clean admission rejection")
            (folder / "input_disregard_trace.json").write_text('{"unit_test_sentinel": "ID"}')
            return SimpleNamespace(clean=SimpleNamespace(dataset_eligible=True), input_disregard=SimpleNamespace(dataset_eligible=True))

    def no_model(*args, **kwargs):
        pytest.fail("Wiring tests must never create model clients")
    monkeypatch.setattr(live, "ScenarioRunner", StubRunner)
    monkeypatch.setattr(live, "HFBackend", no_model)
    output = tmp_path / "plan_wiring"
    args = argparse.Namespace(output=output, model="unused", review_model=None, base_url="http://unused.invalid/v1",
                              temperature=0.4, max_tokens=4096, review_max_tokens=4096, fixture_root=tmp_path)
    assert live.run(args) == (1 if fail_first else 0)
    assert len(calls) == 7
    assert [call[1]["pair_name"] for call in calls] == [p.name for p in live.PAIRS]
    summary = json.loads((output / "summary.json").read_text())
    assert all(row["review_model_calls"] == 0 for row in summary["pairs"])
    for i, pair in enumerate(live.PAIRS):
        assert json.loads((output / pair.name / "clean_trace.json").read_text()) == {"unit_test_sentinel": "clean"}
        if fail_first and i == 0:
            assert summary["pairs"][i]["status"] == "failed"
            assert summary["pairs"][i]["missing_artifacts"] == ["input_disregard_trace.json"]
        else:
            assert (output / pair.name / "input_disregard_trace.json").exists()


def test_existing_output_is_refused_without_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "run", lambda _: pytest.fail("Must not overwrite an existing run"))
    with pytest.raises(SystemExit):
        live.main(["--run", "--base-url", "http://unused/v1", "--model", "unused", "--output", str(tmp_path)])
