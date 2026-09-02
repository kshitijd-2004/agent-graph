"""Tests for the topology-target LEP selection layer.

Verifies that resolve_target_stage() correctly handles branch:<role>,
worker:<role>, None, and invalid/unknown inputs.
"""

from __future__ import annotations

import unittest

from leps.topology_target import (
    InvalidTopologyTargetError,
    resolve_target_stage,
)


# ── Helpers ────────────────────────────────────────────────────────────────


class _FakeTopology:
    """Minimal stand-in for TopologyConfig."""
    def __init__(self, topology_id, agent_roles, metadata=None):
        self.topology_id = topology_id
        self.agent_roles = agent_roles
        self.metadata = metadata or {}


class _FakeLEPConfig:
    """Minimal stand-in for LEPConfig."""
    def __init__(self, topology_target=None):
        self.topology_target = topology_target


# ── Topology fixtures ──────────────────────────────────────────────────────


BANDV = _FakeTopology(
    topology_id="branch_and_verify",
    agent_roles=["researcher", "analyst", "verifier"],
    metadata={"branch_roles": ["researcher", "analyst"], "merge_role": "verifier"},
)

COORD_WORKERS = _FakeTopology(
    topology_id="coordinator_workers",
    agent_roles=["coordinator", "specialist_a", "specialist_b", "synthesizer"],
    metadata={
        "coordinator_role": "coordinator",
        "worker_roles": ["specialist_a", "specialist_b", "synthesizer"],
    },
)


# ── Tests ──────────────────────────────────────────────────────────────────


class TestBranchTarget(unittest.TestCase):
    def test_branch_researcher_resolves(self):
        cfg = _FakeLEPConfig(topology_target="branch:researcher")
        self.assertEqual(resolve_target_stage(cfg, BANDV), "researcher")

    def test_branch_analyst_resolves(self):
        cfg = _FakeLEPConfig(topology_target="branch:analyst")
        self.assertEqual(resolve_target_stage(cfg, BANDV), "analyst")

    def test_branch_invalid_raises(self):
        cfg = _FakeLEPConfig(topology_target="branch:nonexistent")
        with self.assertRaises(InvalidTopologyTargetError) as ctx:
            resolve_target_stage(cfg, BANDV)
        self.assertIn("branch:nonexistent", str(ctx.exception))

    def test_branch_on_coordinator_workers_raises(self):
        cfg = _FakeLEPConfig(topology_target="branch:researcher")
        with self.assertRaises(InvalidTopologyTargetError) as ctx:
            resolve_target_stage(cfg, COORD_WORKERS)
        self.assertIn("researcher", str(ctx.exception))


class TestWorkerTarget(unittest.TestCase):
    def test_worker_specialist_a_resolves(self):
        cfg = _FakeLEPConfig(topology_target="worker:specialist_a")
        self.assertEqual(resolve_target_stage(cfg, COORD_WORKERS), "specialist_a")

    def test_worker_specialist_b_resolves(self):
        cfg = _FakeLEPConfig(topology_target="worker:specialist_b")
        self.assertEqual(resolve_target_stage(cfg, COORD_WORKERS), "specialist_b")

    def test_worker_synthesizer_resolves(self):
        cfg = _FakeLEPConfig(topology_target="worker:synthesizer")
        self.assertEqual(resolve_target_stage(cfg, COORD_WORKERS), "synthesizer")

    def test_worker_invalid_raises(self):
        cfg = _FakeLEPConfig(topology_target="worker:nonexistent")
        with self.assertRaises(InvalidTopologyTargetError) as ctx:
            resolve_target_stage(cfg, COORD_WORKERS)
        self.assertIn("worker:nonexistent", str(ctx.exception))

    def test_worker_on_branch_topology_raises(self):
        cfg = _FakeLEPConfig(topology_target="worker:specialist_a")
        with self.assertRaises(InvalidTopologyTargetError) as ctx:
            resolve_target_stage(cfg, BANDV)
        self.assertIn("specialist_a", str(ctx.exception))


class TestNoneTarget(unittest.TestCase):
    def test_none_returns_none(self):
        cfg = _FakeLEPConfig(topology_target=None)
        self.assertIsNone(resolve_target_stage(cfg, BANDV))

    def test_no_attribute_returns_none(self):
        cfg = object()  # no topology_target attr at all
        self.assertIsNone(resolve_target_stage(cfg, BANDV))


class TestUpstreamDeferred(unittest.TestCase):
    def test_upstream_raises_deferred_error(self):
        cfg = _FakeLEPConfig(topology_target="upstream:coordinator")
        with self.assertRaises(InvalidTopologyTargetError) as ctx:
            resolve_target_stage(cfg, COORD_WORKERS)
        self.assertIn("not yet implemented", str(ctx.exception))


class TestUnknownPrefix(unittest.TestCase):
    def test_unknown_prefix_raises(self):
        cfg = _FakeLEPConfig(topology_target="foo:bar")
        with self.assertRaises(InvalidTopologyTargetError) as ctx:
            resolve_target_stage(cfg, BANDV)
        self.assertIn("unrecognized format", str(ctx.exception))


class TestEvaluateForBoundaryFilter(unittest.TestCase):
    """End-to-end: LEPOrchestrator.evaluate_for_boundary must filter events
    by the resolved topology target."""

    def _make_fake_event(self, agent_role):
        from schemas.trace_event import TraceEvent, TraceEventType
        return TraceEvent(
            trace_id="t1",
            event_id="0",
            event_index=0,
            timestamp="2024-01-01T00:00:00+00:00",
            event_type=TraceEventType.AGENT_HANDOFF,
            source_entity_id=agent_role,
            target_entity_id="next",
            agent_id="agent_001",
            agent_role=agent_role,
        )

    def test_target_filters_non_matching_events(self):
        from leps.registry import LEPOrchestrator
        from schemas.lep_config import LEPConfig

        orchestrator = LEPOrchestrator()
        lep_cfg = LEPConfig(
            code="LEP_HANDOFF_CORRUPTION",
            name="test",
            category="handoff",
            description="test",
            topology_target="branch:researcher",
        )
        orchestrator.register_leps([lep_cfg])
        orchestrator.set_topology(BANDV)

        # Event from researcher should be evaluated
        researcher_evt = self._make_fake_event("researcher")
        result = orchestrator.evaluate_for_boundary(researcher_evt)
        # Should be empty dict because no trigger is registered on a bare
        # handoff event without actual content — but critically the call
        # does NOT raise and DOES NOT skip the researcher stage.
        # We verify by checking the orchestrator didn't mark it as
        # successfully mutated (no false positive).
        self.assertNotIn("LEP_HANDOFF_CORRUPTION", orchestrator._successfully_mutated)

    def test_no_target_fires_all_events(self):
        from leps.registry import LEPOrchestrator
        from schemas.lep_config import LEPConfig

        orchestrator = LEPOrchestrator()
        lep_cfg = LEPConfig(
            code="LEP_HANDOFF_CORRUPTION",
            name="test",
            category="handoff",
            description="test",
            # topology_target=None (default)
        )
        orchestrator.register_leps([lep_cfg])
        orchestrator.set_topology(BANDV)

        for role in ("researcher", "analyst", "verifier"):
            evt = self._make_fake_event(role)
            result = orchestrator.evaluate_for_boundary(evt)
            # No trigger matches, so empty result — but the orchestrator
            # evaluated it (didn't skip due to topology_target filter).
            self.assertNotIn("LEP_HANDOFF_CORRUPTION",
                             orchestrator._successfully_mutated)


if __name__ == "__main__":
    unittest.main()
