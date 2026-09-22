"""Fixture-aware propagation analysis tests.

Verifies that:
- LEP grouping keys include fixture_id (issues 10–13)
- Benign-reference lookup uses (fixture_id, topology, execution_variant) (issue 14)
- MEMORY_POISONING selects memory_enabled per fixture (issue 15)
- Two fixtures with identical task_family/topology/LEP/mode produce
  independent cells and never cross-use clean references (synthetic regression)
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from benchmark.propagation_analysis import (
    CleanReference,
    PropagationAnalyzer,
    PropagationResult,
)
from schemas.trace import Trace, TraceVariant
from schemas.trace_event import TraceEvent, TraceEventType
from schemas.event_labels import EventLabels


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _mk_event(
    idx: int,
    event_type: TraceEventType,
    agent_role: str = "reviewer",
    tool_name: str | None = None,
    output_text: str = "",
    input_text: str = "",
    memory_key: str | None = None,
    target_entity_id: str = "",
    depends_on: list[str] | None = None,
    stage_event_index: int | None = None,
    is_injection_origin: bool = False,
) -> TraceEvent:
    labels = EventLabels(is_injection_origin=is_injection_origin)
    return TraceEvent(
        trace_id="t1",
        event_id=f"evt_{idx}",
        event_index=idx,
        timestamp="2026-01-01T00:00:00Z",
        event_type=event_type,
        agent_role=agent_role,
        tool_name=tool_name,
        input_text=input_text,
        output_text=output_text,
        memory_key=memory_key,
        target_entity_id=target_entity_id,
        depends_on=depends_on or [],
        stage_event_index=stage_event_index,
        event_labels=labels,
    )


def _mk_trace(
    trace_id: str,
    events: list[TraceEvent],
    task_family: str = "code_review",
    topology: str = "review_loop",
    execution_variant: str = "standard",
    repetition_index: int = 0,
    fixture_id: str = "code_review_easy",
    lep_codes: list[str] | None = None,
    propagation_mode: str = "single_origin",
) -> Trace:
    return Trace(
        trace_id=trace_id,
        execution_id=f"exec-{trace_id}",
        variant=TraceVariant.BENIGN if not lep_codes else TraceVariant.MALIGNANT,
        events=events,
        metadata={
            "task_family": task_family,
            "topology": topology,
            "execution_variant": execution_variant,
            "repetition_index": repetition_index,
            "fixture_id": fixture_id,
            "condition": "benign" if not lep_codes else "single_lep",
            "lep_codes": lep_codes or [],
            "propagation_mode": propagation_mode,
        },
    )


def _write_trace_json(tmp_path: Path, trace: Trace) -> None:
    """Write a trace JSON to the traces directory."""
    traces_dir = tmp_path / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    path = traces_dir / f"{trace.trace_id}_trace.json"
    path.write_text(json.dumps(trace.to_dict()))


def _benign_traces(
    n: int,
    events: list[TraceEvent],
    *,
    fixture_id: str,
    execution_variant: str = "standard",
    task_family: str = "code_review",
    topology: str = "review_loop",
) -> list[Trace]:
    return [
        _mk_trace(
            f"{fixture_id}_{execution_variant}_b_{i:02d}",
            [deepcopy(e) for e in events],
            fixture_id=fixture_id,
            execution_variant=execution_variant,
            repetition_index=i,
            task_family=task_family,
            topology=topology,
        )
        for i in range(n)
    ]


def _lep_trace(
    trace_id: str,
    events: list[TraceEvent],
    lep_code: str,
    *,
    fixture_id: str,
    task_family: str = "code_review",
    topology: str = "review_loop",
    propagation_mode: str = "single_origin",
) -> Trace:
    return _mk_trace(
        trace_id,
        events,
        fixture_id=fixture_id,
        lep_codes=[lep_code],
        task_family=task_family,
        topology=topology,
        propagation_mode=propagation_mode,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Test 10: pair tags never cross fixtures
# ──────────────────────────────────────────────────────────────────────────────


class TestPairTagsNeverCrossFixtures:
    def test_lep_paired_to_same_fixture_benign(self):
        """LEP entry's pair_tag must reference a benign run from the same fixture."""
        from benchmark.benchmark_runner import BenchmarkManifest
        from schemas.scenario import TOPOLOGY_PROPAGATION_MODES
        from tasks.registry import get_default_leps

        manifest = BenchmarkManifest(
            task_families=["code_review"],
            topologies=list(TOPOLOGY_PROPAGATION_MODES),
            lep_configs=get_default_leps("code_review"),
            num_repetitions=5,
            smoke_fixtures=False,
        )
        plan = manifest.build_plan()
        benign_by_tag = {}
        for entry in plan:
            if entry["condition"] == "benign":
                benign_by_tag[entry["pair_tag"]] = entry["fixture_id"]

        for entry in plan:
            if entry["condition"] == "single_lep":
                expected_fixture = benign_by_tag[entry["pair_tag"]]
                assert entry["fixture_id"] == expected_fixture, (
                    f"LEP {entry['scenario_id']} paired to benign from "
                    f"{expected_fixture} but has fixture_id {entry['fixture_id']}"
                )


# ──────────────────────────────────────────────────────────────────────────────
# Test 11: LEP grouping keeps two fixtures separate
# ──────────────────────────────────────────────────────────────────────────────


class TestLepGroupingSeparatesFixtures:
    def test_two_fixtures_same_family_produce_separate_groups(self, tmp_path: Path):
        """LEP traces from two fixtures must be in different cell groups."""
        events = [_mk_event(i, TraceEventType.TOOL_CALL, output_text="ok", depends_on=[f"evt_{i-1}"] if i else None)
                  for i in range(3)]
        events[0].event_labels.is_injection_origin = True

        for fx in ("code_review_easy", "code_review_conflicting"):
            trace = _lep_trace(
                f"{fx}_lep_00",
                [deepcopy(e) for e in events],
                "LEP_TOOL_RESULT_CORRUPTION",
                fixture_id=fx,
            )
            _write_trace_json(tmp_path, trace)

        analyzer = PropagationAnalyzer(tmp_path)
        benign_refs, lep_traces = analyzer._load_and_group_traces()

        # Two distinct LEP groups
        assert len(lep_traces) == 2
        keys = set(lep_traces.keys())
        expected = {
            ("code_review_easy", "code_review", "review_loop", "LEP_TOOL_RESULT_CORRUPTION", "single_origin"),
            ("code_review_conflicting", "code_review", "review_loop", "LEP_TOOL_RESULT_CORRUPTION", "single_origin"),
        }
        assert keys == expected


# ──────────────────────────────────────────────────────────────────────────────
# Test 12: LEP trace can only use its own fixture's clean reference
# ──────────────────────────────────────────────────────────────────────────────


class TestLepUsesOwnCleanReference:
    def test_cross_fixture_reference_impossible(self, tmp_path: Path):
        """Even if both fixtures have clean references, LEP from fixture A
        must use only fixture A's clean reference."""
        clean_events = [_mk_event(i, TraceEventType.TOOL_CALL, output_text="clean", depends_on=[f"evt_{i-1}"] if i else None)
                        for i in range(3)]

        # Write 5 benign runs for each fixture
        for fx in ("code_review_easy", "code_review_conflicting"):
            for trace in _benign_traces(5, [deepcopy(e) for e in clean_events], fixture_id=fx):
                _write_trace_json(tmp_path, trace)

        # LEP trace from code_review_conflicting
        lep_events = [deepcopy(e) for e in clean_events]
        lep_events[0].event_labels.is_injection_origin = True
        lep_events[0].output_text = "CORRUPTED"
        lep_trace = _lep_trace(
            "cr_conflicting_lep_00",
            lep_events,
            "LEP_TOOL_RESULT_CORRUPTION",
            fixture_id="code_review_conflicting",
        )
        _write_trace_json(tmp_path, lep_trace)

        analyzer = PropagationAnalyzer(tmp_path)
        benign_refs, lep_traces = analyzer._load_and_group_traces()

        # Verify both clean references exist
        assert ("code_review_easy", "review_loop", "standard") in benign_refs
        assert ("code_review_conflicting", "review_loop", "standard") in benign_refs

        # Run analysis
        summary = analyzer.analyze()
        by_cell = summary["by_cell"]

        # Only one cell should exist (the LEP trace's own fixture)
        assert len(by_cell) == 1
        cell_key = next(iter(by_cell))
        assert "code_review_conflicting" in cell_key
        assert "code_review_easy" not in cell_key


# ──────────────────────────────────────────────────────────────────────────────
# Test 13: _aggregate_by_cell does not merge fixtures
# ──────────────────────────────────────────────────────────────────────────────


class TestAggregationDoesNotMergeFixtures:
    def test_distinct_fixtures_produce_distinct_cells(self):
        """Two PropagationResults with same task_family/topology/LEP/mode
        but different fixture_ids must land in separate CellSummaries."""
        pr_a = PropagationResult(
            trace_id="a_lep",
            task_family="code_review",
            fixture_id="code_review_easy",
            topology="review_loop",
            lep_code="LEP_TOOL_RESULT_CORRUPTION",
            propagation_mode="single_origin",
            propagation_occurred=True,
            event_depth=2,
            handoff_depth=1,
            downstream_agents_affected=["reviewer"],
            num_downstream_agents=1,
            anomalous_event_count=1,
            anomalous_event_ids=["evt_1"],
            num_benign_reference_runs=5,
        )
        pr_b = PropagationResult(
            trace_id="b_lep",
            task_family="code_review",
            fixture_id="code_review_conflicting",
            topology="review_loop",
            lep_code="LEP_TOOL_RESULT_CORRUPTION",
            propagation_mode="single_origin",
            propagation_occurred=False,
            event_depth=0,
            handoff_depth=0,
            downstream_agents_affected=[],
            num_downstream_agents=0,
            anomalous_event_count=0,
            anomalous_event_ids=[],
            num_benign_reference_runs=5,
        )

        # Minimal benign refs for the lookup
        benign_refs = {
            ("code_review_easy", "review_loop", "standard"): CleanReference(
                fixture_id="code_review_easy",
                task_family="code_review",
                topology="review_loop",
                execution_variant="standard",
                traces=[],
                repetition_indices=[],
            ),
            ("code_review_conflicting", "review_loop", "standard"): CleanReference(
                fixture_id="code_review_conflicting",
                task_family="code_review",
                topology="review_loop",
                execution_variant="standard",
                traces=[],
                repetition_indices=[],
            ),
        }

        analyzer = PropagationAnalyzer(Path("/tmp"))
        # Directly test the aggregation
        cells = analyzer._aggregate_by_cell([pr_a, pr_b], benign_refs)

        assert len(cells) == 2, f"Expected 2 cells, got {len(cells)}: {list(cells.keys())}"
        by_fixture = {cell.fixture_id: cell for cell in cells.values()}
        assert set(by_fixture) == {"code_review_easy", "code_review_conflicting"}
        assert by_fixture["code_review_easy"].propagation_probability == 1.0
        assert by_fixture["code_review_conflicting"].propagation_probability == 0.0
        assert all(cell.total_lep_runs == 1 for cell in cells.values())


# ──────────────────────────────────────────────────────────────────────────────
# Test 14: total_benign_runs reflects actual pool size
# ──────────────────────────────────────────────────────────────────────────────


class TestBenignRunCountAccuracy:
    def test_total_benign_runs_matches_pool_size(self, tmp_path: Path):
        """CellSummary.total_benign_runs should be 5 when five-run references exist."""
        events = [_mk_event(i, TraceEventType.TOOL_CALL, output_text="data", depends_on=[f"evt_{i-1}"] if i else None)
                  for i in range(3)]

        for fx in ("code_review_easy", "code_review_conflicting"):
            for trace in _benign_traces(5, events, fixture_id=fx):
                _write_trace_json(tmp_path, trace)
            # One LEP trace
            lep_evts = deepcopy(events)
            lep_evts[0].event_labels.is_injection_origin = True
            lep_evts[0].output_text = "BAD"
            _write_trace_json(tmp_path, _lep_trace(
                f"{fx}_lep_00", lep_evts, "LEP_TOOL_RESULT_CORRUPTION", fixture_id=fx,
            ))

        summary = PropagationAnalyzer(tmp_path).analyze()
        by_cell = summary["by_cell"]

        assert len(by_cell) == 2
        for cell in by_cell.values():
            assert cell["total_benign_runs"] == 5, (
                f"Expected 5 benign runs, got {cell['total_benign_runs']}"
            )


# ──────────────────────────────────────────────────────────────────────────────
# Test 15: MEMORY_POISONING selects memory_enabled per fixture
# ──────────────────────────────────────────────────────────────────────────────


class TestMemoryPoisoningPerFixtureReference:
    def test_memory_poisoning_uses_memory_enabled_reference(self, tmp_path: Path):
        """MEMORY_POISONING LEP traces must be analyzed against the
        memory_enabled clean reference, not standard, for each fixture."""
        events = [_mk_event(i, TraceEventType.TOOL_CALL, output_text="clean", depends_on=[f"evt_{i-1}"] if i else None)
                  for i in range(3)]

        for fx in ("code_review_easy", "code_review_conflicting"):
            # Standard clean reference (5 runs)
            for trace in _benign_traces(5, events,
                                        fixture_id=fx, execution_variant="standard"):
                _write_trace_json(tmp_path, trace)
            # Memory-enabled clean reference (5 runs)
            for trace in _benign_traces(5, events,
                                        fixture_id=fx, execution_variant="memory_enabled"):
                _write_trace_json(tmp_path, trace)
            # MEMORY_POISONING LEP trace
            lep_evts = deepcopy(events)
            lep_evts[0].event_labels.is_injection_origin = True
            lep_evts[0].output_text = "BAD"
            _write_trace_json(tmp_path, _lep_trace(
                f"{fx}_mem_lep_00", lep_evts, "LEP_MEMORY_POISONING", fixture_id=fx,
            ))

        analyzer = PropagationAnalyzer(tmp_path)
        benign_refs, _ = analyzer._load_and_group_traces()
        assert len(benign_refs) == 4
        assert all(ref.num_runs == 5 for ref in benign_refs.values())
        with patch.object(analyzer, "_analyze_single_trace", wraps=analyzer._analyze_single_trace) as analyze_trace:
            summary = analyzer.analyze()
        assert analyze_trace.call_count == 2
        for call in analyze_trace.call_args_list:
            ref = call.kwargs["benign_ref"]
            assert ref.fixture_id == call.kwargs["trace"].metadata["fixture_id"]
            assert ref.execution_variant == "memory_enabled"
        by_cell = summary["by_cell"]

        # Two cells (one per fixture), both analyzed
        assert len(by_cell) == 2
        for cell in by_cell.values():
            assert cell["total_benign_runs"] == 5
            assert cell["lep_code"] == "LEP_MEMORY_POISONING"


# ──────────────────────────────────────────────────────────────────────────────
# Test 16: Existing behavioral-anomaly tests remain green (import regression)
# ──────────────────────────────────────────────────────────────────────────────


class TestExistingBehavioralAnomalyImports:
    def test_behavioral_anomaly_module_imports(self):
        """Verify the behavioral_anomaly module still imports cleanly."""
        from benchmark.behavioral_anomaly import (
            build_clean_reference,
            canonicalize_artifact,
            compare_event_to_clean_reference,
            detect_behavioral_anomalies,
            select_clean_reference_variant,
            should_skip_cell,
            semantic_slot,
        )
        # All expected symbols present
        assert callable(build_clean_reference)
        assert callable(canonicalize_artifact)
        assert callable(compare_event_to_clean_reference)
        assert callable(detect_behavioral_anomalies)
        assert callable(select_clean_reference_variant)
        assert callable(should_skip_cell)
        assert callable(semantic_slot)


# ──────────────────────────────────────────────────────────────────────────────
# Synthetic regression: two fixtures, same task_family/topology/LEP/mode,
# intentionally different benign behavior → two independent cells
# ──────────────────────────────────────────────────────────────────────────────


class TestSyntheticTwoFixtureRegression:
    """Synthetic regression: two fixtures with same task_family, topology,
    LEP, propagation_mode but different benign behavior must produce two
    independent cells and never cross-use clean references."""

    def test_independent_cells_no_cross_reference_use(self, tmp_path: Path):
        """Fixture A always emits 'output_A'; fixture B always emits 'output_B'.
        LEP injects 'CORRUPTED'.  Each fixture's cell should only flag its
        own downstream events using its own clean reference."""
        # Build clean events for fixture A
        clean_a = [
            _mk_event(0, TraceEventType.TOOL_CALL, output_text="output_A", tool_name="search"),
            _mk_event(1, TraceEventType.TOOL_CALL, output_text="output_A", tool_name="write", depends_on=["evt_0"]),
            _mk_event(2, TraceEventType.FINAL_RESPONSE, output_text="output_A", depends_on=["evt_1"]),
        ]
        # Build clean events for fixture B (same structure, different content)
        clean_b = [
            _mk_event(0, TraceEventType.TOOL_CALL, output_text="output_B", tool_name="search"),
            _mk_event(1, TraceEventType.TOOL_CALL, output_text="output_B", tool_name="write", depends_on=["evt_0"]),
            _mk_event(2, TraceEventType.FINAL_RESPONSE, output_text="output_B", depends_on=["evt_1"]),
        ]

        # Write 5 clean runs for each fixture
        for fx, events in [("fixture_A", clean_a), ("fixture_B", clean_b)]:
            for i in range(5):
                trace = _mk_trace(
                    f"{fx}_b_{i:02d}",
                    [deepcopy(e) for e in events],
                    fixture_id=fx,
                    task_family="shared_family",
                    topology="review_loop",
                    repetition_index=i,
                )
                _write_trace_json(tmp_path, trace)

            # Write 1 LEP run per fixture
            lep_events = deepcopy(events)
            lep_events[0].event_labels.is_injection_origin = True
            lep_events[0].output_text = "CORRUPTED"
            lep_events[1].output_text = "CORRUPTED"
            _write_trace_json(tmp_path, _lep_trace(
                f"{fx}_lep_00", lep_events, "LEP_TOOL_RESULT_CORRUPTION",
                fixture_id=fx, task_family="shared_family",
            ))

        analyzer = PropagationAnalyzer(tmp_path)
        benign_refs, lep_traces = analyzer._load_and_group_traces()

        # Two separate LEP groups
        assert len(lep_traces) == 2
        fixture_ids_in_groups = {key[0] for key in lep_traces}
        assert fixture_ids_in_groups == {"fixture_A", "fixture_B"}

        # Two separate clean references
        assert ("fixture_A", "review_loop", "standard") in benign_refs
        assert ("fixture_B", "review_loop", "standard") in benign_refs
        assert benign_refs[("fixture_A", "review_loop", "standard")].num_runs == 5
        assert benign_refs[("fixture_B", "review_loop", "standard")].num_runs == 5

        # Run full analysis
        with patch.object(analyzer, "_analyze_single_trace", wraps=analyzer._analyze_single_trace) as analyze_trace:
            summary = analyzer.analyze()
        assert analyze_trace.call_count == 2
        for call in analyze_trace.call_args_list:
            trace = call.kwargs["trace"]
            ref = call.kwargs["benign_ref"]
            assert ref.fixture_id == trace.metadata["fixture_id"]
            assert all(clean.events[2].output_text == trace.events[2].output_text for clean in ref.traces)
        by_cell = summary["by_cell"]

        # Must produce exactly 2 cells (one per fixture)
        assert len(by_cell) == 2, f"Expected 2 cells, got {len(by_cell)}: {list(by_cell.keys())}"

        # Each cell must carry its own fixture_id
        fixture_ids_in_cells = {cell["fixture_id"] for cell in by_cell.values()}
        assert fixture_ids_in_cells == {"fixture_A", "fixture_B"}

        # Each cell's total_benign_runs must be 5 (from its own fixture)
        for cell in by_cell.values():
            assert cell["total_benign_runs"] == 5, (
                f"Cell {cell['fixture_id']} has total_benign_runs={cell['total_benign_runs']}, expected 5"
            )

        # Each cell should have exactly 1 LEP run
        for cell in by_cell.values():
            assert cell["total_lep_runs"] == 1

    def test_clean_reference_lookup_key_shape(self, tmp_path: Path):
        """Verify that the benign ref lookup key is (fixture_id, topology, execution_variant),
        not the old (task_family, topology) shape."""
        events = [_mk_event(i, TraceEventType.TOOL_CALL, output_text="x", depends_on=[f"evt_{i-1}"] if i else None)
                  for i in range(3)]
        for fx in ("fx_A", "fx_B"):
            for i in range(5):
                trace = _mk_trace(
                    f"{fx}_b_{i:02d}", [deepcopy(e) for e in events],
                    fixture_id=fx, task_family="same_family", topology="review_loop",
                    repetition_index=i,
                )
                _write_trace_json(tmp_path, trace)

        analyzer = PropagationAnalyzer(tmp_path)
        benign_refs, _ = analyzer._load_and_group_traces()

        # Old shape (task_family, topology) would give 1 key — wrong.
        # New shape (fixture_id, topology, exec_variant) gives 2 keys — correct.
        assert len(benign_refs) == 2
        assert ("fx_A", "review_loop", "standard") in benign_refs
        assert ("fx_B", "review_loop", "standard") in benign_refs
        # These old-shape keys must NOT exist
        assert ("same_family", "review_loop") not in benign_refs
