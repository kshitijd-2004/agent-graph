"""Deterministic benchmark planning; no inference or external services."""

from dataclasses import replace
from pathlib import Path

import pytest

from benchmark.benchmark_runner import (
    BenchmarkManifest,
    BenchmarkRunner,
    fixture_id_for_task_family,
)
from generation.runner import RunResult, ScenarioRunner
from schemas import Trace, TraceVariant
from schemas.scenario import TOPOLOGY_PROPAGATION_MODES
from tasks.registry import get_default_leps


FIXTURES = {
    "code_review": "code_review_easy",
    "financial_analysis": "financial_clean",
    "research_synthesis": "research_conflicting",
}


def manifest_for(family="financial_analysis", **kwargs):
    return BenchmarkManifest(
        task_families=[family],
        topologies=list(TOPOLOGY_PROPAGATION_MODES),
        lep_configs=get_default_leps(family),
        num_repetitions=5,
        **kwargs,
    )


@pytest.mark.parametrize("family,fixture", FIXTURES.items())
def test_plan_has_explicit_fixture_identity_and_unique_trace_ids(family, fixture):
    plan = manifest_for(family).build_plan()
    assert plan
    assert fixture_id_for_task_family(family) == fixture
    assert {entry["fixture_id"] for entry in plan} == {fixture}
    trace_ids = [BenchmarkRunner._short_trace_id(entry) for entry in plan]
    assert len(trace_ids) == len(set(trace_ids))
    assert all(trace_id.startswith(fixture + ".") and "unknown" not in trace_id for trace_id in trace_ids)
    assert len({entry["run_id"] for entry in plan}) == len(plan)
    assert len({entry["scenario_id"] for entry in plan}) == len(plan)


@pytest.mark.parametrize("family", FIXTURES)
def test_separate_five_run_pools_and_matching_pair_tags(family):
    plan = manifest_for(family).build_plan()
    benign = {entry["pair_tag"]: entry for entry in plan if entry["condition"] == "benign"}
    for topology in TOPOLOGY_PROPAGATION_MODES:
        for variant in ("standard", "memory_enabled"):
            pool = [entry for entry in benign.values()
                    if entry["topology"] == topology and entry["execution_variant"] == variant]
            assert len(pool) == 5
            assert {entry["repetition_index"] for entry in pool} == set(range(5))
    for entry in plan:
        if entry["condition"] == "benign":
            continue
        variant = "memory_enabled" if entry["lep_code"] == "LEP_MEMORY_POISONING" else "standard"
        assert entry["execution_variant"] == variant
        matched = benign[entry["pair_tag"]]
        for key in ("fixture_id", "topology", "execution_variant", "repetition_index"):
            assert entry[key] == matched[key]


@pytest.mark.parametrize("requested", [
    ["single_origin"], ["one_to_many"], ["many_to_one"],
    ["single_origin", "many_to_one"], [], ["unsupported"],
    ["single_origin", "single_origin"],
])
def test_modes_are_exactly_requested_supported_intersection(requested):
    plan = manifest_for(propagation_modes=requested).build_plan()
    for topology, supported in TOPOLOGY_PROPAGATION_MODES.items():
        expected = set(requested) & set(supported)
        entries = [entry for entry in plan if entry["topology"] == topology]
        assert {entry["propagation_mode"] for entry in entries} == expected
        lep_entries = [entry for entry in entries if entry["condition"] == "single_lep"]
        assert {entry["propagation_mode"] for entry in lep_entries} == expected
        if not expected:
            assert not entries
    assert len({entry["scenario_id"] for entry in plan}) == len(plan)


def test_memory_pool_is_only_created_for_families_with_memory_leps():
    non_memory = next(lep for lep in get_default_leps("code_review")
                      if lep.code == "LEP_TOOL_RESULT_CORRUPTION")
    memory = next(lep for lep in get_default_leps("financial_analysis")
                  if lep.code == "LEP_MEMORY_POISONING")
    manifest = BenchmarkManifest(
        task_families=["code_review", "financial_analysis"], topologies=["review_loop"],
        lep_configs=[replace(non_memory, task_family="code_review"),
                     replace(memory, task_family="financial_analysis")],
        num_repetitions=5, propagation_modes=["single_origin"],
    )
    plan = manifest.build_plan()
    assert {e["execution_variant"] for e in plan if e["task_family"] == "code_review"} == {"standard"}
    assert {e["execution_variant"] for e in plan if e["task_family"] == "financial_analysis"} == {
        "standard", "memory_enabled"}


@pytest.mark.parametrize("explicit_fixture", [True, False])
def test_execute_uses_planned_fixture_or_shared_legacy_fallback(tmp_path, monkeypatch, explicit_fixture):
    manifest = manifest_for(output_dir=tmp_path, fixture_root=tmp_path)
    entry = manifest.build_plan()[0]
    expected = "explicit_fixture" if explicit_fixture else "financial_clean"
    if explicit_fixture:
        entry["fixture_id"] = expected
    else:
        entry.pop("fixture_id")
    seen = []

    def capture_run(self, spec, fixture_root, execution_id):
        seen.append(spec.fixture_id)
        return RunResult(
            scenario_id=spec.scenario_id,
            trace=Trace(trace_id="fixture_test", execution_id=execution_id, variant=TraceVariant.BENIGN),
            success=True, runner_success=True, dataset_eligible=False,
        )

    monkeypatch.setattr(ScenarioRunner, "run", capture_run)
    runner = BenchmarkRunner(manifest, llm_backend=object())
    result = runner._execute(entry)
    assert result.success, result.error
    assert seen == [expected]


def test_tiny_real_vllm_smoke_plan_without_execution():
    manifest = manifest_for(dry_run=False, backend_name="vllm", propagation_modes=["single_origin"])
    manifest.topologies = ["review_loop"]
    manifest.lep_configs = [lep for lep in manifest.lep_configs if lep.code in {
        "LEP_TOOL_RESULT_CORRUPTION", "LEP_MEMORY_POISONING"}]
    # Preserve all five clean repetitions; the smoke experiment needs one LEP
    # execution per mechanism, paired to clean repetition 0 in its own pool.
    smoke = [entry for entry in manifest.build_plan()
             if entry["condition"] == "benign" or entry["repetition_index"] == 0]
    assert len(smoke) == 12
    for variant in ("standard", "memory_enabled"):
        assert sum(entry["condition"] == "benign" and entry["execution_variant"] == variant
                   for entry in smoke) == 5
    assert [(entry["lep_code"], entry["execution_variant"]) for entry in smoke if entry["lep_code"]] == [
        ("LEP_TOOL_RESULT_CORRUPTION", "standard"), ("LEP_MEMORY_POISONING", "memory_enabled")]
    assert {entry["propagation_mode"] for entry in smoke} == {"single_origin"}
    assert {entry["fixture_id"] for entry in smoke} == {"financial_clean"}
    assert (Path(__file__).resolve().parent.parent / "workspace_fixtures" / "financial_clean" / "manifest.json").is_file()
    benign_tags = {entry["pair_tag"] for entry in smoke if entry["condition"] == "benign"}
    assert all(entry["pair_tag"] in benign_tags for entry in smoke)
    assert all("unknown" not in BenchmarkRunner._short_trace_id(entry) for entry in smoke)
