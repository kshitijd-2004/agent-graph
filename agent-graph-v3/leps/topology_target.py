"""Topology-aware target selection for LEPs.

Validates and resolves topology_target strings into concrete agent_role
targets. Used by LEPOrchestrator.set_topology() after topology construction
but before scenario execution begins.

Accepted topology_target formats:
    None                     — topology-agnostic (fire on any stage)
    "branch:<role>"          — target one branch in branch_and_verify
    "worker:<role>"          — target one worker in coordinator_workers
    "upstream:<role>"        — target the upstream coordinator in
                              coordinator_workers one-to-many propagation
"""

from __future__ import annotations

from typing import Optional


class InvalidTopologyTargetError(ValueError):
    """Raised when topology_target references a non-existent stage
    or uses an invalid format for the topology/propagation mode."""
    pass


def resolve_target_stage(lep_config, topology, propagation_mode: str = "single_origin") -> Optional[str]:
    """Return the agent_role that this LEP should target, or None for any.

    Args:
        lep_config: LEPConfig (or any object) with optional ``topology_target``
                    attribute.
        topology:   TopologyConfig with ``agent_roles`` list and
                    ``topology_id`` string.
        propagation_mode: The scenario's propagation mode. ``upstream:`` is
                    only valid for coordinator_workers + one_to_many.

    Returns:
        None  — topology_target not set → LEP fires on any stage (default).
        str   — the specific agent_role that ``event.agent_role`` must match.

    Raises:
        InvalidTopologyTargetError: if topology_target references a role not
            present in the topology, uses an unknown prefix, or uses the
            ``upstream:`` prefix outside coordinator_workers + one_to_many.
    """
    target = getattr(lep_config, "topology_target", None)
    if target is None:
        return None

    if not isinstance(target, str):
        return None

    available_roles = set(topology.agent_roles)

    # branch:<role>  — branch_and_verify targeting
    if target.startswith("branch:"):
        if topology.topology_id != "branch_and_verify":
            raise InvalidTopologyTargetError(
                f"topology_target='{target}' uses 'branch:' prefix but "
                f"topology '{topology.topology_id}' is not branch_and_verify. "
                f"'branch:' is only valid for branch_and_verify."
            )
        role = target.split(":", 1)[1]
        if role not in available_roles:
            raise InvalidTopologyTargetError(
                f"topology_target='{target}' references role '{role}' "
                f"which is not in topology '{topology.topology_id}'. "
                f"Available roles: {sorted(available_roles)}"
            )
        return role

    # worker:<role>  — coordinator_workers targeting
    if target.startswith("worker:"):
        if topology.topology_id != "coordinator_workers":
            raise InvalidTopologyTargetError(
                f"topology_target='{target}' uses 'worker:' prefix but "
                f"topology '{topology.topology_id}' is not coordinator_workers. "
                f"'worker:' is only valid for coordinator_workers."
            )
        role = target.split(":", 1)[1]
        if role not in available_roles:
            raise InvalidTopologyTargetError(
                f"topology_target='{target}' references role '{role}' "
                f"which is not in topology '{topology.topology_id}'. "
                f"Available roles: {sorted(available_roles)}"
            )
        return role

    # upstream:<role>  — coordinator_workers one-to-many only
    if target.startswith("upstream:"):
        if topology.topology_id != "coordinator_workers":
            raise InvalidTopologyTargetError(
                f"topology_target='{target}' uses 'upstream:' prefix but "
                f"topology '{topology.topology_id}' is not coordinator_workers. "
                f"'upstream:' is only valid for coordinator_workers."
            )
        if propagation_mode != "one_to_many":
            raise InvalidTopologyTargetError(
                f"topology_target='{target}' uses 'upstream:' prefix but "
                f"propagation_mode is '{propagation_mode}'. "
                f"'upstream:' is only valid for one_to_many propagation."
            )
        role = target.split(":", 1)[1]
        canonical = "coordinator"
        if role != canonical:
            raise InvalidTopologyTargetError(
                f"topology_target='{target}' references role '{role}'. "
                f"Canonical valid role for upstream: is '{canonical}'. "
                f"Available roles: {sorted(available_roles)}"
            )
        return role

    # Unknown prefix — hard error
    raise InvalidTopologyTargetError(
        f"topology_target='{target}' has unrecognized format. "
        f"Accepted: 'branch:<role>', 'worker:<role>', 'upstream:<role>'."
    )


# ── Canonical topology targets ─────────────────────────────────────────────
#
# Default injection targets for each topology. Used when a LEP config does
# not explicitly set topology_target. These define WHERE the single canonical
# injection origin occurs for benchmark experiments.

TOPOLOGY_TARGETS: dict = {
    "linear_2": {
        "kind": "stage",
        "target": "first_agent",
    },
    "linear_3": {
        "kind": "stage",
        "target": "first_agent",
    },
    "review_loop": {
        "kind": "stage_invocation",
        "target": "producer",
        "invocation": 1,
    },
    "branch_and_verify": {
        "kind": "branch",
        "target": "researcher",
    },
    "coordinator_workers": {
        "kind": "worker",
        "target": "worker_a",
    },
}


def get_default_topology_target(topology_id: str) -> Optional[dict]:
    """Return the default target dict for a topology, or None."""
    return TOPOLOGY_TARGETS.get(topology_id)
