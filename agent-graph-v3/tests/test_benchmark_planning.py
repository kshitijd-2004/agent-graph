"""Deterministic benchmark planning; no inference or external services.

Tests verify that the planner is fixture-aware: it discovers all fixtures
for a task family from their manifest.json files, includes fixture_id on
every plan entry, filters topologies and LEPs per-fixture, and keeps
benign/LEP clean-reference pools strictly per-fixture.
"""
from dataclasses import replace
from pathlib import Path

import pytest

from benchmark.benchmark_runner import (
    BenchmarkManifest,
    BenchmarkRunner,
    discover_fixtures,
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


class TestFixtureDiscovery:
    """Tests 1–3: fixture discovery and selection."""

    def test_code_review_has_two_fixtures_on_disk(self):
        discovered = discover_fixtures()
        assert "code_review" in discovered
        ids = {fx["fixture_id"] for fx in discovered["code_review"]}
        assert ids == {"code_review_easy", "code_review_conflicting"}

    def test_financial_has_two_fixtures_on_disk(self):
        discovered = discover_fixtures()
        assert "financial_analysis" in discovered
        ids = {fx["fixture_id"] for fx in discovered["financial_analysis"]}
        assert ids == {"financial_clean", "financial_version_conflict"}

    def test_research_has_one_fixture_on_disk(self):
        discovered = discover_fixtures()
        assert "research_synthesis" in discovered
        ids = {fx["fixture_id"] for fx in discovered["research_synthesis"]}
        assert ids == {"research_conflicting"}

    def test_discovery_populates_task_variant(self):
        discovered = discover_fixtures()
        by_id = {fx["fixture_id"]: fx for fx in discovered.get("code_review", [])}
        assert by_id["code_review_easy"]["task_variant"] == "easy"
        assert by_id["code_review_conflicting"]["task_variant"] == "conflicting"

    def test_explicit_fixture_ids_limits_plan(self):
        manifest = manifest_for(
            "code_review",
            fixture_ids={"code_review": ["code_review_easy"]},
        )
        plan = manifest.build_plan()
        assert {e["fixture_id"] for e in plan} == {"code_review_easy"}

    def test_smoke_fixtures_limits_to_default_per_family(self):
        manifest = manifest_for("code_review", smoke_fixtures=True)
        plan = manifest.build_plan()
        assert {e["fixture_id"] for e in plan} == {"code_review_easy"}


class TestPlanEntryFields:
    """Tests 3, 4: every entry carries fixture_id and task_variant."""

    @pytest.mark.parametrize("family,default_fixture", FIXTURES.items())
    def test_plan_has_explicit_fixture_identity_and_unique_trace_ids(self, family, default_fixture):
        manifest = manifest_for(family, smoke_fixtures=True)
        plan = manifest.build_plan()
        assert plan
        # In smoke mode only the default fixture should appear.
        assert {entry["fixture_id"] for entry in plan} == {default_fixture}
        trace_ids = [BenchmarkRunner._short_trace_id(entry) for entry in plan]
        assert len(trace_ids) == len(set(trace_ids))
        assert all(trace_id.startswith(default_fixture + ".") and "unknown" not in trace_id for trace_id in trace_ids)
        assert len({entry["run_id"] for entry in plan}) == len(plan)
        assert len({entry["scenario_id"] for entry in plan}) == len(plan)

    def test_multi_fixture_plan_carries_both_fixture_ids(self):
        manifest = manifest_for("code_review")
        plan = manifest.build_plan()
        fixture_ids = {e["fixture_id"] for e in plan}
        assert "code_review_easy" in fixture_ids
        assert "code_review_conflicting" in fixture_ids

    def test_alternate_fixture_task_variant_propagates(self):
        manifest = manifest_for("code_review")
        plan = manifest.build_plan()
        by_fixture = {}
        for entry in plan:
            by_fixture.setdefault(entry["fixture_id"], set()).add(entry["task_variant"])
        assert by_fixture["code_review_easy"] == {"easy"}
        assert by_fixture["code_review_conflicting"] == {"conflicting"}


class TestCompatibilityFiltering:
    """Tests 5–7: fixture-specific topology and LEP filtering."""

    def test_fixture_unsupported_topology_excluded(self):
        # research_conflicting does not support linear_2 or linear_3.
        manifest = BenchmarkManifest(
            task_families=["research_synthesis"],
            topologies=["review_loop", "linear_2", "linear_3"],
            lep_configs=get_default_leps("research_synthesis"),
            num_repetitions=1,
        )
        plan = manifest.build_plan()
        topologies = {e["topology"] for e in plan}
        assert "review_loop" in topologies
        assert "linear_2" not in topologies
        assert "linear_3" not in topologies

    def test_fixture_unsupported_lep_excluded(self):
        # All five family LEPs exist in the registry; the fixture manifest
        # restricts research_conflicting to only two of them.
        rs_manifest = manifest_for("research_synthesis")
        rs_plan = rs_manifest.build_plan()
        rs_leps = {e["lep_code"] for e in rs_plan if e["lep_code"]}
        # These are in the registry but filtered out by the fixture manifest.
        assert "LEP_HANDOFF_CORRUPTION" not in rs_leps
        assert "LEP_TOOL_RESULT_CORRUPTION" not in rs_leps
        assert "LEP_INPUT_DISREGARD" not in rs_leps
        # Only these two are supported by research_conflicting's manifest.
        assert rs_leps == {"LEP_INDIRECT_PROMPT_INJECTION", "LEP_MEMORY_POISONING"}

    def test_propagation_mode_filtering_still_works(self):
        requested = ["single_origin", "many_to_one"]
        manifest = manifest_for(propagation_modes=requested, smoke_fixtures=True)
        plan = manifest.build_plan()
        for topology, supported in TOPOLOGY_PROPAGATION_MODES.items():
            expected = set(requested) & set(supported)
            entries = [e for e in plan if e["topology"] == topology]
            assert {e["propagation_mode"] for e in entries} == expected
            lep_entries = [e for e in entries if e["condition"] == "single_lep"]
            assert {e["propagation_mode"] for e in lep_entries} == expected
            if not expected:
                assert not entries


class TestBenignPools:
    """Tests 8, 9, 14, 15: per-fixture five-run benign pools."""

    def test_separate_five_run_pools_and_matching_pair_tags(self):
        for family in FIXTURES:
            manifest = manifest_for(family, smoke_fixtures=True)
            plan = manifest.build_plan()
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

    def test_multi_fixture_each_gets_five_benign_runs(self):
        manifest = manifest_for("code_review")
        plan = manifest.build_plan()
        for fixture_id in ("code_review_easy", "code_review_conflicting"):
            for topology in TOPOLOGY_PROPAGATION_MODES:
                for variant in ("standard", "memory_enabled"):
                    pool = [e for e in plan
                            if e["fixture_id"] == fixture_id
                            and e["topology"] == topology
                            and e["execution_variant"] == variant
                            and e["condition"] == "benign"]
                    if pool:
                        assert len(pool) == 5, f"Expected 5 runs for {fixture_id}/{topology}/{variant}"

    def test_memory_poisoning_selects_memory_enabled_per_fixture(self):
        manifest = manifest_for("code_review")
        plan = manifest.build_plan()
        # Each fixture gets exactly 5 memory_enabled runs per supported topology.
        for fixture_id in ("code_review_easy", "code_review_conflicting"):
            mem_pools = [e for e in plan
                         if e["fixture_id"] == fixture_id
                         and e["condition"] == "benign"
                         and e["execution_variant"] == "memory_enabled"]
            by_topo = {}
            for e in mem_pools:
                by_topo.setdefault(e["topology"], []).append(e)
            for topo, entries in by_topo.items():
                assert len(entries) == 5, f"{fixture_id}/{topo}: expected 5, got {len(entries)}"


class TestMemoryPoolCreation:
    """Tests for memory pool conditional creation."""

    def test_memory_pool_is_only_created_for_families_with_memory_leps(self):
        non_memory = next(lep for lep in get_default_leps("code_review")
                          if lep.code == "LEP_TOOL_RESULT_CORRUPTION")
        memory = next(lep for lep in get_default_leps("financial_analysis")
                      if lep.code == "LEP_MEMORY_POISONING")
        manifest = BenchmarkManifest(
            task_families=["code_review", "financial_analysis"], topologies=["review_loop"],
            lep_configs=[replace(non_memory, task_family="code_review"),
                         replace(memory, task_family="financial_analysis")],
            num_repetitions=5, propagation_modes=["single_origin"],
            smoke_fixtures=True,
        )
        plan = manifest.build_plan()
        assert {e["execution_variant"] for e in plan if e["task_family"] == "code_review"} == {"standard"}
        assert {e["execution_variant"] for e in plan if e["task_family"] == "financial_analysis"} == {
            "standard", "memory_enabled"}


class TestSmokeExperiment:
    """Smoke experiment verification."""

    def test_tiny_real_vllm_smoke_plan_without_execution(self):
        manifest = manifest_for(dry_run=False, backend_name="vllm", propagation_modes=["single_origin"],
                                smoke_fixtures=True)
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


class TestFixtureFallback:
    def test_execute_uses_planned_fixture_or_shared_legacy_fallback(self, tmp_path, monkeypatch):
        manifest = manifest_for(output_dir=tmp_path, fixture_root=tmp_path, smoke_fixtures=True)
        entry = manifest.build_plan()[0]
        expected = "explicit_fixture" if False else "financial_clean"
        if False:
            entry["fixture_id"] = expected
        else:
            entry.pop("fixture_id", None)
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
