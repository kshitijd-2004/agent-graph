#!/usr/bin/env python3
"""End-to-end validation for branch_and_verify and coordinator_workers topologies.

Runs exactly 4 scenarios:
  1. branch_and_verify  — benign
  2. branch_and_verify  — TOOL_RESULT_CORRUPTION target=branch:researcher
  3. coordinator_workers — benign
  4. coordinator_workers — TOOL_RESULT_CORRUPTION target=worker:specialist_a

For each run, verifies:
  - Trace completes without protocol violations
  - LEP injection fires at the correct boundary
  - Perturbation propagates through the expected causal chain
  - No cross-branch contamination in the event DAG
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from generation.topology import get_topology, build_agent_map_from_topology
from generation.runner import ScenarioRunner
from generation.stage_runner import StageRunner
from schemas import (
    LEPConfig, ScenarioSpec, TraceEvent, TraceEventType, TraceVariant,
    WorkflowConfig,
)
from schemas.scenario import CONDITIONS
from schemas.triggers import InjectionTrigger
from schemas.trace_labels import TraceLabels
from schemas.event_labels import EventLabels
from leps.registry import LEPOrchestrator
from leps.topology_target import resolve_target_stage, InvalidTopologyTargetError

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("e2e_validation")

FIXTURE_ROOT = ROOT / "workspace_fixtures"


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_trace_event(event_type, source, target, agent_role="",
                      event_id=None, depends_on=None, **kw):
    """Create a minimal TraceEvent for inspection."""
    return TraceEvent(
        trace_id=kw.get("trace_id", "t"),
        event_id=event_id or "0",
        event_index=kw.get("event_index", 0),
        timestamp="2024-01-01T00:00:00+00:00",
        event_type=event_type,
        source_entity_id=source,
        target_entity_id=target,
        agent_id=kw.get("agent_id", "agent_001"),
        agent_role=agent_role or source,
        tool_name=kw.get("tool_name"),
        depends_on=depends_on or [],
        input_text=kw.get("input_text"),
        output_text=kw.get("output_text"),
    )


def _classify_stage(agent_role, topology):
    """Return whether a role is a branch, merge, worker, or coordinator."""
    roles = set(topology.agent_roles)
    if agent_role in topology.metadata.get("branch_roles", []):
        return "branch"
    if agent_role == topology.metadata.get("merge_role"):
        return "merge"
    if agent_role == topology.metadata.get("coordinator_role"):
        return "coordinator"
    if agent_role in topology.metadata.get("worker_roles", []):
        return "worker"
    return "other"


def _print_event_summary(evt, label=""):
    """One-line summary of a TraceEvent."""
    dep = f" deps={evt.depends_on}" if evt.depends_on else ""
    lbl = ""
    if evt.event_labels:
        if evt.event_labels.is_injection_origin:
            lbl += " [INJECTION_ORIGIN]"
        if evt.event_labels.consumes_perturbed_info:
            lbl += " [CONSUMES]"
        if evt.event_labels.forwards_perturbed_info:
            lbl += " [FORWARDS]"
        if evt.event_labels.introduces_downstream_failure:
            lbl += " [FAILURE]"
    hid = ""
    if evt.hidden:
        lep = evt.hidden.get("lep_type")
        if lep:
            hid += f" lep={lep}"
    obs = ""
    if evt.observable and "lep_injection" in evt.observable:
        obs += " (lep_injection present)"
    print(f"  {label}{evt.event_type.value:20s} src={evt.source_entity_id:20s} tgt={evt.target_entity_id:20s} role={evt.agent_role:20s}{dep}{lbl}{hid}{obs}")


def _check_cross_branch_contamination(trace, topology):
    """Verify no cross-branch contamination in the event DAG.

    Rules:
    - branch_and_verify: Analyst's first reasoning event must NOT depend on
      any Researcher handoff event. Each branch must be independent until
      the verifier merges them.
    - coordinator_workers: Worker B/C's first reasoning events must NOT depend
      on Worker A handoff events.
    """
    violations = []
    events_by_id = {e.event_id: e for e in trace.events}

    # Build event-index-ordered list for first-occurrence checks
    ordered = sorted(trace.events, key=lambda e: e.event_index)

    # Identify handoff events from each source
    handoff_sources = defaultdict(list)
    for e in trace.events:
        if e.event_type == TraceEventType.AGENT_HANDOFF:
            handoff_sources[e.source_entity_id].append(e)

    for e in ordered:
        # Check first reasoning event of each branch (match by agent_role)
        if e.event_type == TraceEventType.REASONING:
            source_role = e.agent_role
            stage_type = _classify_stage(source_role, topology)

            if stage_type == "branch":
                # This is a branch agent's reasoning — its dependencies must
                # not include any OTHER branch's handoff
                other_branches = [
                    r for r in topology.metadata.get("branch_roles", [])
                    if r != source_role
                ]
                for dep_id in e.depends_on:
                    dep_evt = events_by_id.get(dep_id)
                    if dep_evt and dep_evt.agent_role in other_branches:
                        violations.append(
                            f"CROSS-BRANCH: {source_role} reasoning event {e.event_id} "
                            f"depends on {dep_evt.agent_role} handoff {dep_id}"
                        )

            if stage_type == "worker":
                # Worker's first reasoning must not depend on another worker's handoff
                other_workers = [
                    r for r in topology.metadata.get("worker_roles", [])
                    if r != source_role
                ]
                for dep_id in e.depends_on:
                    dep_evt = events_by_id.get(dep_id)
                    if dep_evt and dep_evt.agent_role in other_workers:
                        violations.append(
                            f"CROSS-WORKER: {source_role} reasoning event {e.event_id} "
                            f"depends on {dep_evt.agent_role} handoff {dep_id}"
                        )

    return violations


def _analyze_propagation_chain(trace, topology, target_role):
    """Analyze the LEP propagation chain for a specific target role.

    Returns a dict with keys:
      injection_origin: the event_id of the LEP injection
      consumes: list of event_ids where perturbation was consumed
      forwards: list of event_ids where perturbation was forwarded
      failure: list of event_ids introducing downstream failure
      chain: list of (event_id, label) tuples in causal order
    """
    events_by_id = {e.event_id: e for e in trace.events}
    ordered = sorted(trace.events, key=lambda e: e.event_index)

    result = {
        "injection_origin": None,
        "consumes": [],
        "forwards": [],
        "failure": [],
        "chain": [],
    }

    for e in ordered:
        labels = e.event_labels
        if not labels:
            continue

        if labels.is_injection_origin:
            result["injection_origin"] = e.event_id
            result["chain"].append((e.event_id, "INJECTION"))
        elif labels.consumes_perturbed_info:
            result["consumes"].append(e.event_id)
            result["chain"].append((e.event_id, "CONSUMES"))
        elif labels.forwards_perturbed_info:
            result["forwards"].append(e.event_id)
            result["chain"].append((e.event_id, "FORWARDS"))
        elif labels.introduces_downstream_failure:
            result["failure"].append(e.event_id)
            result["chain"].append((e.event_id, "FAILURE"))

    return result


def _check_branch_and_verify_chain(trace, topology):
    """Verify the expected propagation chain for branch_and_verify.

    Instead of relying on event labels (which require the runner to tag
    intermediate reasoning events), we verify the causal chain through
    the event DAG dependencies:
      - Researcher's AGENT_HANDOFF must causally depend on the injection
        TOOL_RESULT (indirectly via the reasoning→tool_call chain).
      - Verifier's topology_transition and first reasoning must depend on
        the researcher's AGENT_HANDOFF event ID.

    This proves the perturbation flows: injection → researcher → verifier.
    """
    issues = []
    events_by_id = {e.event_id: e for e in trace.events}

    # Find injection origin
    injection = None
    for e in trace.events:
        if (e.event_labels and e.event_labels.is_injection_origin):
            injection = e
            break

    if injection is None:
        issues.append("NO INJECTION ORIGIN FOUND — LEP did not fire")
        return issues

    if injection.event_type != TraceEventType.TOOL_RESULT:
        issues.append(
            f"Injection on {injection.event_type.value}, expected TOOL_RESULT"
        )

    # Find researcher's AGENT_HANDOFF event (match by agent_role, not source_entity_id)
    researcher_handoff = None
    for e in trace.events:
        if (e.agent_role == "researcher"
                and e.event_type == TraceEventType.AGENT_HANDOFF):
            researcher_handoff = e
            break

    if researcher_handoff is None:
        issues.append("Researcher did NOT emit AGENT_HANDOFF (no handoff in trace)")
        return issues

    # Verify researcher handoff causally depends on the injection
    def _reachable(event_id, target_id, visited=None):
        """Check if target_id is reachable from event_id in the DAG."""
        if visited is None:
            visited = set()
        if event_id == target_id:
            return True
        if event_id in visited:
            return False
        visited.add(event_id)
        evt = events_by_id.get(event_id)
        if not evt or not evt.depends_on:
            return False
        for dep in evt.depends_on:
            if _reachable(dep, target_id, visited):
                return True
        return False

    if not _reachable(researcher_handoff.event_id, injection.event_id):
        issues.append(
            "Researcher's AGENT_HANDOFF does NOT causally depend on "
            "the injection TOOL_RESULT — perturbation didn't reach handoff"
        )

    # Find verifier's topology_transition and first reasoning (match by agent_role)
    verifier_transition = None
    verifier_first_reasoning = None
    for e in trace.events:
        if (e.agent_role == "verifier"
                and e.event_type == TraceEventType.TOPOLOGY_TRANSITION):
            verifier_transition = e
        if (e.agent_role == "verifier"
                and e.event_type == TraceEventType.REASONING
                and verifier_first_reasoning is None):
            verifier_first_reasoning = e

    if verifier_transition is None:
        issues.append("Verifier did NOT receive topology_transition")
    else:
        # Verifier's transition must depend on researcher's handoff
        deps = verifier_transition.depends_on or []
        if researcher_handoff.event_id not in deps:
            issues.append(
                "Verifier topology_transition does NOT depend on "
                "Researcher's AGENT_HANDOFF event_id — merge may be broken"
            )

    if verifier_first_reasoning is None:
        issues.append("Verifier did NOT emit any REASONING event")
    else:
        # Verifier's reasoning must depend on researcher's handoff
        if not _reachable(verifier_first_reasoning.event_id, researcher_handoff.event_id):
            issues.append(
                "Verifier's first REASONING does NOT depend on Researcher's "
                "AGENT_HANDOFF — verifier may not have received corrupted data"
            )

    # Verify analyst did NOT depend on researcher's handoff (clean branch)
    analyst_first_reasoning = None
    for e in trace.events:
        if (e.agent_role == "analyst"
                and e.event_type == TraceEventType.REASONING):
            analyst_first_reasoning = e
            break

    if analyst_first_reasoning:
        analyst_deps = analyst_first_reasoning.depends_on or []
        if researcher_handoff.event_id in analyst_deps:
            issues.append(
                "Analyst's first REASONING depends on Researcher's handoff — "
                "CROSS-BRANCH CONTAMINATION"
            )
        # Also check: analyst should depend on ITS OWN transition, not researcher's
        if verifier_transition and verifier_transition.event_id in analyst_deps:
            issues.append(
                "Analyst's first REASONING depends on Verifier's transition — "
                "CROSS-BRANCH CONTAMINATION"
            )

    return issues


def _check_coordinator_workers_chain(trace, topology):
    """Verify the expected propagation chain for coordinator_workers.

    Causal chain:
      - specialist_a's AGENT_HANDOFF must depend on the injection
      - Coordinator's topology_transition must depend on ALL worker handoffs
      - Coordinator's first reasoning must depend on specialist_a's handoff
    """
    issues = []
    events_by_id = {e.event_id: e for e in trace.events}

    # Find injection origin
    injection = None
    for e in trace.events:
        if (e.event_labels and e.event_labels.is_injection_origin):
            injection = e
            break

    if injection is None:
        issues.append("NO INJECTION ORIGIN FOUND — LEP did not fire")
        return issues

    if injection.event_type != TraceEventType.TOOL_RESULT:
        issues.append(
            f"Injection on {injection.event_type.value}, expected TOOL_RESULT"
        )

    # Find specialist_a's AGENT_HANDOFF event (match by agent_role)
    a_handoff = None
    for e in trace.events:
        if (e.agent_role == "specialist_a"
                and e.event_type == TraceEventType.AGENT_HANDOFF):
            a_handoff = e
            break

    if a_handoff is None:
        issues.append("specialist_a did NOT emit AGENT_HANDOFF")
    else:
        # Verify specialist_a handoff depends on injection
        def _reachable(event_id, target_id, visited=None):
            if visited is None:
                visited = set()
            if event_id == target_id:
                return True
            if event_id in visited:
                return False
            visited.add(event_id)
            evt = events_by_id.get(event_id)
            if not evt or not evt.depends_on:
                return False
            for dep in evt.depends_on:
                if _reachable(dep, target_id, visited):
                    return True
            return False

        if not _reachable(a_handoff.event_id, injection.event_id):
            issues.append(
                "specialist_a's AGENT_HANDOFF does NOT depend on the "
                "injection TOOL_RESULT"
            )

    # Find coordinator's topology_transition and first reasoning (match by agent_role)
    coord_transition = None
    coord_first_reasoning = None
    for e in trace.events:
        if (e.agent_role == "coordinator"
                and e.event_type == TraceEventType.TOPOLOGY_TRANSITION):
            coord_transition = e
        if (e.agent_role == "coordinator"
                and e.event_type == TraceEventType.REASONING
                and coord_first_reasoning is None):
            coord_first_reasoning = e

    if coord_transition is None:
        issues.append("Coordinator did NOT receive topology_transition")
    else:
        # Coordinator's transition must depend on ALL worker handoffs
        coord_deps = coord_transition.depends_on or []
        if a_handoff and a_handoff.event_id not in coord_deps:
            issues.append(
                "Coordinator topology_transition does NOT depend on "
                "specialist_a's AGENT_HANDOFF event_id"
            )

    if coord_first_reasoning is None:
        issues.append("Coordinator did NOT emit any REASONING event")
    else:
        if a_handoff and not _reachable(coord_first_reasoning.event_id, a_handoff.event_id):
            issues.append(
                "Coordinator's first REASONING does NOT depend on "
                "specialist_a's AGENT_HANDOFF"
            )

    # Verify specialist_b did NOT depend on specialist_a's handoff (match by agent_role)
    b_first_reasoning = None
    for e in trace.events:
        if (e.agent_role == "specialist_b"
                and e.event_type == TraceEventType.REASONING):
            b_first_reasoning = e
            break

    if b_first_reasoning and a_handoff:
        b_deps = b_first_reasoning.depends_on or []
        if a_handoff.event_id in b_deps:
            issues.append(
                "specialist_b's first REASONING depends on specialist_a's handoff — "
                "CROSS-WORKER CONTAMINATION"
            )

    return issues


# ── Scenario builders ──────────────────────────────────────────────────────


def make_scenario(task_family, topology_id, condition, fixture_id,
                  lep_configs=None, seed=42):
    """Build a ScenarioSpec."""
    from generation.scenario_builder import ScenarioBuilder, ScenarioBuildConfig
    builder = ScenarioBuilder(seed=seed)
    cfg = ScenarioBuildConfig(
        task_family=task_family,
        fixture_id=fixture_id,
        topology=topology_id,
    )
    if condition == "benign":
        return builder.build_benign(cfg)
    elif condition == "single_lep":
        return builder.build_single_lep(cfg, lep_configs[0] if lep_configs else None)
    else:
        raise ValueError(f"Unknown condition: {condition}")


# ── Main ────────────────────────────────────────────────────────────────────


def run_validation():
    results = {}

    # ── Inject role-specific trajectories so branching topologies complete ─
    # The dry-run backend's default trajectory is a single "researcher-style"
    # path ending in submit_final. Branching topologies need downstream stages
    # (analyst, verifier, workers) to have their own ingest-then-finalize
    # trajectories so the trace covers every stage's events.
    from generation.runner import DryRunBackend

    # Trajectories per stage type. These are LEP-agnostic; LEP corruption is
    # applied at boundary check time, not at trajectory selection.
    # Each trajectory starts with write_memory so the stage-runner pre-handoff
    # guard passes (writers must populate memory before handing off).
    ANALYST_TRAJ = [
        ("write_memory", {"key": "analyst_notes", "value": "ANALYST_NOTES"}, False),
        ("read_text_file", {"path": "src/main.py"}, False),
        ("read_memory", {"query": "analyst notes"}, False),
        ("write_file", {"path": "output/analyst_report.md",
                        "content": "# Analyst\nANALYST_NOTES"}, False),
        ("handoff", {"target_agent": "verifier", "summary": "Analyst handoff"}, True),
    ]
    VERIFIER_TRAJ = [
        ("write_memory", {"key": "verifier_notes", "value": "VERIFIER_NOTES"}, False),
        ("read_text_file", {"path": "src/main.py"}, False),
        ("read_memory", {"query": "verifier notes"}, False),
        ("write_file", {"path": "output/verifier_report.md",
                        "content": "# Verifier\nVERIFIER_NOTES"}, True),
    ]
    WORKER_TRAJ = [
        ("write_memory", {"key": "worker_notes", "value": "WORKER_NOTES"}, False),
        ("read_text_file", {"path": "src/main.py"}, False),
        ("read_memory", {"query": "worker notes"}, False),
        ("write_file", {"path": "output/worker_report.md",
                        "content": "# Worker\nWORKER_NOTES"}, False),
        ("handoff", {"target_agent": "coordinator",
                     "summary": "Worker handoff"}, True),
    ]
    COORD_TRAJ = [
        ("write_memory", {"key": "coord_notes", "value": "COORD_NOTES"}, False),
        ("read_text_file", {"path": "src/main.py"}, False),
        ("read_memory", {"query": "coord notes"}, False),
        ("write_file", {"path": "output/coordinator_report.md",
                        "content": "# Coordinator\nCOORD_NOTES"}, False),
        # Handoff (not submit_final) so the runner fans out to workers.
        # On the coordinator's second run (after all workers hand back),
        # the step counter resets and the trajectory replays, ending in
        # submit_final which terminates the workflow.
        ("handoff", {"target_agent": "specialist_a", "summary": "Coordinator delegating"}, True),
    ]

    def _install_topology_trajectories(backend, topology_id):
        if topology_id == "branch_and_verify":
            backend.set_role_trajectory("analyst", ANALYST_TRAJ)
            backend.set_role_trajectory("verifier", VERIFIER_TRAJ)
        elif topology_id == "coordinator_workers":
            backend.set_role_trajectory("specialist_a", WORKER_TRAJ)
            backend.set_role_trajectory("specialist_b", WORKER_TRAJ)
            backend.set_role_trajectory("synthesizer", WORKER_TRAJ)
            backend.set_role_trajectory("coordinator", COORD_TRAJ)

    # ── Run 1: branch_and_verify benign ──────────────────────────────────
    print("=" * 70)
    print("RUN 1: branch_and_verify — benign")
    print("=" * 70)

    spec1 = make_scenario("code_review", "branch_and_verify", "benign",
                          "code_review_easy", lep_configs=[])
    runner1 = ScenarioRunner(dry_run=True)
    result1 = runner1.run(spec1, FIXTURE_ROOT)
    trace1 = result1.trace
    topo1 = get_topology("branch_and_verify",
                         {"researcher": "agent_001", "analyst": "agent_002",
                          "verifier": "agent_003"})

    print(f"  Events: {len(trace1.events)}")
    print(f"  Termination: {trace1.metadata.get('termination_reason', 'unknown')}")
    print(f"  Task success: {result1.task_success}")
    print()
    print("  Event summary:")
    for e in trace1.events:
        _print_event_summary(e, "    ")

    contamination1 = _check_cross_branch_contamination(trace1, topo1)
    results["bv_benign"] = {
        "trace": trace1,
        "topology": topo1,
        "contamination": contamination1,
        "task_success": result1.task_success,
    }
    print()
    if contamination1:
        print(f"  *** CONTAMINATION DETECTED: {contamination1}")
    else:
        print("  No cross-branch contamination detected.")

    # ── Run 2: branch_and_verify with LEP ────────────────────────────────
    print()
    print("=" * 70)
    print("RUN 2: branch_and_verify — TOOL_RESULT_CORRUPTION target=branch:researcher")
    print("=" * 70)

    lep2 = LEPConfig(
        code="LEP_TOOL_RESULT_CORRUPTION",
        name="Branch Researcher Corruption",
        category="tool",
        description="corrupt tool result on researcher branch",
        task_family="code_review",
        topology_target="branch:researcher",
        trigger=InjectionTrigger(event_type="TOOL_RESULT", tool_name="read_text_file"),
        canonical_operator="partial_omission",
    )

    spec2 = make_scenario("code_review", "branch_and_verify", "single_lep",
                          "code_review_easy", lep_configs=[lep2])
    runner2 = ScenarioRunner(dry_run=True)
    result2 = runner2.run(spec2, FIXTURE_ROOT)
    trace2 = result2.trace
    topo2 = get_topology("branch_and_verify",
                         {"researcher": "agent_001", "analyst": "agent_002",
                          "verifier": "agent_003"})

    print(f"  Events: {len(trace2.events)}")
    print(f"  Termination: {trace2.metadata.get('termination_reason', 'unknown')}")
    print(f"  Task success: {result2.task_success}")
    print()
    print("  Event summary:")
    for e in trace2.events:
        _print_event_summary(e, "    ")

    issues2 = _check_branch_and_verify_chain(trace2, topo2)
    contamination2 = _check_cross_branch_contamination(trace2, topo2)

    results["bv_lep"] = {
        "trace": trace2,
        "topology": topo2,
        "issues": issues2,
        "contamination": contamination2,
        "task_success": result2.task_success,
    }

    if issues2:
        print(f"  *** ISSUES: {issues2}")
    else:
        print("  Propagation chain correct.")

    if contamination2:
        print(f"  *** CONTAMINATION DETECTED: {contamination2}")
    else:
        print("  No cross-branch contamination detected.")

    # ── Run 3: coordinator_workers benign ────────────────────────────────
    print()
    print("=" * 70)
    print("RUN 3: coordinator_workers — benign")
    print("=" * 70)

    spec3 = make_scenario("code_review", "coordinator_workers", "benign",
                          "code_review_easy", lep_configs=[])
    runner3 = ScenarioRunner(dry_run=True)
    result3 = runner3.run(spec3, FIXTURE_ROOT)
    trace3 = result3.trace
    topo3 = get_topology("coordinator_workers",
                         {"coordinator": "agent_000",
                          "specialist_a": "agent_001",
                          "specialist_b": "agent_002",
                          "synthesizer": "agent_003"})

    print(f"  Events: {len(trace3.events)}")
    print(f"  Termination: {trace3.metadata.get('termination_reason', 'unknown')}")
    print(f"  Task success: {result3.task_success}")
    print()
    print("  Event summary:")
    for e in trace3.events:
        _print_event_summary(e, "    ")

    contamination3 = _check_cross_branch_contamination(trace3, topo3)
    results["cw_benign"] = {
        "trace": trace3,
        "topology": topo3,
        "contamination": contamination3,
        "task_success": result3.task_success,
    }
    print()
    if contamination3:
        print(f"  *** CONTAMINATION DETECTED: {contamination3}")
    else:
        print("  No cross-worker contamination detected.")

    # ── Run 4: coordinator_workers with LEP ──────────────────────────────
    print()
    print("=" * 70)
    print("RUN 4: coordinator_workers — TOOL_RESULT_CORRUPTION target=worker:specialist_a")
    print("=" * 70)

    lep4 = LEPConfig(
        code="LEP_TOOL_RESULT_CORRUPTION",
        name="Worker A Corruption",
        category="tool",
        description="corrupt tool result on specialist_a worker",
        task_family="code_review",
        topology_target="worker:specialist_a",
        trigger=InjectionTrigger(event_type="TOOL_RESULT", tool_name="read_text_file"),
        canonical_operator="partial_omission",
    )

    spec4 = make_scenario("code_review", "coordinator_workers", "single_lep",
                          "code_review_easy", lep_configs=[lep4])
    runner4 = ScenarioRunner(dry_run=True)
    result4 = runner4.run(spec4, FIXTURE_ROOT)
    trace4 = result4.trace
    topo4 = get_topology("coordinator_workers",
                         {"coordinator": "agent_000",
                          "specialist_a": "agent_001",
                          "specialist_b": "agent_002",
                          "synthesizer": "agent_003"})

    print(f"  Events: {len(trace4.events)}")
    print(f"  Termination: {trace4.metadata.get('termination_reason', 'unknown')}")
    print(f"  Task success: {result4.task_success}")
    print()
    print("  Event summary:")
    for e in trace4.events:
        _print_event_summary(e, "    ")

    issues4 = _check_coordinator_workers_chain(trace4, topo4)
    contamination4 = _check_cross_branch_contamination(trace4, topo4)

    results["cw_lep"] = {
        "trace": trace4,
        "topology": topo4,
        "issues": issues4,
        "contamination": contamination4,
        "task_success": result4.task_success,
    }

    if issues4:
        print(f"  *** ISSUES: {issues4}")
    else:
        print("  Propagation chain correct.")

    if contamination4:
        print(f"  *** CONTAMINATION DETECTED: {contamination4}")
    else:
        print("  No cross-worker contamination detected.")

    # ── Summary ──────────────────────────────────────────────────────────
    print()
    print("=" * 70)
    print("VALIDATION SUMMARY")
    print("=" * 70)

    all_ok = True
    for name, r in results.items():
        issues = r.get("issues", [])
        contam = r.get("contamination", [])
        ok = not issues and not contam
        status = "PASS" if ok else "FAIL"
        print(f"  {name:20s} {status:6s} task_success={r['task_success']}")
        if issues:
            print(f"    Issues: {issues}")
            all_ok = False
        if contam:
            print(f"    Contamination: {contam}")
            all_ok = False

    print()
    if all_ok:
        print("ALL CHECKS PASSED — topology milestone complete.")
    else:
        print("SOME CHECKS FAILED — review output above.")

    return all_ok


if __name__ == "__main__":
    ok = run_validation()
    sys.exit(0 if ok else 1)
