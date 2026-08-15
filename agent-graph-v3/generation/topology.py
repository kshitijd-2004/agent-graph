"""Workflow topology definitions for multi-agent execution.

Each topology defines a directed graph of execution stages with handoff
rules, turn budgets, and iteration constraints.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Stage:
    """One agent's turn in a topology.

    `can_handoff` controls whether the stage is allowed to *emit* an outgoing
    handoff (i.e. whether the `handoff` native tool is exposed). Whether this
    stage *receives* a handoff is determined by incoming HandoffRule entries,
    not by this flag.

    `can_finalize` controls whether the stage is allowed to terminate the
    workflow (i.e. whether the `submit_final` native tool is exposed).

    Both flags must be set explicitly per stage — there is no default — so
    every topology declares its stage completion and forwarding permissions
    structurally rather than inheriting them.
    """
    stage_id: str
    agent_role: str
    agent_id: str
    stage_type: str = "execute"       # "execute" | "coordinate" | "branch" | "merge"
    max_turns: int = 10
    can_handoff: bool = False         # explicit; set per topology
    can_finalize: bool = False        # explicit; set per topology


@dataclass
class HandoffRule:
    """How handoff payloads flow between stages."""
    from_stage: str          # source agent role
    to_stage: str            # target agent role
    required: bool = True
    label_on_ignore: str = "handoff_ignored"
    label_on_consume: str = "handoff_consumed"


@dataclass
class TopologyConfig:
    """Complete topology specification."""
    topology_id: str
    display_name: str
    stages: List[Stage]
    handoff_rules: List[HandoffRule]
    exit_stage: str
    max_iterations: int = 1
    max_review_cycles: int = 2   # max back-and-forth loops for review topologies
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def stage_by_id(self) -> Dict[str, Stage]:
        return {s.stage_id: s for s in self.stages}

    @property
    def stage_by_role(self) -> Dict[str, Stage]:
        return {s.agent_role: s for s in self.stages}

    @property
    def agent_roles(self) -> List[str]:
        return [s.agent_role for s in self.stages]

    def get_stage(self, role: str) -> Optional[Stage]:
        return self.stage_by_role.get(role)

    def get_outgoing_handoff(self, role: str) -> Optional[HandoffRule]:
        """Return the outgoing HandoffRule for a role, or None."""
        for rule in self.handoff_rules:
            if rule.from_stage == role:
                return rule
        return None

    def is_backedge(self, rule: HandoffRule) -> bool:
        """Return True if rule goes backward in stage order (to an earlier stage)."""
        positions = {s.agent_role: i for i, s in enumerate(self.stages)}
        from_idx = positions.get(rule.from_stage, -1)
        to_idx = positions.get(rule.to_stage, -1)
        return to_idx >= 0 and from_idx >= 0 and to_idx < from_idx

    def get_backedges(self) -> List[HandoffRule]:
        """Return all handoff rules that go backward (to an earlier stage)."""
        return [r for r in self.handoff_rules if self.is_backedge(r)]

    def get_reviewer_stage(self) -> Optional[Stage]:
        """Return the stage that is the source of all backedges, if any.

        In a review-loop topology this is the stage that can both hand back
        for revision and finalize (the reviewer).
        """
        backedge_sources = {r.from_stage for r in self.get_backedges()}
        if len(backedge_sources) == 1:
            role = next(iter(backedge_sources))
            return self.get_stage(role)
        return None


def _build_registry() -> Dict[str, callable]:
    """Return {topology_id: builder_fn}."""
    def linear_2(agent_map: Dict[str, str]) -> TopologyConfig:
        return TopologyConfig(
            topology_id="linear_2",
            display_name="Linear (2 agents)",
            stages=[
                Stage("researcher", "researcher", agent_map["researcher"],
                      max_turns=10, can_handoff=True, can_finalize=False),
                Stage("analyst", "analyst", agent_map["analyst"],
                      max_turns=10, can_handoff=False, can_finalize=True),
            ],
            handoff_rules=[
                HandoffRule("researcher", "analyst"),
            ],
            exit_stage="analyst",
            max_iterations=1,
        )

    def linear_3(agent_map: Dict[str, str]) -> TopologyConfig:
        return TopologyConfig(
            topology_id="linear_3",
            display_name="Linear (3 agents)",
            stages=[
                Stage("researcher", "researcher", agent_map["researcher"],
                      max_turns=8, can_handoff=True, can_finalize=False),
                Stage("analyst", "analyst", agent_map["analyst"],
                      max_turns=8, can_handoff=True, can_finalize=False),
                Stage("verifier", "verifier", agent_map["verifier"],
                      max_turns=8, can_handoff=False, can_finalize=True),
            ],
            handoff_rules=[
                HandoffRule("researcher", "analyst"),
                HandoffRule("analyst", "verifier"),
            ],
            exit_stage="verifier",
            max_iterations=1,
        )

    def coordinator_star(agent_map: Dict[str, str]) -> TopologyConfig:
        return TopologyConfig(
            topology_id="coordinator_star",
            display_name="Coordinator Star",
            stages=[
                Stage("coordinator", "coordinator", agent_map["coordinator"],
                      max_turns=5, can_handoff=False, can_finalize=True),
                Stage("specialist_a", "specialist_a", agent_map["specialist_a"],
                      max_turns=6, can_handoff=True, can_finalize=False),
                Stage("specialist_b", "specialist_b", agent_map["specialist_b"],
                      max_turns=6, can_handoff=True, can_finalize=False),
            ],
            handoff_rules=[
                HandoffRule("specialist_a", "coordinator"),
                HandoffRule("specialist_b", "coordinator"),
            ],
            exit_stage="coordinator",
            max_iterations=2,
        )

    def parallel_merge(agent_map: Dict[str, str]) -> TopologyConfig:
        return TopologyConfig(
            topology_id="parallel_merge",
            display_name="Parallel Merge",
            stages=[
                Stage("researcher", "researcher", agent_map["researcher"],
                      max_turns=6, can_handoff=True, can_finalize=False),
                Stage("analyst", "analyst", agent_map["analyst"],
                      max_turns=6, can_handoff=True, can_finalize=False),
                Stage("verifier", "verifier", agent_map["verifier"],
                      max_turns=8, can_handoff=False, can_finalize=True),
            ],
            handoff_rules=[
                HandoffRule("researcher", "verifier"),
                HandoffRule("analyst", "verifier"),
            ],
            exit_stage="verifier",
            max_iterations=1,
        )

    def review_loop(agent_map: Dict[str, str]) -> TopologyConfig:
        return TopologyConfig(
            topology_id="review_loop",
            display_name="Review Loop",
            stages=[
                Stage("researcher", "researcher", agent_map["researcher"],
                      max_turns=6, can_handoff=True, can_finalize=False),
                # Analyst can both hand back for revision AND finalize.
                Stage("analyst", "analyst", agent_map["analyst"],
                      max_turns=6, can_handoff=True, can_finalize=True),
            ],
            handoff_rules=[
                HandoffRule("researcher", "analyst"),
                HandoffRule("analyst", "researcher"),  # loop back for revision
            ],
            exit_stage="analyst",
            max_iterations=2,
            max_review_cycles=1,
        )

    def shared_memory_collaboration(agent_map: Dict[str, str]) -> TopologyConfig:
        return TopologyConfig(
            topology_id="shared_memory_collaboration",
            display_name="Shared Memory Collaboration",
            stages=[
                Stage("researcher", "researcher", agent_map["researcher"],
                      max_turns=8, can_handoff=True, can_finalize=False),
                Stage("analyst", "analyst", agent_map["analyst"],
                      max_turns=8, can_handoff=False, can_finalize=True),
            ],
            handoff_rules=[
                HandoffRule("researcher", "analyst"),
            ],
            exit_stage="analyst",
            max_iterations=1,
        )

    def branch_and_verify(agent_map: Dict[str, str]) -> TopologyConfig:
        return TopologyConfig(
            topology_id="branch_and_verify",
            display_name="Branch and Verify",
            stages=[
                Stage("researcher", "researcher", agent_map["researcher"],
                      max_turns=6, can_handoff=True, can_finalize=False),
                Stage("analyst", "analyst", agent_map["analyst"],
                      max_turns=6, can_handoff=True, can_finalize=False),
                Stage("verifier", "verifier", agent_map["verifier"],
                      max_turns=8, can_handoff=False, can_finalize=True),
            ],
            handoff_rules=[
                HandoffRule("researcher", "verifier"),
                HandoffRule("analyst", "verifier"),
            ],
            exit_stage="verifier",
            max_iterations=1,
        )

    return {
        "linear_2": linear_2,
        "linear_3": linear_3,
        "coordinator_star": coordinator_star,
        "parallel_merge": parallel_merge,
        "review_loop": review_loop,
        "shared_memory_collaboration": shared_memory_collaboration,
        "branch_and_verify": branch_and_verify,
    }


def get_topology(topology_id: str, agent_map: Dict[str, str],
                 max_agent_turns: Optional[int] = None,
                 max_review_cycles: Optional[int] = None) -> TopologyConfig:
    """Return the TopologyConfig for a given topology ID and agent map.

    Args:
        topology_id: One of the supported topology identifiers.
        agent_map: Mapping from agent roles to entity IDs.
        max_agent_turns: If provided, override each stage's max_turns with this value.
        max_review_cycles: If provided, override the topology's max_review_cycles.
    """
    registry = _build_registry()
    builder = registry.get(topology_id)
    if builder is None:
        raise ValueError(f"Unknown topology: {topology_id!r}. "
                         f"Supported: {sorted(registry.keys())}")
    topo = builder(agent_map)
    if max_agent_turns is not None:
        for stage in topo.stages:
            stage.max_turns = max_agent_turns
    if max_review_cycles is not None:
        topo.max_review_cycles = max_review_cycles
    return topo


def build_agent_map_from_topology(topology: TopologyConfig) -> Dict[str, str]:
    """Derive an agent_map from a TopologyConfig's stages.

    Returns {agent_role: agent_id} for every stage in the topology.
    This is the single source of truth — topology builders set agent_id
    per stage, and this function extracts the map.
    """
    return {stage.agent_role: stage.agent_id for stage in topology.stages}
