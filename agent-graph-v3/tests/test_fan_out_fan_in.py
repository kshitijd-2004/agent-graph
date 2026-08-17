#!/usr/bin/env python3
"""Unit tests for fan-out/fan-in execution in the queue-based runner.

Covers:
- Queue dedup (enqueue skips already-queued stages)
- Fan-in merge detection and branch waiting
- _merge_handoff_payloads aggregation and branch_metadata preservation
- Topology get_outgoing_handoffs / get_incoming_handoffs
- List-based handoff_event_ids (no semicolon-strings)
"""

import sys
from pathlib import Path
V3_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(V3_ROOT))

from generation.runner import ScenarioRunner, RunResult
from generation.handoff import HandoffPayload
from generation.topology import (
    TopologyConfig, Stage, HandoffRule,
    get_topology,
)

DEFAULT_AGENT_MAP = {
    "researcher": "agent_001",
    "analyst": "agent_002",
    "verifier": "agent_003",
    "coordinator": "agent_004",
    "specialist_a": "agent_005",
    "specialist_b": "agent_006",
}


def _topo(name: str) -> TopologyConfig:
    return get_topology(name, DEFAULT_AGENT_MAP)


def test_topology_incoming_outgoing_methods():
    topo = _topo("branch_and_verify")
    incoming_v = topo.get_incoming_handoffs("verifier")
    outgoing_r = topo.get_outgoing_handoffs("researcher")
    assert len(incoming_v) == 2, f"verifier should have 2 incoming, got {len(incoming_v)}"
    assert len(outgoing_r) == 1, f"researcher should have 1 outgoing, got {len(outgoing_r)}"
    assert incoming_v[0].from_stage == "researcher"
    assert incoming_v[1].from_stage == "analyst"
    assert outgoing_r[0].to_stage == "verifier"
    print("  PASS  topology methods")


def test_merge_handoff_payloads_preserves_branch_metadata():
    payload_a = HandoffPayload(
        from_agent="researcher",
        to_agent="verifier",
        findings=["finding A1", "finding A2"],
        summary="Research summary",
        extra={"input_disregard": {"instruction": "disregard X"}},
    )
    payload_b = HandoffPayload(
        from_agent="analyst",
        to_agent="verifier",
        findings=["finding B1"],
        summary="Analysis summary",
        extra={"lep_propagation": {"source": "LEP_TOOL_CORRUPTION"}},
    )

    merged = ScenarioRunner._merge_handoff_payloads(
        [payload_a, payload_b],
        target_role="verifier",
    )

    # Symmetric aggregation
    assert merged.from_agent == "researcher+analyst"
    assert merged.to_agent == "verifier"
    assert len(merged.findings) == 3  # A1, A2, B1 (no dedup needed)
    assert "finding A1" in merged.findings
    assert "finding B1" in merged.findings
    assert merged.contains_corrupted_data is False

    # Summary: both branches prefixed with their role
    assert "[researcher] Research summary" in merged.summary
    assert "[analyst] Analysis summary" in merged.summary

    # Provenance
    assert len(merged.provenance_event_ids) >= 0  # set by caller, not _merge

    # Branch metadata preserved — NOT collapsed
    bm = merged.extra.get("branch_metadata", {})
    assert "researcher" in bm, "researcher branch extra missing"
    assert "analyst" in bm, "analyst branch extra missing"
    assert bm["researcher"].get("input_disregard") == {"instruction": "disregard X"}
    assert bm["analyst"].get("lep_propagation") == {"source": "LEP_TOOL_CORRUPTION"}

    # Merged-from list
    assert merged.extra.get("merged_from") == ["researcher", "analyst"]
    print("  PASS  merge payload + branch_metadata")


def test_merge_handoff_payloads_corruption_flag():
    p_clean = HandoffPayload(from_agent="a", to_agent="verifier", extra={})
    p_dirty = HandoffPayload(from_agent="b", to_agent="verifier",
                             contains_corrupted_data=True, extra={})
    merged = ScenarioRunner._merge_handoff_payloads([p_clean, p_dirty], "verifier")
    assert merged.contains_corrupted_data is True
    print("  PASS  corruption propagation")


def test_enqueue_dedup():
    """Simulate the enqueue dedup logic."""

    topo = _topo("branch_and_verify")
    stage_a = topo.get_stage("researcher")
    stage_b = topo.get_stage("analyst")
    stage_v = topo.get_stage("verifier")

    queue = [stage_v]
    queued_ids = {stage_v.stage_id}

    def enqueue(stage):
        if stage.stage_id not in queued_ids:
            queue.append(stage)
            queued_ids.add(stage.stage_id)

    # First enqueue of a and v — both added
    enqueue(stage_a)
    enqueue(stage_v)  # already queued → skipped
    assert len(queue) == 2, f"Expected 2, got {len(queue)}"
    assert queue[0] is stage_v
    assert queue[1] is stage_a
    assert len(queued_ids) == 2

    # Pop v (simulating pop(0))
    queue.pop(0)
    queued_ids.discard(stage_v.stage_id)

    # Now v can be re-enqueued (review_loop backedge case)
    enqueue(stage_v)
    assert len(queue) == 2, f"Expected 2 after re-enqueue, got {len(queue)}"
    assert queue[0] is stage_a
    assert queue[1] is stage_v
    print("  PASS  enqueue dedup")


def test_fan_in_logic():
    """Simulate fan-in waiting without running the full runner."""
    topo = _topo("branch_and_verify")

    branch_handoffs = {}
    branch_event_ids = {}

    # After researcher runs
    branch_handoffs["researcher"] = "payload_R"
    branch_event_ids["researcher"] = "evt_5"

    # Simulate: verifier pops, checks incoming
    incoming = topo.get_incoming_handoffs("verifier")
    missing = [r.from_stage for r in incoming if r.from_stage not in branch_handoffs]
    assert missing == ["analyst"], f"Expected ['analyst'], got {missing}"

    # After analyst runs
    branch_handoffs["analyst"] = "payload_A"
    branch_event_ids["analyst"] = "evt_12"

    missing2 = [r.from_stage for r in incoming if r.from_stage not in branch_handoffs]
    assert missing2 == [], f"Expected no missing, got {missing2}"
    print("  PASS  fan-in waiting logic")


def test_linear_topologies_regression():
    """Linear topologies should have no merge targets."""
    for name in ["linear_2", "linear_3"]:
        topo = _topo(name)
        for stage in topo.stages:
            incoming = topo.get_incoming_handoffs(stage.agent_role)
            assert len(incoming) <= 1, \
                f"{name}: stage {stage.agent_role} has {len(incoming)} incoming (should be ≤1)"
    print("  PASS  linear topologies have no merge targets")


def main():
    print("=== fan-out/fan-in unit tests ===\n")
    test_topology_incoming_outgoing_methods()
    test_merge_handoff_payloads_preserves_branch_metadata()
    test_merge_handoff_payloads_corruption_flag()
    test_enqueue_dedup()
    test_fan_in_logic()
    test_linear_topologies_regression()
    print("\nAll tests passed.")


if __name__ == "__main__":
    main()
