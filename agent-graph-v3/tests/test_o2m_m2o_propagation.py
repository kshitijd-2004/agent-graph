"""Tests for O2M/M2O propagation logic in the agent-graph-v3 LEP subsystem.

Covers:
  1. LEPFiringState origin budget (max_origins / fired_origin_count) per mode.
  2. Target-aware filtering in LEPOrchestrator.evaluate_for_boundary (M2O).
  3. mark_fired_origin with target recording.
  4. Topology target resolution (branch:/worker:/upstream: prefix validation).
  5. get_default_topology_target values.
  6. PropagationRole enum members (PROPAGATED, IS_INJECTION).
"""

from __future__ import annotations

import pytest

from leps.registry import LEPFiringState, LEPOrchestrator
from leps.topology_target import (
    InvalidTopologyTargetError,
    TOPOLOGY_TARGETS,
    get_default_topology_target,
    resolve_target_stage,
)
from schemas.edge_labels import PropagationRole
from schemas.lep_config import LEPConfig
from schemas.scenario import WorkflowConfig
from schemas.trace_event import TraceEvent, TraceEventType
from generation.topology import build_agent_map_from_topology, get_topology


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def coordinator_workers_topology():
    """coordinator_workers topology used across M2O tests."""
    am = {
        "coordinator": "agent_001",
        "specialist_a": "agent_002",
        "specialist_b": "agent_003",
        "synthesizer": "agent_004",
    }
    return get_topology("coordinator_workers", am)


@pytest.fixture()
def branch_and_verify_topology():
    """branch_and_verify topology used across O2M / branch-target tests."""
    am = {
        "researcher": "agent_001",
        "analyst": "agent_002",
        "verifier": "agent_003",
    }
    return get_topology("branch_and_verify", am)


@pytest.fixture()
def coordinator_orchestrator(coordinator_workers_topology):
    """LEPOrchestrator pre-registered with a LEP and set to coordinator_workers."""
    orch = LEPOrchestrator()
    lep_cfg = LEPConfig(
        code="LEP_HANDOFF_CORRUPTION",
        name="test_lep",
        category="handoff",
        description="test LEP for propagation tests",
    )
    orch.register_leps([lep_cfg])
    return orch


@pytest.fixture()
def coordinator_orchestrator_m2o(coordinator_orchestrator, coordinator_workers_topology):
    """Orchestrator in many_to_one propagation mode on coordinator_workers."""
    coordinator_orchestrator.set_topology(
        coordinator_workers_topology, propagation_mode="many_to_one"
    )
    return coordinator_orchestrator


def _make_handoff_event(agent_role: str, trace_id: str = "t1") -> TraceEvent:
    """Helper: create a handoff TraceEvent with a given agent_role."""
    return TraceEvent(
        trace_id=trace_id,
        event_id="0",
        event_index=0,
        timestamp="2024-01-01T00:00:00+00:00",
        event_type=TraceEventType.AGENT_HANDOFF,
        agent_role=agent_role,
    )


def _make_tool_result_event(agent_role: str) -> TraceEvent:
    """Helper: create a tool_result TraceEvent with a given agent_role."""
    return TraceEvent(
        trace_id="t1",
        event_id="0",
        event_index=0,
        timestamp="2024-01-01T00:00:00+00:00",
        event_type=TraceEventType.TOOL_RESULT,
        agent_role=agent_role,
    )


# ── 1. Origin budget configuration ───────────────────────────────────────────


class TestLEPFiringStateOriginBudget:
    """LEPFiringState tracks max_origins and fired_origin_count correctly
    for all three propagation modes (single_origin, one_to_many, many_to_one)."""

    def test_single_origin_default_max_is_one(self):
        state = LEPFiringState(max_origins=1)
        assert state.max_origins == 1
        assert state.fired_origin_count == 0
        assert state.fired_targets == set()

    def test_custom_max_origins(self):
        state = LEPFiringState(max_origins=5)
        assert state.max_origins == 5

    def test_single_origin_increment_tracks_count(self):
        state = LEPFiringState(max_origins=1)
        state.fired_origin_count += 1
        assert state.fired_origin_count == 1
        assert state.max_origins == 1

    def test_many_to_one_increment_tracks_count(self):
        """M2O mode: fired_origin_count increments per worker that fires."""
        state = LEPFiringState(max_origins=3)
        state.fired_origin_count += 1
        assert state.fired_origin_count == 1
        state.fired_origin_count += 1
        assert state.fired_origin_count == 2

    def test_one_to_many_increment_tracks_count(self):
        """O2M mode: origin count tracks the single upstream event."""
        state = LEPFiringState(max_origins=1)
        state.fired_origin_count += 1
        assert state.fired_origin_count == 1
        # In O2M consumers are NOT added to fired_targets
        assert state.fired_targets == set()

    def test_fired_targets_tracks_individual_targets(self):
        """M2O mode: fired_targets records each perturbed worker."""
        state = LEPFiringState(max_origins=3)
        state.fired_targets.add("specialist_a")
        state.fired_targets.add("specialist_b")
        assert state.fired_targets == {"specialist_a", "specialist_b"}

    def test_fired_targets_empty_by_default(self):
        state = LEPFiringState(max_origins=10)
        assert state.fired_targets == set()

    def test_eligible_occurrence_counts_empty_by_default(self):
        state = LEPFiringState(max_origins=1)
        assert state.eligible_occurrence_counts == {}

    def test_exhausted_budget(self):
        """When fired_origin_count >= max_origins, no more origins allowed."""
        state = LEPFiringState(max_origins=2)
        state.fired_origin_count = 2
        assert state.fired_origin_count >= state.max_origins

    def test_origin_budget_via_set_max_origins(self):
        """LEPOrchestrator.set_max_origins correctly sets max_origins."""
        orchestrator = LEPOrchestrator()
        lep_cfg = LEPConfig(
            code="LEP_TOOL_RESULT_CORRUPTION",
            name="test",
            category="tool",
            description="test",
        )
        orchestrator.register_leps([lep_cfg])
        # Before set_max_origins: no firing state has been created yet
        # (it is lazily initialized), so get_firing_state returns None.
        assert orchestrator.get_firing_state("LEP_TOOL_RESULT_CORRUPTION") is None

        orchestrator.set_max_origins("LEP_TOOL_RESULT_CORRUPTION", 3)
        state = orchestrator.get_firing_state("LEP_TOOL_RESULT_CORRUPTION")
        assert state is not None
        assert state.max_origins == 3
        assert state.fired_origin_count == 0


# ── 2. Target-aware filtering in evaluate_for_boundary ───────────────────────


class TestEvaluateForBoundaryM2OFiltering:
    """In many_to_one mode with topology_target=None, evaluate_for_boundary
    skips events from the coordinator (exit stage) and workers that have
    already fired, while still evaluating events from unfired workers."""

    def test_coordinator_events_skipped(self, coordinator_orchestrator_m2o):
        """Events from the exit stage (coordinator) are excluded in M2O."""
        coordinator_evt = _make_handoff_event("coordinator")
        result = coordinator_orchestrator_m2o.evaluate_for_boundary(coordinator_evt)
        # Result should be empty because coordinator events are skipped
        # before evaluation even begins
        assert "LEP_HANDOFF_CORRUPTION" not in result

    def test_fired_worker_events_skipped(self, coordinator_orchestrator_m2o):
        """Workers already in fired_targets are excluded."""
        coordinator_orchestrator_m2o.set_max_origins("LEP_HANDOFF_CORRUPTION", 3)
        coordinator_orchestrator_m2o.mark_fired_origin(
            "LEP_HANDOFF_CORRUPTION", target="specialist_a"
        )

        evt = _make_handoff_event("specialist_a")
        result = coordinator_orchestrator_m2o.evaluate_for_boundary(evt)
        assert "LEP_HANDOFF_CORRUPTION" not in result

    def test_unfired_worker_events_are_evaluated(
        self, coordinator_orchestrator_m2o
    ):
        """Workers NOT yet in fired_targets pass the filter and are evaluated."""
        coordinator_orchestrator_m2o.set_max_origins("LEP_HANDOFF_CORRUPTION", 3)
        # Only specialist_a has fired
        coordinator_orchestrator_m2o.mark_fired_origin(
            "LEP_HANDOFF_CORRUPTION", target="specialist_a"
        )

        # specialist_b has NOT fired — it should pass the filter
        evt = _make_handoff_event("specialist_b")
        result = coordinator_orchestrator_m2o.evaluate_for_boundary(evt)
        # The LEP IS evaluated (returned in result dict). The trigger may
        # or may not fire depending on event content, but it is not
        # filtered out by the target-aware logic.
        assert "LEP_HANDOFF_CORRUPTION" in result

    def test_synthesizer_unfired_also_evaluated(self, coordinator_orchestrator_m2o):
        """The synthesizer worker is also eligible when it hasn't fired."""
        coordinator_orchestrator_m2o.set_max_origins("LEP_HANDOFF_CORRUPTION", 3)
        coordinator_orchestrator_m2o.mark_fired_origin(
            "LEP_HANDOFF_CORRUPTION", target="specialist_a"
        )

        evt = _make_handoff_event("synthesizer")
        result = coordinator_orchestrator_m2o.evaluate_for_boundary(evt)
        assert "LEP_HANDOFF_CORRUPTION" in result

    def test_all_workers_fired_then_all_skipped(self, coordinator_orchestrator_m2o):
        """Once every worker has fired, all worker events are skipped."""
        coordinator_orchestrator_m2o.set_max_origins("LEP_HANDOFF_CORRUPTION", 3)
        for worker in ["specialist_a", "specialist_b", "synthesizer"]:
            coordinator_orchestrator_m2o.mark_fired_origin(
                "LEP_HANDOFF_CORRUPTION", target=worker
            )

        state = coordinator_orchestrator_m2o.get_firing_state(
            "LEP_HANDOFF_CORRUPTION"
        )
        assert state.fired_origin_count == 3
        assert state.fired_targets == {
            "specialist_a",
            "specialist_b",
            "synthesizer",
        }

        for role in ["coordinator", "specialist_a", "specialist_b", "synthesizer"]:
            evt = _make_handoff_event(role)
            result = coordinator_orchestrator_m2o.evaluate_for_boundary(evt)
            assert "LEP_HANDOFF_CORRUPTION" not in result

    def test_origin_budget_exhaustion_skips_evaluation(self, coordinator_orchestrator_m2o):
        """When fired_origin_count >= max_origins, no events are evaluated."""
        coordinator_orchestrator_m2o.set_max_origins("LEP_HANDOFF_CORRUPTION", 1)
        coordinator_orchestrator_m2o.mark_fired_origin(
            "LEP_HANDOFF_CORRUPTION", target="specialist_a"
        )

        state = coordinator_orchestrator_m2o.get_firing_state(
            "LEP_HANDOFF_CORRUPTION"
        )
        assert state.fired_origin_count >= state.max_origins

        # Even an unfired worker should be skipped because budget is exhausted
        evt = _make_handoff_event("specialist_b")
        result = coordinator_orchestrator_m2o.evaluate_for_boundary(evt)
        assert "LEP_HANDOFF_CORRUPTION" not in result


# ── 3. mark_fired_origin with target ─────────────────────────────────────────


class TestMarkFiredOriginWithTarget:
    """mark_fired_origin(code, target) increments origin count and records
    the target in fired_targets."""

    def test_mark_fired_origin_increments_count(self, coordinator_orchestrator_m2o):
        coordinator_orchestrator_m2o.set_max_origins("LEP_HANDOFF_CORRUPTION", 3)
        state_before = coordinator_orchestrator_m2o.get_firing_state(
            "LEP_HANDOFF_CORRUPTION"
        )
        assert state_before.fired_origin_count == 0

        coordinator_orchestrator_m2o.mark_fired_origin(
            "LEP_HANDOFF_CORRUPTION", target="worker_a"
        )
        state_after = coordinator_orchestrator_m2o.get_firing_state(
            "LEP_HANDOFF_CORRUPTION"
        )
        assert state_after.fired_origin_count == 1

    def test_mark_fired_origin_records_target(self, coordinator_orchestrator_m2o):
        coordinator_orchestrator_m2o.set_max_origins("LEP_HANDOFF_CORRUPTION", 3)
        coordinator_orchestrator_m2o.mark_fired_origin(
            "LEP_HANDOFF_CORRUPTION", target="specialist_a"
        )

        state = coordinator_orchestrator_m2o.get_firing_state(
            "LEP_HANDOFF_CORRUPTION"
        )
        assert "specialist_a" in state.fired_targets

    def test_mark_fired_origin_multiple_targets_accumulate(
        self, coordinator_orchestrator_m2o
    ):
        coordinator_orchestrator_m2o.set_max_origins("LEP_HANDOFF_CORRUPTION", 3)
        coordinator_orchestrator_m2o.mark_fired_origin(
            "LEP_HANDOFF_CORRUPTION", target="specialist_a"
        )
        coordinator_orchestrator_m2o.mark_fired_origin(
            "LEP_HANDOFF_CORRUPTION", target="specialist_b"
        )
        coordinator_orchestrator_m2o.mark_fired_origin(
            "LEP_HANDOFF_CORRUPTION", target="synthesizer"
        )

        state = coordinator_orchestrator_m2o.get_firing_state(
            "LEP_HANDOFF_CORRUPTION"
        )
        assert state.fired_origin_count == 3
        assert state.fired_targets == {
            "specialist_a",
            "specialist_b",
            "synthesizer",
        }

    def test_mark_fired_origin_target_none_does_not_record(
        self, coordinator_orchestrator_m2o
    ):
        """When target is None, fired_targets is NOT modified."""
        coordinator_orchestrator_m2o.set_max_origins("LEP_HANDOFF_CORRUPTION", 3)
        coordinator_orchestrator_m2o.mark_fired_origin(
            "LEP_HANDOFF_CORRUPTION", target=None
        )
        state = coordinator_orchestrator_m2o.get_firing_state(
            "LEP_HANDOFF_CORRUPTION"
        )
        assert state.fired_origin_count == 1
        assert state.fired_targets == set()

    def test_mark_fired_origin_creates_state_if_missing(self):
        """mark_fired_origin creates a LEPFiringState with max_origins=1 if
        none exists yet for that LEP."""
        orchestrator = LEPOrchestrator()
        orchestrator.mark_fired_origin("LEP_TOOL_RESULT_CORRUPTION", target="researcher")
        state = orchestrator.get_firing_state("LEP_TOOL_RESULT_CORRUPTION")
        assert state is not None
        assert state.max_origins == 1
        assert state.fired_origin_count == 1
        assert "researcher" in state.fired_targets

    def test_mark_fired_origin_subsequent_calls_increment_progressively(
        self, coordinator_orchestrator_m2o
    ):
        coordinator_orchestrator_m2o.set_max_origins("LEP_HANDOFF_CORRUPTION", 3)
        coordinator_orchestrator_m2o.mark_fired_origin(
            "LEP_HANDOFF_CORRUPTION", target="specialist_a"
        )
        assert coordinator_orchestrator_m2o.get_firing_state(
            "LEP_HANDOFF_CORRUPTION"
        ).fired_origin_count == 1

        coordinator_orchestrator_m2o.mark_fired_origin(
            "LEP_HANDOFF_CORRUPTION", target="specialist_b"
        )
        assert coordinator_orchestrator_m2o.get_firing_state(
            "LEP_HANDOFF_CORRUPTION"
        ).fired_origin_count == 2

        coordinator_orchestrator_m2o.mark_fired_origin(
            "LEP_HANDOFF_CORRUPTION", target="synthesizer"
        )
        assert coordinator_orchestrator_m2o.get_firing_state(
            "LEP_HANDOFF_CORRUPTION"
        ).fired_origin_count == 3


# ── 4. Topology target resolution ────────────────────────────────────────────


class _SimpleLEPConfig:
    """Minimal config stand-in for topology_target tests."""

    def __init__(self, topology_target=None):
        self.topology_target = topology_target


class TestTopologyTargetResolution:
    """resolve_target_stage validates prefix/topology/propagation_mode combos."""

    # --- upstream:coordinator valid only for coordinator_workers + one_to_many ---

    def test_upstream_coordinator_coordinator_workers_one_to_many(
        self, coordinator_workers_topology
    ):
        cfg = _SimpleLEPConfig(topology_target="upstream:coordinator")
        result = resolve_target_stage(
            cfg, coordinator_workers_topology, propagation_mode="one_to_many"
        )
        assert result == "coordinator"

    def test_upstream_coordinator_single_origin_raises(
        self, coordinator_workers_topology
    ):
        cfg = _SimpleLEPConfig(topology_target="upstream:coordinator")
        with pytest.raises(InvalidTopologyTargetError, match="one_to_many"):
            resolve_target_stage(cfg, coordinator_workers_topology, propagation_mode="single_origin")

    def test_upstream_coordinator_many_to_one_raises(
        self, coordinator_workers_topology
    ):
        cfg = _SimpleLEPConfig(topology_target="upstream:coordinator")
        with pytest.raises(InvalidTopologyTargetError, match="one_to_many"):
            resolve_target_stage(
                cfg, coordinator_workers_topology, propagation_mode="many_to_one"
            )

    def test_upstream_coordinator_branch_and_verify_raises(
        self, branch_and_verify_topology
    ):
        cfg = _SimpleLEPConfig(topology_target="upstream:coordinator")
        with pytest.raises(InvalidTopologyTargetError):
            resolve_target_stage(cfg, branch_and_verify_topology, propagation_mode="one_to_many")

    # --- branch:researcher only valid for branch_and_verify ---

    def test_branch_researcher_branch_and_verify_resolves(
        self, branch_and_verify_topology
    ):
        cfg = _SimpleLEPConfig(topology_target="branch:researcher")
        result = resolve_target_stage(cfg, branch_and_verify_topology)
        assert result == "researcher"

    def test_branch_researcher_coordinator_workers_raises(
        self, coordinator_workers_topology
    ):
        cfg = _SimpleLEPConfig(topology_target="branch:researcher")
        with pytest.raises(InvalidTopologyTargetError, match="branch_and_verify"):
            resolve_target_stage(cfg, coordinator_workers_topology)

    def test_branch_nonexistent_role_raises(self, branch_and_verify_topology):
        cfg = _SimpleLEPConfig(topology_target="branch:nonexistent")
        with pytest.raises(InvalidTopologyTargetError, match="branch:nonexistent"):
            resolve_target_stage(cfg, branch_and_verify_topology)

    # --- worker:specialist_a only valid for coordinator_workers ---

    def test_worker_specialist_a_coordinator_workers_resolves(
        self, coordinator_workers_topology
    ):
        cfg = _SimpleLEPConfig(topology_target="worker:specialist_a")
        result = resolve_target_stage(cfg, coordinator_workers_topology)
        assert result == "specialist_a"

    def test_worker_specialist_a_branch_and_verify_raises(
        self, branch_and_verify_topology
    ):
        cfg = _SimpleLEPConfig(topology_target="worker:specialist_a")
        with pytest.raises(InvalidTopologyTargetError, match="coordinator_workers"):
            resolve_target_stage(cfg, branch_and_verify_topology)

    def test_worker_nonexistent_role_raises(self, coordinator_workers_topology):
        cfg = _SimpleLEPConfig(topology_target="worker:nonexistent")
        with pytest.raises(InvalidTopologyTargetError, match="worker:nonexistent"):
            resolve_target_stage(cfg, coordinator_workers_topology)

    # --- Other invalid combinations ---

    def test_unknown_prefix_raises(self, branch_and_verify_topology):
        cfg = _SimpleLEPConfig(topology_target="foo:bar")
        with pytest.raises(InvalidTopologyTargetError, match="unrecognized format"):
            resolve_target_stage(cfg, branch_and_verify_topology)

    def test_none_topology_target_returns_none(self, branch_and_verify_topology):
        cfg = _SimpleLEPConfig(topology_target=None)
        assert resolve_target_stage(cfg, branch_and_verify_topology) is None

    def test_missing_attribute_returns_none(self, branch_and_verify_topology):
        cfg = object()
        assert resolve_target_stage(cfg, branch_and_verify_topology) is None

    def test_non_string_topology_target_returns_none(self, branch_and_verify_topology):
        cfg = _SimpleLEPConfig(topology_target=12345)
        assert resolve_target_stage(cfg, branch_and_verify_topology) is None

    def test_branch_resolver_specialist_b(self, coordinator_workers_topology):
        cfg = _SimpleLEPConfig(topology_target="worker:specialist_b")
        result = resolve_target_stage(cfg, coordinator_workers_topology)
        assert result == "specialist_b"

    def test_branch_resolver_synthesizer(self, coordinator_workers_topology):
        cfg = _SimpleLEPConfig(topology_target="worker:synthesizer")
        result = resolve_target_stage(cfg, coordinator_workers_topology)
        assert result == "synthesizer"

    def test_branch_resolver_analyst(self, branch_and_verify_topology):
        cfg = _SimpleLEPConfig(topology_target="branch:analyst")
        result = resolve_target_stage(cfg, branch_and_verify_topology)
        assert result == "analyst"

    def test_upstream_wrong_role_raises(self, coordinator_workers_topology):
        """upstream: prefix only accepts 'coordinator' as the role."""
        cfg = _SimpleLEPConfig(topology_target="upstream:wrong_role")
        with pytest.raises(InvalidTopologyTargetError, match="Canonical valid role"):
            resolve_target_stage(
                cfg, coordinator_workers_topology, propagation_mode="one_to_many"
            )


# ── 5. get_default_topology_target ───────────────────────────────────────────


class TestGetDefaultTopologyTarget:
    """Each topology returns its expected default target dict."""

    def test_linear_2_default(self):
        result = get_default_topology_target("linear_2")
        assert result == {"kind": "stage", "target": "first_agent"}

    def test_linear_3_default(self):
        result = get_default_topology_target("linear_3")
        assert result == {"kind": "stage", "target": "first_agent"}

    def test_review_loop_default(self):
        result = get_default_topology_target("review_loop")
        assert result == {
            "kind": "stage_invocation",
            "target": "producer",
            "invocation": 1,
        }

    def test_branch_and_verify_default(self):
        result = get_default_topology_target("branch_and_verify")
        assert result == {"kind": "branch", "target": "researcher"}

    def test_coordinator_workers_default(self):
        result = get_default_topology_target("coordinator_workers")
        assert result == {"kind": "worker", "target": "worker_a"}

    def test_unknown_topology_returns_none(self):
        assert get_default_topology_target("nonexistent_topology") is None

    def test_all_known_topologies_return_non_none(self):
        for topology_id in TOPOLOGY_TARGETS:
            assert get_default_topology_target(topology_id) is not None

    def test_default_targets_match_topology_targets_dict(self):
        """get_default_topology_target should return the same dict as
        TOPOLOGY_TARGETS[topology_id] for known topologies."""
        for topology_id, expected in TOPOLOGY_TARGETS.items():
            result = get_default_topology_target(topology_id)
            assert result == expected


# ── 6. PropagationRole edge labels ───────────────────────────────────────────


class TestPropagationRoleEnum:
    """PropagationRole enum contains the new propagation-specific members."""

    def test_propagated_member_exists(self):
        assert hasattr(PropagationRole, "PROPAGATED")

    def test_is_injection_member_exists(self):
        assert hasattr(PropagationRole, "IS_INJECTION")

    def test_propagated_value(self):
        assert PropagationRole.PROPAGATED.value == "propagated"

    def test_is_injection_value(self):
        assert PropagationRole.IS_INJECTION.value == "is_injection"

    def test_propagated_is_string_enum(self):
        assert isinstance(PropagationRole.PROPAGATED, str)

    def test_is_injection_is_string_enum(self):
        assert isinstance(PropagationRole.IS_INJECTION, str)

    def test_all_expected_members_present(self):
        """Verify the full set of members includes both new and legacy roles."""
        expected_members = {
            "ORIGIN",
            "TRANSFER",
            "TRANSFORMATION",
            "STORAGE",
            "CONVERGENCE",
            "RECOVERY",
            "TERMINAL_IMPACT",
            "PROPAGATED",
            "IS_INJECTION",
            "UNKNOWN",
        }
        actual_members = {m.name for m in PropagationRole}
        assert expected_members == actual_members

    def test_member_values_are_unique(self):
        values = [m.value for m in PropagationRole]
        assert len(values) == len(set(values)), "PropagationRole values must be unique"

    def test_new_members_are_distinct_from_legacy(self):
        assert PropagationRole.PROPAGATED.value != PropagationRole.ORIGIN.value
        assert PropagationRole.IS_INJECTION.value != PropagationRole.ORIGIN.value
        assert PropagationRole.PROPAGATED.value != PropagationRole.TRANSFER.value
        assert PropagationRole.IS_INJECTION.value != PropagationRole.TRANSFER.value
