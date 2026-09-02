"""Topology-aware target selection for LEPs.

Validates and resolves topology_target strings into concrete agent_role
targets. Used by LEPOrchestrator.set_topology() after topology construction
but before scenario execution begins.

Accepted topology_target formats:
    None                     — topology-agnostic (fire on any stage)
    "branch:<role>"          — target one branch in branch_and_verify
    "worker:<role>"          — target one worker in coordinator_workers

DEFERRED:
    "upstream:<role>"        — not yet implemented; requires redesign to
                              demonstrate one perturbed event feeding
                              multiple worker consumers.
"""

from __future__ import annotations

from typing import Optional


class InvalidTopologyTargetError(ValueError):
    """Raised when topology_target references a non-existent stage
    or uses a format that is not yet implemented."""
    pass


def resolve_target_stage(lep_config, topology) -> Optional[str]:
    """Return the agent_role that this LEP should target, or None for any.

    Args:
        lep_config: LEPConfig (or any object) with optional ``topology_target``
                    attribute.
        topology:   TopologyConfig with ``agent_roles`` list and
                    ``topology_id`` string.

    Returns:
        None  — topology_target not set → LEP fires on any stage (default).
        str   — the specific agent_role that ``event.agent_role`` must match.

    Raises:
        InvalidTopologyTargetError: if topology_target references a role not
            present in the topology, uses an unknown prefix, or uses the
            deferred ``upstream:`` prefix.
    """
    target = getattr(lep_config, "topology_target", None)
    if target is None:
        return None

    if not isinstance(target, str):
        return None

    available_roles = set(topology.agent_roles)

    # branch:<role>  — branch_and_verify targeting
    if target.startswith("branch:"):
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
        role = target.split(":", 1)[1]
        if role not in available_roles:
            raise InvalidTopologyTargetError(
                f"topology_target='{target}' references role '{role}' "
                f"which is not in topology '{topology.topology_id}'. "
                f"Available roles: {sorted(available_roles)}"
            )
        return role

    # upstream:<role>  — DEFERRED (not yet implemented)
    if target.startswith("upstream:"):
        raise InvalidTopologyTargetError(
            f"topology_target='{target}' is not yet implemented. "
            f"Requires redesign to show one perturbed event feeding "
            f"multiple worker consumers. Use 'branch:<role>' or "
            f"'worker:<role>' for current experiments."
        )

    # Unknown prefix — hard error
    raise InvalidTopologyTargetError(
        f"topology_target='{target}' has unrecognized format. "
        f"Accepted: 'branch:<role>', 'worker:<role>'. "
        f"(upstream:<role> is deferred.)"
    )
