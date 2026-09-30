"""Canonical injection-origin expectation for execution and trace auditing."""
from collections import defaultdict
from typing import Iterable

from generation.topology import get_topology


IPI = "LEP_INDIRECT_PROMPT_INJECTION"


def remap_task_roles(topology, task_family):
    """Keep stage identities stable while applying the production role mapping."""
    from generation.scenario_builder import TASK_CONFIGS
    agents = TASK_CONFIGS.get(task_family, {}).get("default_agents", [])
    if agents and len(agents) == len(topology.stages):
        mapping = {stage.agent_role: agents[i] for i, stage in enumerate(topology.stages)}
        for stage in topology.stages:
            stage.agent_role = mapping.get(stage.agent_role, stage.agent_role)
        for rule in topology.handoff_rules:
            rule.from_stage = mapping.get(rule.from_stage, rule.from_stage)
            rule.to_stage = mapping.get(rule.to_stage, rule.to_stage)
    return topology


def intended_origin_roles(config, topology, mode):
    """Return required roles, or None for an unbound single-origin config.

    Handoff endpoints are roles; exit_stage is a stable stage ID.
    Explicit single-origin targets take precedence over family defaults.
    """
    from leps.topology_target import resolve_target_stage
    explicit = resolve_target_stage(config, topology, propagation_mode=mode)
    exit_role = topology.stage_by_id[topology.exit_stage].agent_role
    if mode == "many_to_one":
        roles = {rule.from_stage for rule in topology.handoff_rules
                 if rule.to_stage == exit_role and rule.from_stage != exit_role
                 and topology.stage_by_role[rule.from_stage].can_handoff}
        if not roles or (explicit is not None and explicit not in roles):
            raise ValueError("many_to_one requires upstream handoff origins")
        return roles
    if mode == "one_to_many":
        if explicit is not None and explicit != exit_role:
            raise ValueError("one_to_many origin must be the upstream coordinator")
        if len(topology.get_outgoing_handoffs(exit_role)) < 2:
            raise ValueError("one_to_many requires an upstream fan-out")
        return {exit_role}
    if explicit is not None:
        return {explicit}
    if config.code == IPI and config.task_family == "financial_analysis":
        # Canonical single-origin defaults: producer, branch A, or worker A.
        stage_id = "security_worker" if topology.topology_id == "coordinator_workers" else "researcher"
        return {topology.stage_by_id[stage_id].agent_role}
    return None


def origin_structure_errors(trace, scenario):
    """Private admission/audit checks; never enrich detector-visible events.

    Counts remain necessary. They are not sufficient: each LEP must realize its
    intended distinct roles, and fan-out must follow the upstream origin.
    A handoff LEP may mutate the initial fan-out boundary itself.
    """
    if scenario.condition in ("benign", "counterfactual") or not scenario.lep_configs:
        return []
    mode = scenario.workflow_config.propagation_mode
    from schemas.scenario import TOPOLOGY_PROPAGATION_MODES
    if mode not in TOPOLOGY_PROPAGATION_MODES.get(scenario.workflow_config.topology, []):
        return [f"Unsupported topology/mode: {scenario.workflow_config.topology}/{mode}"]
    topology = remap_task_roles(get_topology(scenario.workflow_config.topology,
                                            defaultdict(str)), scenario.task_family)
    exit_role = topology.stage_by_id[topology.exit_stage].agent_role
    handoffs = [e.event_index for e in trace.events
                if e.event_type.value == "agent_handoff" and e.agent_role == exit_role]
    fanout = min(handoffs) if handoffs else None
    origins = [e for e in trace.events if e.event_labels.is_injection_origin]
    codes = {c.code for c in scenario.lep_configs}
    errors = []
    by_code = {code: [] for code in codes}
    for event in origins:
        code = event.hidden.get("lep_type") or event.hidden.get("lep_code")
        code = code or event.hidden.get("lep_injection", {}).get("lep_code")
        # Legacy single-LEP traces have unambiguous attribution without metadata.
        if not code and len(codes) == 1:
            code = next(iter(codes))
        if code not in by_code:
            errors.append(f"origin {event.event_id}: unknown LEP attribution")
        else:
            by_code[code].append(event)
    for config in scenario.lep_configs:
        events = by_code[config.code]
        required = intended_origin_roles(config, topology, mode)
        roles = [e.agent_role for e in events]
        expected = len(required) if mode == "many_to_one" else 1
        if len(events) != expected:
            errors.append(f"{config.code}: expected {expected} actual origins, got {len(events)}")
        if required is not None and (set(roles) != required or len(roles) != len(set(roles))):
            errors.append(f"{config.code}: intended roles {sorted(required)}, got {roles}")
        if mode == "one_to_many":
            if fanout is None:
                errors.append(f"{config.code}: no initial fan-out boundary")
            else:
                for event in events:
                    on_handoff = (config.code in {"LEP_HANDOFF_CORRUPTION", "LEP_INPUT_DISREGARD"}
                                  and event.event_type.value == "agent_handoff")
                    if event.event_index > fanout or (event.event_index == fanout and not on_handoff):
                        errors.append(f"{config.code}: origin {event.event_id} is not before initial fan-out")
    return errors


def expected_injection_origins(*, condition: str, lep_codes: Iterable[str],
                               propagation_mode: str = "single_origin",
                               topology: str = "review_loop") -> int:
    """Return the total origin budget, with one origin per active LEP/worker.

    Counterfactuals may retain LEP configuration but never inject. Many-to-one
    workers are the topology's intended handoff sources into its final stage.
    """
    codes = set(lep_codes)
    if condition in ("benign", "counterfactual") or not codes:
        return 0
    if propagation_mode in ("single_origin", "one_to_many"):
        return len(codes)
    if propagation_mode != "many_to_one":
        raise ValueError(f"Unknown propagation mode: {propagation_mode!r}")
    config = get_topology(topology, defaultdict(str))
    workers = {
        rule.from_stage for rule in config.handoff_rules
        if rule.to_stage == config.exit_stage
        and rule.from_stage != config.exit_stage
        and config.stage_by_role[rule.from_stage].can_handoff
    }
    if not workers:
        raise ValueError(f"many_to_one topology {topology!r} has no upstream workers")
    return len(codes) * len(workers)
