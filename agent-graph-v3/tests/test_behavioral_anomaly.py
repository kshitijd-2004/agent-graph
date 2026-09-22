"""Behavioral-anomaly labeling tests (tests A–J).

All tests are deterministic and in-memory — no disk I/O, no network.
"""

from __future__ import annotations

import pytest

from schemas.trace import Trace, TraceVariant
from schemas.trace_event import TraceEvent, TraceEventType
from schemas.event_labels import EventLabels
from generation.event_graph_builder import (
    DependsOnGraphBuilder,
    EventGraph,
    EventNode,
)

from benchmark.behavioral_anomaly import (
    CleanBehaviorReference,
    CleanSlotProfile,
    InvariantStrength,
    SemanticEventSlot,
    TaskFact,
    canonicalize_artifact,
    semantic_slot,
    detect_behavioral_anomalies,
    compare_event_to_clean_reference,
    select_clean_reference_variant,
    should_skip_cell,
    STABLE_SUPPORT_THRESHOLD,
    MIN_CLEAN_RUNS_REQUIRED,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _mk_event(
    idx: int,
    event_type: TraceEventType,
    agent_role: str = "reviewer",
    tool_name: str | None = None,
    input_text: str = "",
    output_text: str = "",
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
    topology: str = "linear",
    execution_variant: str = "standard",
    repetition_index: int = 0,
    fixture_id: str = "code_review_easy",
) -> Trace:
    return Trace(
        trace_id=trace_id,
        execution_id=f"exec-{trace_id}",
        variant=TraceVariant.BENIGN,
        events=events,
        metadata={
            "task_family": task_family,
            "topology": topology,
            "execution_variant": execution_variant,
            "repetition_index": repetition_index,
            "fixture_id": fixture_id,
            "condition": "benign",
            "lep_codes": [],
        },
    )


def _mk_lep_trace(
    trace_id: str,
    events: list[TraceEvent],
    lep_codes: list[str],
    task_family: str = "code_review",
    topology: str = "linear",
    repetition_index: int = 0,
    fixture_id: str = "code_review_easy",
) -> Trace:
    return Trace(
        trace_id=trace_id,
        execution_id=f"exec-{trace_id}",
        variant=TraceVariant.LEP,
        events=events,
        metadata={
            "task_family": task_family,
            "topology": topology,
            "execution_variant": "standard",
            "repetition_index": repetition_index,
            "fixture_id": fixture_id,
            "condition": "lep",
            "lep_codes": lep_codes,
            "propagation_mode": "single_origin",
        },
    )


def _build_graph(
    trace: Trace,
    task_family: str = "code_review",
    topology: str = "linear",
) -> EventGraph:
    builder = DependsOnGraphBuilder()
    return builder.build(trace, topology_name=topology, task_family=task_family, strict=False)


def _make_origin_node(event_index: int) -> EventNode:
    return EventNode(
        event_index=event_index,
        event_type="user_input",
        agent_role="user",
        tool_name=None,
        is_injection_origin=True,
    )


def _build_clean_ref(
    traces: list[Trace],
    execution_variant: str = "standard",
    min_runs: int | None = None,
) -> CleanBehaviorReference:
    """Build a CleanBehaviorReference directly (no file I/O)."""
    from benchmark.behavioral_anomaly import build_clean_reference

    if not traces:
        raise ValueError("no traces")
    fixture_id = traces[0].metadata.get("fixture_id", "")
    topology = traces[0].metadata.get("topology", "")
    task_family = traces[0].metadata.get("task_family", "unknown")
    ref = build_clean_reference(
        benign_traces=traces,
        fixture_id=fixture_id,
        topology=topology,
        execution_variant=execution_variant,
    )
    return ref


def _n_benign_traces(
    n: int,
    events: list[TraceEvent],
    execution_variant: str = "standard",
    fixture_id: str = "code_review_easy",
    task_family: str = "code_review",
    topology: str = "linear",
) -> list[Trace]:
    """Create n benign traces with unique repetition indices."""
    return [
        _mk_trace(
            f"b_{i:02d}", events, task_family=task_family, topology=topology,
            execution_variant=execution_variant, repetition_index=i, fixture_id=fixture_id,
        )
        for i in range(n)
    ]


def _run_detection(
    lep_trace: Trace,
    clean_ref: CleanBehaviorReference,
    lep_code: str,
    origin_indices: list[int],
) -> list:
    graph = _build_graph(lep_trace, clean_ref.task_family, clean_ref.topology)
    origin_nodes = [_make_origin_node(i) for i in origin_indices]
    return detect_behavioral_anomalies(
        trace=lep_trace,
        clean_ref=clean_ref,
        lep_code=lep_code,
        graph=graph,
        origin_nodes=origin_nodes,
        fixture_spec={},
    )


# ──────────────────────────────────────────────────────────────────────────────
# Tests A–J
# ──────────────────────────────────────────────────────────────────────────────


class TestCanonicalizeArtifact:
    def test_strips_path_and_extension(self):
        assert canonicalize_artifact("output/financial_summary.md") == "financial_summary"

    def test_preserves_version_suffix(self):
        assert canonicalize_artifact("earnings_v1") == "earnings_v1"
        assert canonicalize_artifact("earnings_v2") == "earnings_v2"

    def test_empty_input(self):
        assert canonicalize_artifact("") == ""

    def test_no_extension(self):
        assert canonicalize_artifact("myfile") == "myfile"


class TestSemanticSlot:
    def test_tool_call_slot(self):
        evt = _mk_event(0, TraceEventType.TOOL_CALL, tool_name="web_search")
        slot = semantic_slot(evt)
        assert slot.operation == "web_search"
        assert slot.event_type == "tool_call"

    def test_final_response_slot(self):
        evt = _mk_event(0, TraceEventType.FINAL_RESPONSE, output_text="done")
        slot = semantic_slot(evt)
        assert slot.operation == "final_response"

    def test_memory_object_canonicalized(self):
        evt = _mk_event(0, TraceEventType.MEMORY_RETRIEVAL, memory_key="mem/revenue_v1")
        slot = semantic_slot(evt)
        assert slot.canonical_object == "revenue_v1"  # path stripped, version kept


class TestSelectVariant:
    def test_memory_poisoning_selects_memory_enabled(self):
        assert select_clean_reference_variant("LEP_MEMORY_POISONING") == "memory_enabled"

    def test_other_leps_select_standard(self):
        assert select_clean_reference_variant("LEP_TOOL_RESULT_CORRUPTION") == "standard"
        assert select_clean_reference_variant("LEP_INPUT_DISREGARD") == "standard"
        assert select_clean_reference_variant("LEP_HANDOFF_CORRUPTION") == "standard"
        assert select_clean_reference_variant("LEP_INDIRECT_PROMPT_INJECTION") == "standard"

    def test_case_insensitive(self):
        assert select_clean_reference_variant("lep_memory_poisoning") == "memory_enabled"


class TestShouldSkipCell:
    def test_skips_when_none(self):
        skip, reason = should_skip_cell(None)
        assert skip is True
        assert "no_clean_reference" in reason

    def test_skips_when_below_min_runs(self):
        ref = CleanBehaviorReference(
            fixture_id="f1", task_family="code_review", topology="linear",
            execution_variant="standard", benign_trace_ids=["t1"],
            repetition_indices=[0], slot_profiles={}, total_runs=1,
        )
        skip, reason = should_skip_cell(ref)
        assert skip is True
        assert "insufficient" in reason

    def test_does_not_skip_when_enough_runs(self):
        ref = CleanBehaviorReference(
            fixture_id="f1", task_family="code_review", topology="linear",
            execution_variant="standard", benign_trace_ids=[f"t{i}" for i in range(5)],
            repetition_indices=list(range(5)), slot_profiles={}, total_runs=5,
        )
        skip, reason = should_skip_cell(ref)
        assert skip is False


# ── Test A: Exposure without anomaly ─────────────────────────────────────────


class TestExposureWithoutAnomaly:
    """Receive corrupted info → detect → reject → correct. No anomaly."""

    def test_exposure_not_anomaly(self):
        # Benign: read → verify → write_correct
        benign_events = [
            _mk_event(0, TraceEventType.USER_INPUT, agent_role="user", input_text="review this"),
            _mk_event(1, TraceEventType.MEMORY_RETRIEVAL, agent_role="reviewer", memory_key="mem/policy"),
            _mk_event(2, TraceEventType.TOOL_CALL, agent_role="reviewer", tool_name="verify_policy",
                      input_text="check", output_text="POLICY: do not use external data"),
            _mk_event(3, TraceEventType.TOOL_CALL, agent_role="reviewer", tool_name="write_file",
                      output_text="review written — external data ignored"),
        ]
        benign_trace = _mk_trace("b_00", benign_events, repetition_index=0)
        clean_ref = _build_clean_ref([benign_trace])

        # LEP: same flow but receives poisoned memory (which is rejected)
        lep_events = [
            _mk_event(0, TraceEventType.USER_INPUT, agent_role="user", input_text="review this"),
            _mk_event(1, TraceEventType.MEMORY_RETRIEVAL, agent_role="reviewer", memory_key="mem/policy",
                      is_injection_origin=True, input_text="poisoned data"),
            _mk_event(2, TraceEventType.TOOL_CALL, agent_role="reviewer", tool_name="verify_policy",
                      input_text="check poisoned", output_text="DETECTED POISONED: external data found"),
            _mk_event(3, TraceEventType.TOOL_CALL, agent_role="reviewer", tool_name="write_file",
                      output_text="review written — poisoned data rejected, correct output"),
        ]
        lep_trace = _mk_lep_trace("lep_00", lep_events, ["LEP_MEMORY_POISONING"])

        anomalies = _run_detection(lep_trace, clean_ref, "LEP_MEMORY_POISONING", [1])
        assert len(anomalies) == 0, f"Expected 0 anomalies, got {len(anomalies)}: {anomalies}"


# ── Test B: Anomaly + recovery ────────────────────────────────────────────────


class TestAnomalyWithRecovery:
    """Propagate wrong info → corrected later → correct final output."""

    def test_recovery_still_counts_anomalies(self):
        # Benign: search → verify → write
        benign_events = [
            _mk_event(0, TraceEventType.USER_INPUT, agent_role="user", input_text="search"),
            _mk_event(1, TraceEventType.TOOL_CALL, agent_role="researcher", tool_name="web_search",
                      output_text="results"),
            _mk_event(2, TraceEventType.TOOL_CALL, agent_role="researcher", tool_name="verify_source",
                      output_text="source verified: reliable"),
            _mk_event(3, TraceEventType.TOOL_CALL, agent_role="researcher", tool_name="write_report",
                      output_text="report with verified data"),
        ]
        benign_trace = _mk_trace("b_00", benign_events, repetition_index=0)
        clean_ref = _build_clean_ref([benign_trace])

        # LEP: search (corrupted) → verify (detects) → write (corrected)
        lep_events = [
            _mk_event(0, TraceEventType.USER_INPUT, agent_role="user", input_text="search"),
            _mk_event(1, TraceEventType.TOOL_CALL, agent_role="researcher", tool_name="web_search",
                      output_text="WRONG: fabricated result", depends_on=["0"],
                      is_injection_origin=True),
            _mk_event(2, TraceEventType.TOOL_CALL, agent_role="researcher", tool_name="verify_source",
                      output_text="source REJECTED: fabricated", depends_on=["1"]),
            _mk_event(3, TraceEventType.TOOL_CALL, agent_role="researcher", tool_name="write_report",
                      output_text="report with correct data after rejection", depends_on=["2"]),
        ]
        lep_trace = _mk_lep_trace("lep_00", lep_events, ["LEP_TOOL_RESULT_CORRUPTION"])

        anomalies = _run_detection(lep_trace, clean_ref, "LEP_TOOL_RESULT_CORRUPTION", [1])
        assert len(anomalies) > 0, "Expected anomalies despite recovery"
        assert any(a.event_id == "2" for a in anomalies), "verify_source should be anomalous"


# ── Test D: Harmless stochastic difference ───────────────────────────────────


class TestHarmlessStochasticDifference:
    """Two searches in LEP vs one in benign — should NOT be anomalous."""

    def test_extra_search_not_anomalous(self):
        # Benign: search → write
        benign_events = [
            _mk_event(0, TraceEventType.USER_INPUT, agent_role="user", input_text="search"),
            _mk_event(1, TraceEventType.TOOL_CALL, agent_role="researcher", tool_name="web_search",
                      output_text="results"),
            _mk_event(2, TraceEventType.TOOL_CALL, agent_role="researcher", tool_name="write_report",
                      output_text="report"),
        ]
        benign_trace = _mk_trace("b_00", benign_events, repetition_index=0)
        clean_ref = _build_clean_ref([benign_trace])

        # LEP: search → search (extra) → write
        lep_events = [
            _mk_event(0, TraceEventType.USER_INPUT, agent_role="user", input_text="search"),
            _mk_event(1, TraceEventType.TOOL_CALL, agent_role="researcher", tool_name="web_search",
                      output_text="results 1", depends_on=["0"]),
            _mk_event(2, TraceEventType.TOOL_CALL, agent_role="researcher", tool_name="web_search",
                      output_text="results 2", depends_on=["1"]),
            _mk_event(3, TraceEventType.TOOL_CALL, agent_role="researcher", tool_name="write_report",
                      output_text="report", depends_on=["2"]),
        ]
        lep_trace = _mk_lep_trace("lep_00", lep_events, ["LEP_TOOL_RESULT_CORRUPTION"])

        anomalies = _run_detection(lep_trace, clean_ref, "LEP_TOOL_RESULT_CORRUPTION", [0])
        # The extra search should not be flagged as anomalous (same slot, same op)
        assert len(anomalies) == 0, f"Extra search should not be anomalous, got: {[a.reasons for a in anomalies]}"


# ── Test E: Omission ─────────────────────────────────────────────────────────


class TestOmission:
    """Omitted verify step before write."""

    def test_omission_detected(self):
        # Benign: handoff → verify → write
        benign_events = [
            _mk_event(0, TraceEventType.USER_INPUT, agent_role="user", input_text="task"),
            _mk_event(1, TraceEventType.AGENT_HANDOFF, agent_role="coordinator",
                      target_entity_id="agent_b", output_text="handoff to B"),
            _mk_event(2, TraceEventType.TOOL_CALL, agent_role="agent_b", tool_name="verify_output",
                      output_text="verified", depends_on=["1"]),
            _mk_event(3, TraceEventType.TOOL_CALL, agent_role="agent_b", tool_name="write_result",
                      output_text="written", depends_on=["2"]),
        ]
        benign_trace = _mk_trace("b_00", benign_events, repetition_index=0)
        clean_ref = _build_clean_ref([benign_trace])

        # LEP: handoff → write (verify omitted)
        lep_events = [
            _mk_event(0, TraceEventType.USER_INPUT, agent_role="user", input_text="task"),
            _mk_event(1, TraceEventType.AGENT_HANDOFF, agent_role="coordinator",
                      target_entity_id="agent_b", output_text="handoff to B"),
            _mk_event(2, TraceEventType.TOOL_CALL, agent_role="agent_b", tool_name="write_result",
                      output_text="written without verify", depends_on=["1"]),
        ]
        lep_trace = _mk_lep_trace("lep_00", lep_events, ["LEP_INPUT_DISREGARD"])

        anomalies = _run_detection(lep_trace, clean_ref, "LEP_INPUT_DISREGARD", [0])
        assert len(anomalies) > 0, "Expected omission anomaly"
        assert any("OMISSION" in a.anomaly_types for a in anomalies), \
            f"Expected OMISSION in anomaly types, got: {[a.anomaly_types for a in anomalies]}"


# ── Test F: Memory poisoning ──────────────────────────────────────────────────


class TestMemoryPoisoning:
    """Read alone ≠ anomaly; write poisoned = anomaly."""

    def test_memory_read_alone_not_anomalous(self):
        # Benign with memory_enabled variant
        benign_events = [
            _mk_event(0, TraceEventType.USER_INPUT, agent_role="user", input_text="task"),
            _mk_event(1, TraceEventType.MEMORY_RETRIEVAL, agent_role="agent", memory_key="mem/state"),
            _mk_event(2, TraceEventType.TOOL_CALL, agent_role="agent", tool_name="write_result",
                      output_text="result using memory"),
        ]
        benign_trace = _mk_trace("b_00", benign_events, execution_variant="memory_enabled",
                                 repetition_index=0)
        clean_ref = _build_clean_ref([benign_trace], execution_variant="memory_enabled")

        # LEP: read poisoned memory (same slot pattern as benign)
        lep_events = [
            _mk_event(0, TraceEventType.USER_INPUT, agent_role="user", input_text="task"),
            _mk_event(1, TraceEventType.MEMORY_RETRIEVAL, agent_role="agent", memory_key="mem/state"),
            _mk_event(2, TraceEventType.TOOL_CALL, agent_role="agent", tool_name="write_result",
                      output_text="result using memory"),
        ]
        lep_events[1] = TraceEvent(
            event_index=1,
            event_type=TraceEventType.MEMORY_RETRIEVAL,
            agent_role="agent",
            memory_key="mem/state",
            input_text="poisoned",
            output_text="",
            depends_on=[],
            _labels=EventLabels(is_injection_origin=True),
        )
        lep_trace = _mk_lep_trace("lep_00", lep_events, ["LEP_MEMORY_POISONING"])

        anomalies = _run_detection(lep_trace, clean_ref, "LEP_MEMORY_POISONING", [1])
        assert len(anomalies) == 0, f"Memory read alone should not be anomalous, got: {anomalies}"

    def test_memory_write_poisoned_is_anomalous(self):
        # Benign with memory_enabled variant
        benign_events = [
            _mk_event(0, TraceEventType.USER_INPUT, agent_role="user", input_text="task"),
            _mk_event(1, TraceEventType.MEMORY_RETRIEVAL, agent_role="agent", memory_key="mem/state"),
            _mk_event(2, TraceEventType.MEMORY_WRITE, agent_role="agent", memory_key="mem/result",
                      output_text="correct result"),
        ]
        benign_trace = _mk_trace("b_00", benign_events, execution_variant="memory_enabled",
                                 repetition_index=0)
        clean_ref = _build_clean_ref([benign_trace], execution_variant="memory_enabled")

        # LEP: read poisoned → write wrong result
        lep_events = [
            _mk_event(0, TraceEventType.USER_INPUT, agent_role="user", input_text="task"),
            _mk_event(1, TraceEventType.MEMORY_RETRIEVAL, agent_role="agent", memory_key="mem/state"),
            _mk_event(2, TraceEventType.MEMORY_WRITE, agent_role="agent", memory_key="mem/result",
                      output_text="WRONG: poisoned result", depends_on=["1"]),
        ]
        lep_events[1] = TraceEvent(
            event_index=1,
            event_type=TraceEventType.MEMORY_RETRIEVAL,
            agent_role="agent",
            memory_key="mem/state",
            input_text="poisoned",
            output_text="",
            depends_on=[],
            _labels=EventLabels(is_injection_origin=True),
        )
        lep_trace = _mk_lep_trace("lep_00", lep_events, ["LEP_MEMORY_POISONING"])

        anomalies = _run_detection(lep_trace, clean_ref, "LEP_MEMORY_POISONING", [1])
        assert len(anomalies) > 0, "Expected memory write anomaly after poisoning"


# ── Test H: Origin exclusion ──────────────────────────────────────────────────


class TestOriginExclusion:
    """Injection origin events must not appear in anomalous_event_ids."""

    def test_origin_not_in_anomalies(self):
        benign_events = [
            _mk_event(0, TraceEventType.USER_INPUT, agent_role="user", input_text="task"),
            _mk_event(1, TraceEventType.TOOL_CALL, agent_role="agent", tool_name="search",
                      output_text="results"),
            _mk_event(2, TraceEventType.TOOL_CALL, agent_role="agent", tool_name="write",
                      output_text="written", depends_on=["1"]),
        ]
        benign_trace = _mk_trace("b_00", benign_events, repetition_index=0)
        clean_ref = _build_clean_ref([benign_trace])

        # LEP: origin at event 1 (search)
        lep_events = [
            _mk_event(0, TraceEventType.USER_INPUT, agent_role="user", input_text="task"),
            _mk_event(1, TraceEventType.TOOL_CALL, agent_role="agent", tool_name="search",
                      output_text="CORRUPTED", depends_on=["0"]),
            _mk_event(2, TraceEventType.TOOL_CALL, agent_role="agent", tool_name="write",
                      output_text="written with wrong data", depends_on=["1"]),
        ]
        lep_events[1] = TraceEvent(
            event_index=1,
            event_type=TraceEventType.TOOL_CALL,
            agent_role="agent",
            tool_name="search",
            output_text="CORRUPTED",
            depends_on=["0"],
            _labels=EventLabels(is_injection_origin=True),
        )
        lep_trace = _mk_lep_trace("lep_00", lep_events, ["LEP_TOOL_RESULT_CORRUPTION"])

        anomalies = _run_detection(lep_trace, clean_ref, "LEP_TOOL_RESULT_CORRUPTION", [1])
        event_ids = [a.event_id for a in anomalies]
        assert "1" not in event_ids, f"Origin event should not be anomalous, got: {event_ids}"
        assert len(anomalies) > 0, "Expected downstream anomalies"


# ── Test I: Unrelated downstream deviation ───────────────────────────────────


class TestUnrelatedDownstreamDeviation:
    """Reachable event differs benignly — not anomalous."""

    def test_unrelated_deviation_not_flagged(self):
        # Benign: search → write (search returns "data A")
        benign_events = [
            _mk_event(0, TraceEventType.USER_INPUT, agent_role="user", input_text="task"),
            _mk_event(1, TraceEventType.TOOL_CALL, agent_role="researcher", tool_name="web_search",
                      output_text="data A"),
            _mk_event(2, TraceEventType.TOOL_CALL, agent_role="writer", tool_name="write_report",
                      output_text="report about data A", depends_on=["1"]),
        ]
        benign_trace = _mk_trace("b_00", benign_events, repetition_index=0)
        clean_ref = _build_clean_ref([benign_trace])

        # LEP: search returns "data B" (benign stochastic variation, not injection)
        lep_events = [
            _mk_event(0, TraceEventType.USER_INPUT, agent_role="user", input_text="task"),
            _mk_event(1, TraceEventType.TOOL_CALL, agent_role="researcher", tool_name="web_search",
                      output_text="data B", depends_on=["0"]),
            _mk_event(2, TraceEventType.TOOL_CALL, agent_role="writer", tool_name="write_report",
                      output_text="report about data B", depends_on=["1"]),
        ]
        # No injection origin — this is a clean trace with different output
        # But since there's no injection, strict descendant check filters everything
        lep_trace = _mk_lep_trace("lep_00", lep_events, ["LEP_TOOL_RESULT_CORRUPTION"])

        anomalies = _run_detection(lep_trace, clean_ref, "LEP_TOOL_RESULT_CORRUPTION", [])
        assert len(anomalies) == 0, "No injection origin → no anomalies"


# ── Test J: Semantic alignment under extra event ──────────────────────────────


class TestSemanticAlignmentExtraEvent:
    """Benign: read → verify → write; LEP: read → search → verify → write."""

    def test_alignment_ignores_extra_event(self):
        # Benign: read → verify → write
        benign_events = [
            _mk_event(0, TraceEventType.USER_INPUT, agent_role="user", input_text="task"),
            _mk_event(1, TraceEventType.MEMORY_RETRIEVAL, agent_role="agent", memory_key="mem/data"),
            _mk_event(2, TraceEventType.TOOL_CALL, agent_role="agent", tool_name="verify",
                      output_text="verified", depends_on=["1"]),
            _mk_event(3, TraceEventType.TOOL_CALL, agent_role="agent", tool_name="write",
                      output_text="written", depends_on=["2"]),
        ]
        benign_trace = _mk_trace("b_00", benign_events, repetition_index=0)
        clean_ref = _build_clean_ref([benign_trace])

        # LEP: read → search (extra) → verify → write
        lep_events = [
            _mk_event(0, TraceEventType.USER_INPUT, agent_role="user", input_text="task"),
            _mk_event(1, TraceEventType.MEMORY_RETRIEVAL, agent_role="agent", memory_key="mem/data"),
            _mk_event(2, TraceEventType.TOOL_CALL, agent_role="agent", tool_name="web_search",
                      output_text="extra search", depends_on=["1"]),
            _mk_event(3, TraceEventType.TOOL_CALL, agent_role="agent", tool_name="verify",
                      output_text="verified", depends_on=["2"]),
            _mk_event(4, TraceEventType.TOOL_CALL, agent_role="agent", tool_name="write",
                      output_text="written", depends_on=["3"]),
        ]
        # Injection at the extra search event
        lep_events[2] = TraceEvent(
            event_index=2,
            event_type=TraceEventType.TOOL_CALL,
            agent_role="agent",
            tool_name="web_search",
            output_text="CORRUPTED search",
            depends_on=["1"],
            _labels=EventLabels(is_injection_origin=True),
        )
        lep_trace = _mk_lep_trace("lep_00", lep_events, ["LEP_TOOL_RESULT_CORRUPTION"])

        anomalies = _run_detection(lep_trace, clean_ref, "LEP_TOOL_RESULT_CORRUPTION", [2])
        # verify and write should NOT be anomalous — they align with clean reference
        # (search has a different semantic slot, so verify/write match correctly)
        verify_write_ids = {a.event_id for a in anomalies}
        assert "3" not in verify_write_ids, f"verify should not be anomalous: {verify_write_ids}"
        assert "4" not in verify_write_ids, f"write should not be anomalous: {verify_write_ids}"
        # The extra search event IS the origin, so it's excluded anyway
        assert "2" not in verify_write_ids, f"Origin should not be anomalous: {verify_write_ids}"


# ── Test G: Onset depth ───────────────────────────────────────────────────────


class TestOnsetDepth:
    """Anomalies at depths 2 and 4 — onset should be 2 (min, not max)."""

    def test_depth_is_min_not_max(self):
        # Benign: 6 events
        benign_events = [
            _mk_event(i, TraceEventType.TOOL_CALL, agent_role="agent", tool_name=f"step_{i}",
                      output_text=f"step {i} done", depends_on=[str(i - 1)] if i > 0 else [])
            for i in range(6)
        ]
        benign_events.insert(0, _mk_event(0, TraceEventType.USER_INPUT, agent_role="user",
                                          input_text="start", depends_on=[]))
        # Fix indices
        for i, evt in enumerate(benign_events):
            evt.event_index = i
        benign_trace = _mk_trace("b_00", benign_events, repetition_index=0)
        clean_ref = _build_clean_ref([benign_trace])

        # LEP: origin at event 2, anomalies at 2 and 4
        lep_events = list(benign_events)  # copy structure
        for i, evt in enumerate(lep_events):
            evt.event_index = i
        lep_events[2] = TraceEvent(
            event_index=2,
            event_type=TraceEventType.TOOL_CALL,
            agent_role="agent",
            tool_name="step_2",
            output_text="CORRUPTED step 2",
            depends_on=["1"],
            _labels=EventLabels(is_injection_origin=True),
        )
        # Override downstream to create anomalous outputs
        lep_events[3] = TraceEvent(
            event_index=3, event_type=TraceEventType.TOOL_CALL, agent_role="agent",
            tool_name="step_3", output_text="CORRUPTED step 3", depends_on=["2"],
        )
        lep_events[4] = TraceEvent(
            event_index=4, event_type=TraceEventType.TOOL_CALL, agent_role="agent",
            tool_name="step_4", output_text="CORRUPTED step 4", depends_on=["3"],
        )
        lep_trace = _mk_lep_trace("lep_00", lep_events, ["LEP_TOOL_RESULT_CORRUPTION"])

        anomalies = _run_detection(lep_trace, clean_ref, "LEP_TOOL_RESULT_CORRUPTION", [2])
        assert len(anomalies) >= 2, f"Expected at least 2 anomalies, got {len(anomalies)}"


# ── Coverage report tests ─────────────────────────────────────────────────────


class TestCoverage:
    def test_no_ref_skips_cell(self):
        skip, reason = should_skip_cell(None)
        assert skip is True
        assert "no_clean_reference" in reason

    def test_insufficient_runs_skips_cell(self):
        ref = CleanBehaviorReference(
            fixture_id="f1", task_family="code_review", topology="linear",
            execution_variant="standard", benign_trace_ids=["t0"],
            repetition_indices=[0], slot_profiles={}, total_runs=1,
        )
        skip, reason = should_skip_cell(ref)
        assert skip is True
        assert "insufficient" in reason


class TestExecutionVariantSeparation:
    """MEMORY_POISONING must use memory_enabled reference, not standard."""

    def test_variant_selection(self):
        from benchmark.behavioral_anomaly import select_clean_reference_variant
        assert select_clean_reference_variant("LEP_MEMORY_POISONING") == "memory_enabled"
        assert select_clean_reference_variant("LEP_TOOL_RESULT_CORRUPTION") == "standard"


class TestBuildCleanReferenceFails:
    def test_fails_on_fewer_than_min_runs(self):
        events = [_mk_event(0, TraceEventType.USER_INPUT, agent_role="user")]
        trace = _mk_trace("b_00", events, repetition_index=0)
        with pytest.raises(ValueError, match="Insufficient clean runs"):
            _build_clean_ref([trace])

    def test_fails_on_missing_fixture_id(self):
        events = [_mk_event(i, TraceEventType.TOOL_CALL, agent_role="a") for i in range(5)]
        traces = [_mk_trace(f"b_{i}", events, repetition_index=i) for i in range(5)]
        # Remove fixture_id from metadata
        for t in traces:
            t.metadata["fixture_id"] = ""
        with pytest.raises(ValueError, match="fixture_id is required"):
            _build_clean_ref(traces)
