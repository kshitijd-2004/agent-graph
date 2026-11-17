"""Scheduler regression: coordinator_workers must always begin with the
coordinator, independently of LEP propagation mode.

Regression target:
  - Before the fix, the `initial_fanout` gate-bypass was gated on
    ``propagation_mode == "one_to_many"``. Non-O2M runs (benign / single_origin)
    therefore re-queued the coordinator and executed WORKERS FIRST.
  - After the fix, the bypass is derived from the topology's own structure
    (``TopologyConfig.is_coordinator_first``), so benign, SO, and O2M runs all
    start coordinator-first, delegate to workers before any worker executes,
    and only then aggregate.

These tests run the real ScenarioRunner on CPU-only (no GPU, no LLM) with a
deterministic recording backend, and additionally verify that
``review_loop`` / ``branch_and_verify`` scheduling is unchanged (they are not
coordinator-first topologies, so they do not take the new path).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.api_backend import ModelTurn, ToolCall
from generation.runner import ScenarioRunner
from generation.scenario_builder import ScenarioBuilder, ScenarioBuildConfig
from generation.topology import get_topology
from schemas import LEPConfig
from schemas.triggers import InjectionTrigger
from schemas.trace_event import TraceEventType

FIXTURE_ROOT = ROOT / "workspace_fixtures"


# ── Deterministic recording backend ────────────────────────────────────────


class RecordingBackend:
    """Follows the tool the runner exposes to drive clean topology ordering.

    - Coordinator: on its FIRST visit (before any worker has run) it must
      delegate (handoff to specialist_a); on every LATER visit it finalizes
      (aggregates the worker returns). This mirrors the intended protocol:
      coordinator → workers → aggregate → coordinator processes results.
    - Workers / synthesizer: one write_memory + one read_text_file, then
      hand back to the coordinator (the fan-in edge).

    Records ``(agent_role, agent_id)`` in visit order so tests can assert on
    the exact scheduling sequence.
    """

    def __init__(self):
        self.visits: list = []
        self._exec: dict = {}   # role -> how many stage-executions of it
        self._turn = 0          # turns within the current stage execution

    def reset(self, task="", agent_name="", mcp_tools=None, system_prompt=""):
        # reset() is called ONCE per stage execution (not per turn).
        self._exec[agent_name] = self._exec.get(agent_name, 0) + 1
        self._turn = 0
        self.visits.append(agent_name)

    def generate(self, prompt, tool_choice=None):
        role = self.visits[-1]
        exec_no = self._exec[role]      # which stage-execution of this role
        self._turn += 1
        step = self._turn              # which turn within that execution
        if role == "coordinator":
            if exec_no == 1:
                # First visit (delegation turn): read the task (this is where
                # an O2M origin LEP fires on the coordinator), then delegate.
                if step == 1:
                    name, args = "write_memory", {"key": "coord_notes", "value": "COORD_NOTES"}
                elif step == 2:
                    name, args = "read_text_file", {"path": "src/main.py"}
                else:
                    name, args = "handoff", {"target_agent": "specialist_a",
                                             "summary": "Coordinator delegating."}
            else:
                # Later visit after fan-in: aggregate the worker returns and
                # finalize.
                name, args = "submit_final", {"summary": "Coordinator aggregating."}
        elif step == 1:
            name, args = "write_memory", {"key": "notes", "value": "WORKER_NOTES"}
        elif step == 2:
            name, args = "read_text_file", {"path": "src/main.py"}
        else:
            name, args = "handoff", {"target_agent": "coordinator",
                                     "summary": "Worker handoff."}
        return ModelTurn(
            tool_call=ToolCall(id=f"tc-{role}-{exec_no}-{step}", name=name, input=args),
            text=f"[{role}:{exec_no}/{step}] {name}", stop_reason="tool_use",
        )

    def parse_action(self, raw_response):
        return None

    def _append_tool_result(self, tool_call, result):
        pass


def _order(trace):
    return [e.agent_role for e in trace.events
            if e.event_type == TraceEventType.TOPOLOGY_TRANSITION]


# The EXACT structural extraction BND V4.1 applies (benchmark/baseline_deviation.py,
# re-exported unchanged into baseline_deviation_v41 via `import baseline_deviation as bd`).
# A regression that re-introduces a serial relay changes execution_shape and is caught here.
from benchmark.baseline_deviation import execution_shape as _bnd_execution_shape


def _cw_shape(trace):
    """BND V4.1 execution_shape of a CW trace, as a comparable canonical dict.

    Canonical fan-out/fan-in == coordinator is the only root, every worker's
    transition depends_on the coordinator's delegation artifact, the
    coordinator's aggregation depends_on all three workers, and there is NO
    worker-to-worker edge (sp_b must not depend on sp_a).
    """
    return _bnd_execution_shape(trace.to_dict())


def _assert_canonical_cw_shape(shape):
    transfers = set(shape["transfers"])
    assert shape["initial_roles"] == ["coordinator"], (
        f"canonical CW must root at the coordinator, got {shape['initial_roles']}"
    )
    for worker in ("specialist_a", "specialist_b", "synthesizer"):
        assert ("coordinator", worker) in transfers, (
            f"worker {worker} must consume the coordinator's artifact (fan-out), "
            f"transfers={sorted(transfers)}"
        )
        assert (worker, "coordinator") in transfers, (
            f"worker {worker} must hand back to the coordinator (fan-in)"
        )
    # No unintended worker-to-worker relay: the whole point of the fix.
    for a, b in (("specialist_a", "specialist_b"), ("specialist_b", "synthesizer")):
        assert (a, b) not in transfers, (
            f"worker-to-worker relay {a}->{b} present — CW is not canonical fan-out"
        )


def _build(mode, target=None):
    builder = ScenarioBuilder(seed=42)
    cfg = ScenarioBuildConfig(
        task_family="code_review",
        fixture_id="code_review_easy",
        topology="coordinator_workers",
        propagation_mode=mode,
    )
    if target is None:
        return builder.build_benign(cfg)
    lep = LEPConfig(
        code="LEP_TOOL_RESULT_CORRUPTION",
        name="Worker A corruption",
        category="tool",
        description="corrupt specialist_a tool result",
        task_family="code_review",
        topology_target=target,
        trigger=InjectionTrigger(event_type="TOOL_RESULT",
                                 tool_name="read_text_file"),
        canonical_operator="partial_omission",
    )
    return builder.build_single_lep(cfg, lep)


def _run(mode, target=None):
    backend = RecordingBackend()
    runner = ScenarioRunner(llm_backend=backend, dry_run=False)
    result = runner.run(_build(mode, target), FIXTURE_ROOT)
    return result, _order(result.trace), backend


# ── 1-3. Coordinator-first in every propagation mode ───────────────────────


@pytest.mark.parametrize("mode,target", [
    ("single_origin", None),                    # benign coordinator-workers
    ("single_origin", "worker:specialist_a"),    # SO LEP
    ("one_to_many", "upstream:coordinator"),     # O2M LEP
])
def test_coordinator_workers_starts_with_coordinator(mode, target):
    result, order, _ = _run(mode, target)
    assert result.runner_success, result.error
    assert order, "no topology transitions recorded — scheduler did not fan out"
    # The invariant under test: the workflow opens with the coordinator.
    assert order[0] == "coordinator", (
        f"coordinator_workers ({mode}) must start with the coordinator, "
        f"got {order[:4]} — workers ran before delegation"
    )


# ── 4. Coordinator delegates BEFORE workers execute ─────────────────────────


def test_coordinator_delegates_before_any_worker():
    result, order, _ = _run("single_origin")
    workers = {"specialist_a", "specialist_b", "synthesizer"}
    first_worker = next(i for i, r in enumerate(order) if r in workers)
    # The coordinator's opening transition (index 0) precedes the first
    # worker transition, and is followed by a delegation handoff.
    assert order[0] == "coordinator"
    assert first_worker > 0, "a worker ran before the coordinator's opening turn"


# ── 5. All workers return via valid handoffs ────────────────────────────────


def test_all_workers_return_to_coordinator():
    result, order, _ = _run("single_origin")
    final = result.termination_reason
    assert final == "completed", (
        f"expected clean finalization, got '{final}' — a worker handoff "
        f"was likely invalid or lost"
    )
    assert result.trace.events[-1].event_type == TraceEventType.FINAL_RESPONSE


# ── 6. Coordinator processes aggregated results on its second visit ───────


def test_coordinator_aggregates_on_second_visit():
    result, order, backend = _run("single_origin")
    coord_visits = [i for i, r in enumerate(backend.visits) if r == "coordinator"]
    # Coordinator runs at least twice: delegation, then aggregation.
    assert len(coord_visits) >= 2, (
        "coordinator only ran once — no aggregation turn after fan-in"
    )


# ── 7. The workflow terminates correctly (bounded, no infinite loop) ───────


@pytest.mark.parametrize("mode,target", [
    ("single_origin", None),
    ("single_origin", "worker:specialist_a"),
    ("one_to_many", "upstream:coordinator"),
])
def test_coordinator_workers_terminates_cleanly(mode, target):
    result, order, _ = _run(mode, target)
    assert result.runner_success, result.error
    assert result.termination_reason == "completed", result.termination_reason
    # No unbounded re-delegation: the coordinator appears a bounded number of
    # times and the trace ends in a final response.
    assert order.count("coordinator") <= 2
    assert result.trace.events[-1].event_type == TraceEventType.FINAL_RESPONSE


# ── O2M shared-artifact semantics preserved ─────────────────────────────────


def test_o2m_workers_share_one_coordinator_artifact():
    result, order, _ = _run("one_to_many", "upstream:coordinator")
    events = result.trace.events
    by_id = {e.event_id: e for e in events}
    # The coordinator's first handoff event is the shared artifact.
    coord_handoffs = [e for e in events
                     if e.agent_role == "coordinator"
                     and e.event_type == TraceEventType.AGENT_HANDOFF]
    assert coord_handoffs, "coordinator did not emit a delegation handoff"
    artifact_id = coord_handoffs[0].event_id
    # In one_to_many, every worker's FIRST transition consumes that ONE
    # artifact (shared "read"), not independent copies.
    first_worker_trans = {}
    for e in events:
        if e.event_type == TraceEventType.TOPOLOGY_TRANSITION and e.agent_role in (
                "specialist_a", "specialist_b", "synthesizer"):
            first_worker_trans.setdefault(e.agent_role, e)
    assert len(first_worker_trans) == 3
    for role, e in first_worker_trans.items():
        assert e.depends_on and artifact_id in e.depends_on, (
            f"{role} did not consume the shared coordinator artifact "
            f"(shared-artifact semantics broken): deps={e.depends_on}"
        )


def test_single_origin_workers_share_one_coordinator_artifact():
    # Canonical CW topology holds in EVERY mode. A single_origin CW run must
    # record the SAME fan-out DAG as one_to_many: all three workers consume
    # the coordinator's one delegation artifact (no worker-to-worker relay).
    # Before the fix this test asserted the opposite (distinct deps / relay),
    # which is the incorrect serial behavior that was removed.
    result, order, _ = _run("single_origin", "worker:specialist_a")
    events = result.trace.events
    by_id = {e.event_id: e for e in events}
    coord_handoffs = [e for e in events
                      if e.agent_role == "coordinator"
                      and e.event_type == TraceEventType.AGENT_HANDOFF]
    assert coord_handoffs, "coordinator did not emit a delegation handoff"
    artifact_id = coord_handoffs[0].event_id
    first_worker_trans = {}
    for e in events:
        if e.event_type == TraceEventType.TOPOLOGY_TRANSITION and e.agent_role in (
                "specialist_a", "specialist_b", "synthesizer"):
            first_worker_trans.setdefault(e.agent_role, e)
    assert len(first_worker_trans) == 3
    for role, e in first_worker_trans.items():
        assert e.depends_on and artifact_id in e.depends_on, (
            f"{role} did not consume the shared coordinator artifact in "
            f"single_origin mode (canonical CW fan-out broken; serial relay "
            f"leaked back in): deps={e.depends_on}"
        )


# ── Canonical CW topology holds in EVERY mode (BND V4.1 execution_shape) ────
# The user's requirement: benign, single_origin, and one_to_many CW executions
# must all record the SAME canonical fan-out/fan-in DAG. Verified with the exact
# extraction BND V4.1 applies (benchmark.baseline_deviation.execution_shape),
# so any drift back to a serial relay is caught by the BND-compat logic itself.


@pytest.mark.parametrize("mode,target", [
    ("single_origin", None),                    # benign CW
    ("single_origin", "worker:specialist_a"),    # SO LEP
    ("one_to_many", "upstream:coordinator"),     # O2M LEP
])
def test_cw_canonical_shape_per_mode(mode, target):
    result, _, _ = _run(mode, target)
    assert result.runner_success, result.error
    _assert_canonical_cw_shape(_cw_shape(result.trace))


def test_cw_all_modes_share_one_canonical_shape():
    """benign, single_origin LEP, and one_to_many LEP must produce the SAME
    execution_shape (the canonical DAG). This is the regression guard for
    'propagation_mode must affect perturbation delivery only, never topology'."""
    shapes = [_cw_shape(_run(m, t)[0].trace) for m, t in (
        ("single_origin", None),
        ("single_origin", "worker:specialist_a"),
        ("one_to_many", "upstream:coordinator"),
    )]
    ref = shapes[0]
    for i, s in enumerate(shapes[1:], start=1):
        assert s == ref, (
            f"CW mode index {i} execution_shape differs from canonical:\n"
            f"  canonical: {ref}\n  got:       {s}\n"
            "propagation_mode leaked into the recorded workflow topology"
        )
    # and it must actually be the canonical fan-out (not some other shared shape)
    _assert_canonical_cw_shape(ref)


def test_is_coordinator_first_is_topologiespecific():
    cw = get_topology("coordinator_workers",
                     {"coordinator": "a0", "specialist_a": "a1",
                      "specialist_b": "a2", "synthesizer": "a3"})
    bv = get_topology("branch_and_verify",
                     {"researcher": "a1", "analyst": "a2", "verifier": "a3"})
    rl = get_topology("review_loop",
                     {"researcher": "a1", "analyst": "a2"})
    assert cw.is_coordinator_first() is True
    assert bv.is_coordinator_first() is False
    assert rl.is_coordinator_first() is False


from generation.injection_origins import remap_task_roles

@pytest.mark.parametrize("topo,amap", [
    ("branch_and_verify",
     {"researcher": "a1", "analyst": "a2", "verifier": "a3"}),
    ("review_loop",
     {"researcher": "a1", "analyst": "a2"}),
])
def test_other_topologies_keep_original_order(topo, amap):
    """review_loop / branch_and_verify must NOT take the coordinator-first
    path: their opening stage is a branch/producer, not a fan-out coordinator.

    The expected opening role is computed exactly the way the runner does
    (``get_topology`` + task-family ``remap_task_roles``), so the assertion
    stays correct under role remapping (e.g. code_review maps review_loop's
    researcher→inspector) instead of hardcoding the generic role name."""
    topology = remap_task_roles(get_topology(topo, amap), "code_review")
    expected_first = topology.stages[0].agent_role
    # These topologies must not be coordinator-first: their opening stage is a
    # single-source branch/producer, so the fan-in gate applies normally from
    # the start (no delegation-first bypass).
    assert not topology.is_coordinator_first()
    builder = ScenarioBuilder(seed=42)
    cfg = ScenarioBuildConfig(task_family="code_review",
                             fixture_id="code_review_easy",
                             topology=topo)
    spec = builder.build_benign(cfg)
    # branch_and_verify stages use researcher/analyst; the recording backend's
    # default worker behavior (write_memory → read → handoff to coordinator)
    # only fits coordinator_workers. Fall back to the stock dry-run backend
    # for these two so the scheduler's normal paths are exercised verbatim.
    result = ScenarioRunner(dry_run=True).run(spec, FIXTURE_ROOT)
    order = _order(result.trace)
    assert order, "no transitions recorded for a benign run"
    assert order[0] == expected_first, (
        f"{topo} opening stage must remain '{expected_first}', "
        f"got '{order[0]}' — the coordinator-first change leaked into "
        f"another topology"
    )
