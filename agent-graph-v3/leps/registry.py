"""LEP registry and orchestrator.

Maps LEP codes to implementations and coordinates injection
during trace execution.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Set

from schemas import LEPConfig
from schemas.trace_event import TraceEvent, TraceEventType

from leps.tool_result_corruption import ToolResultCorruptionLEP
from leps.indirect_prompt_injection import IndirectPromptInjectionLEP
from leps.memory_poisoning import MemoryPoisoningLEP
from leps.handoff_corruption import HandoffCorruptionLEP
from leps.input_disregard import InputDisregardLEP

logger = logging.getLogger(__name__)

# Map LEP codes to LEP classes
LEP_REGISTRY: Dict[str, type] = {
    "LEP_TOOL_RESULT_CORRUPTION": ToolResultCorruptionLEP,
    "LEP_INDIRECT_PROMPT_INJECTION": IndirectPromptInjectionLEP,
    "LEP_MEMORY_POISONING": MemoryPoisoningLEP,
    "LEP_HANDOFF_CORRUPTION": HandoffCorruptionLEP,
    "LEP_INPUT_DISREGARD": InputDisregardLEP,
}

# Human-readable names
LEP_NAMES: Dict[str, str] = {
    "LEP_TOOL_RESULT_CORRUPTION": "Tool Result Corruption",
    "LEP_INDIRECT_PROMPT_INJECTION": "Indirect Prompt Injection",
    "LEP_MEMORY_POISONING": "Memory Poisoning",
    "LEP_HANDOFF_CORRUPTION": "Handoff Corruption",
    "LEP_INPUT_DISREGARD": "Input Disregard",
}

# ── Boundary routing ─────────────────────────────────────────────────────
#
# Each entry maps an event type (the semantic boundary) to the set of
# LEP codes that are eligible to fire at that boundary.
#
# LEPs NOT listed here are never evaluated automatically — they require
# explicit orchestration code to fire.
#
# Backward compat: "tool_result" is an alias for TOOL_RESULT.

BOUNDARY_LEPS: Dict[str, Set[str]] = {
    "tool_result": {
        "LEP_TOOL_RESULT_CORRUPTION",
        "LEP_INDIRECT_PROMPT_INJECTION",
    },
    "agent_handoff": {
        "LEP_HANDOFF_CORRUPTION",
        "LEP_INPUT_DISREGARD",
    },
    "memory_write": {
        "LEP_MEMORY_POISONING",
    },
}

# Reverse map: lep_code -> preferred boundary event type
LEP_BOUNDARY: Dict[str, str] = {}
for boundary, codes in BOUNDARY_LEPS.items():
    for code in codes:
        LEP_BOUNDARY[code] = boundary


@dataclass
class LEPFiringState:
    """Tracks firing state per LEP across a scenario run.

    Semantics of ``fired_targets`` differ by propagation mode:

    * ``single_origin`` — actual injected stage/branch/worker
    * ``many_to_one`` — independently injected workers (one entry per perturbed worker)
    * ``one_to_many`` — ``{"upstream:coordinator"}`` — consumers A/B/C are NOT added
    """
    max_origins: int
    fired_origin_count: int = 0
    fired_targets: set = field(default_factory=set)
    eligible_occurrence_counts: Dict[str, int] = field(default_factory=dict)


class LEPOrchestrator:
    """Coordinates LEP instances during trace execution.

    Manages:
    - Creating LEP instances from LEPConfigs
    - Evaluating triggers against trace events (boundary-aware)
    - Coordinating injection across tool calls, memory, and handoffs
    - Tracking propagation through events
    - Resetting state between runs
    """

    def __init__(self):
        self._active_leps: Dict[str, Any] = {}  # lep_code -> LEP instance
        self._trigger_results: list = []
        # Per-LEP firing state: tracks origins, targets, and occurrence counts
        # for target-aware firing decisions in many-to-one and one-to-many scenarios.
        self._firing_state: Dict[str, LEPFiringState] = {}
        # Topology-aware target filter: lep_code -> agent_role to target,
        # or None to fire on any stage. Populated by set_topology().
        self._topology_target_stages: Dict[str, Optional[str]] = {}
        # Propagation mode for target-filtering semantics in evaluate_for_boundary.
        self._propagation_mode: str = "single_origin"
        # Topology reference for M2O target filtering.
        self._topology: Optional[Any] = None

    def register_lep(self, lep_config: LEPConfig) -> None:
        """Register a LEP for execution."""
        print(
            "[DEBUG REGISTRY]",
            lep_config.code,
            "task_family=",
            repr(lep_config.task_family),
        )
        code = lep_config.code
        if code not in LEP_REGISTRY:
            raise ValueError(f"Unknown LEP code: {code}. Available: {list(LEP_REGISTRY.keys())}")

        lep_class = LEP_REGISTRY[code]
        instance = lep_class(lep_config)
        self._active_leps[code] = instance
        # Invalidate any previous topology binding so register_leps followed
        # by set_topology() always re-resolves from the new config.
        self._topology_target_stages.pop(code, None)
        logger.debug("Registered LEP: %s (%s)", code, LEP_NAMES.get(code, code))

    def set_topology(self, topology, propagation_mode: str = "single_origin") -> None:
        """Resolve topology_target for every registered LEP against this topology.

        Validates and stores each LEP's target stage so that
        ``evaluate_for_boundary`` can skip events whose ``agent_role`` does
        not match the target. Raises ``InvalidTopologyTargetError`` if any
        LEP references a role not present in the topology.
        """
        from leps.topology_target import (
            resolve_target_stage,
            InvalidTopologyTargetError,
        )
        self._topology = topology
        self._propagation_mode = propagation_mode
        for code, lep in self._active_leps.items():
            try:
                self._topology_target_stages[code] = resolve_target_stage(
                    lep.config, topology, propagation_mode=propagation_mode
                )
            except InvalidTopologyTargetError:
                # Re-raise immediately — invalid targets should fail fast at
                # scenario setup, not silently degrade at run time.
                raise

    def register_leps(self, lep_configs: list[LEPConfig]) -> None:
        """Register multiple LEPs."""
        for config in lep_configs:
            self.register_lep(config)

    def evaluate_triggers(
        self,
        event: Any,
        tool_result: str = "",
    ) -> Dict[str, Any]:
        """Evaluate all registered LEP triggers against an event.

        LEGACY: evaluates every LEP regardless of boundary.
        Prefer evaluate_for_boundary() for new code.
        """
        results = {}
        for code, lep_instance in self._active_leps.items():
            if not hasattr(lep_instance, "evaluate"):
                continue
            try:
                import inspect
                sig = inspect.signature(lep_instance.evaluate)
                params = [p for p in sig.parameters.values()
                         if p.name != 'self' and p.default is inspect.Parameter.empty]
                if len(params) == 2:
                    decision = lep_instance.evaluate(event, tool_result)
                else:
                    decision = lep_instance.evaluate(event)
                results[code] = decision
                if decision.fired:
                    logger.debug(
                        "LEP %s trigger FIRED for event %s: %s",
                        code, getattr(event, 'event_id', '?'), decision.reason,
                    )
            except Exception as e:
                logger.warning("LEP %s evaluation error: %s", code, e)
        self._trigger_results.append(results)
        return results

    def evaluate_for_boundary(
        self,
        event: TraceEvent,
        tool_result: str = "",
    ) -> Dict[str, Any]:
        """Evaluate only the LEPs registered for this event's boundary.

        Routes evaluation based on event.event_type so that boundary-specific
        LEPs (handoff corruption, input disregard, etc.) are only evaluated
        at their semantically correct intervention point.
        """
        event_type = (event.event_type.value if hasattr(event.event_type, 'value') else str(event.event_type)).lower()
        eligible_codes = BOUNDARY_LEPS.get(event_type, set())
        if not eligible_codes:
            # No LEPs are eligible at this boundary — skip evaluation entirely
            # so that empty/default triggers cannot consume one-shot LEPs
            # on unrelated events like USER_INPUT or SYSTEM_INIT.
            return {}

        results = {}
        for code in eligible_codes:
            lep_instance = self._active_leps.get(code)
            if lep_instance is None or not hasattr(lep_instance, "evaluate"):
                continue

            # Ensure firing state exists for this LEP
            if code not in self._firing_state:
                self._firing_state[code] = LEPFiringState(max_origins=1)

            state = self._firing_state[code]

            # Origin budget: skip if this LEP has already fired its max origins
            if state.fired_origin_count >= state.max_origins:
                continue

            # Topology-aware target filter: when set_topology() resolved a
            # topology_target for this LEP, skip events from non-target stages.
            # In many_to_one mode with no explicit target, any worker that has
            # not yet fired is eligible (each worker fires at most once).
            target_stage = self._topology_target_stages.get(code)
            event_role = getattr(event, "agent_role", "") or ""
            if target_stage is not None:
                if event_role != target_stage:
                    continue
            elif self._propagation_mode == "many_to_one" and self._topology is not None:
                # M2O with topology_target=None: skip workers that have already fired.
                # Also skip the coordinator itself — LEPs fire on workers only.
                exit_role = getattr(self._topology, "exit_stage", "")
                if event_role == exit_role:
                    continue
                if event_role in state.fired_targets:
                    continue

            try:
                import inspect
                sig = inspect.signature(lep_instance.evaluate)
                params = [p for p in sig.parameters.values()
                         if p.name != 'self' and p.default is inspect.Parameter.empty]
                if len(params) == 2:
                    decision = lep_instance.evaluate(event, tool_result)
                else:
                    decision = lep_instance.evaluate(event)
                results[code] = decision
                if decision.fired:
                    logger.debug(
                        "LEP %s trigger FIRED at boundary %s for event %s: %s",
                        code, event_type, getattr(event, 'event_id', '?'), decision.reason,
                    )
            except Exception as e:
                logger.warning("LEP %s evaluation error: %s", code, e)
        self._trigger_results.append(results)
        return results

    def mark_successful_mutation(self, lep_code: str) -> None:
        """Record that a LEP has successfully applied a material mutation.

        Legacy: redirects to mark_fired_origin for backward compat.
        """
        self.mark_fired_origin(lep_code)

    def mark_fired_origin(self, lep_code: str, target: Optional[str] = None) -> None:
        """Record that a LEP has fired at an origin point.

        Increments the origin count and records the target (if any) so
        that the firing predicate can correctly handle many-to-one and
        one-to-many scenarios.

        Args:
            lep_code: The LEP that fired.
            target:   The resolved target stage/role, or None if
                      topology-agnostic.
        """
        if lep_code not in self._firing_state:
            self._firing_state[lep_code] = LEPFiringState(max_origins=1)
        state = self._firing_state[lep_code]
        state.fired_origin_count += 1
        if target is not None:
            state.fired_targets.add(target)

    def set_max_origins(self, lep_code: str, max_origins: int) -> None:
        """Set the maximum number of origins for a LEP.

        Must be called before scenario execution begins (before the first
        call to evaluate_for_boundary).
        """
        if lep_code not in self._firing_state:
            self._firing_state[lep_code] = LEPFiringState(max_origins=max_origins)
        else:
            self._firing_state[lep_code].max_origins = max_origins

    def get_firing_state(self, lep_code: str) -> Optional[LEPFiringState]:
        """Get the firing state for a LEP, or None if not initialized."""
        return self._firing_state.get(lep_code)

    def get_lep_instance(self, lep_code: str) -> Any:
        """Get a specific LEP instance."""
        return self._active_leps.get(lep_code)

    def get_all_instances(self) -> Dict[str, Any]:
        """Get all active LEP instances."""
        return dict(self._active_leps)

    def fire_injection(
        self,
        lep_code: str,
        event: Any,
        tool_result: str = "",
        **kwargs,
    ) -> Any:
        """Execute a LEP injection for a fired trigger.

        Returns the injection result object.
        """
        lep = self._active_leps.get(lep_code)
        if lep is None:
            raise ValueError(f"LEP not registered: {lep_code}")

        if hasattr(lep, "corrupt"):
            return lep.corrupt(event, tool_result, **kwargs)
        elif hasattr(lep, "inject_into_content"):
            # Extract file_path from event tool_arguments if not in kwargs
            file_path = kwargs.get("file_path", "")
            if not file_path and hasattr(event, "tool_arguments"):
                file_path = (event.tool_arguments or {}).get("path", "")
            variant = kwargs.get("variant", "ignore_previous")
            return lep.inject_into_content(file_path, tool_result, variant)
        elif hasattr(lep, "poison"):
            memory_key = kwargs.get("memory_key", "")
            task_family = kwargs.get("task_family", "financial_analysis")
            return lep.poison(memory_key, task_family, **kwargs)
        elif hasattr(lep, "create_disregard"):
            target_agent = kwargs.get("target_agent", "")
            handoff_event_id = kwargs.get("handoff_event_id", "")
            return lep.create_disregard(target_agent, handoff_event_id, **kwargs)
        return None

    def get_corrupted_tool_result(self, lep_code: str, tool_call_id: str) -> Optional[str]:
        """Get a corrupted tool result for delivery instead of the real one."""
        lep = self._active_leps.get(lep_code)
        if lep and hasattr(lep, "get_corrupted_result"):
            return lep.get_corrupted_result(tool_call_id)
        return None

    def get_corrupted_handoff(self, lep_code: str, handoff_event_id: str) -> Optional[str]:
        """Get corrupted handoff content."""
        lep = self._active_leps.get(lep_code)
        if lep and hasattr(lep, "get_corrupted_content"):
            return lep.get_corrupted_content(handoff_event_id)
        return None

    def get_original_handoff(self, lep_code: str, handoff_event_id: str) -> Optional[str]:
        """Get original handoff content (for counterfactual analysis)."""
        lep = self._active_leps.get(lep_code)
        if lep and hasattr(lep, "get_original_content"):
            return lep.get_original_content(handoff_event_id)
        return None

    def record_consumption(
        self,
        lep_code: str,
        event_id: str,
        **kwargs,
    ) -> None:
        """Record that a LEP perturbation was consumed."""
        lep = self._active_leps.get(lep_code)
        if lep is None:
            return

        if hasattr(lep, "mark_consumed"):
            lep.mark_consumed(event_id, **kwargs)
        if hasattr(lep, "record_retrieval"):
            memory_key = kwargs.get("memory_key", "")
            agent_id = kwargs.get("agent_id", "")
            lep.record_retrieval(memory_key, event_id, agent_id)

    def record_handoff_receipt(
        self,
        lep_code: str,
        handoff_event_id: str,
        agent_id: str,
    ) -> None:
        """Record that a receiving agent got the handoff."""
        lep = self._active_leps.get(lep_code)
        if lep and hasattr(lep, "mark_received"):
            lep.mark_received(handoff_event_id, agent_id)

    def get_all_results(self) -> Dict[str, Any]:
        """Get results from all LEP instances."""
        results = {}
        for code, lep in self._active_leps.items():
            if hasattr(lep, "get_instances"):
                results[code] = lep.get_instances()
        return results

    def reset(self) -> None:
        """Reset all LEP instances for a new run."""
        for lep in self._active_leps.values():
            if hasattr(lep, "reset"):
                lep.reset()
        self._active_leps.clear()
        self._trigger_results.clear()


def create_lep_instance(lep_config: LEPConfig) -> Any:
    """Factory function to create a LEP instance from config."""
    code = lep_config.code
    if code not in LEP_REGISTRY:
        raise ValueError(f"Unknown LEP code: {code}. Available: {list(LEP_REGISTRY.keys())}")
    return LEP_REGISTRY[code](lep_config)


def get_available_lep_codes() -> list[str]:
    """Return all available LEP codes."""
    return list(LEP_REGISTRY.keys())


def get_lep_name(code: str) -> str:
    """Get human-readable name for a LEP code."""
    return LEP_NAMES.get(code, code)
