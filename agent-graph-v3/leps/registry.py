"""LEP registry and orchestrator.

Maps LEP codes to implementations and coordinates injection
during trace execution.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from schemas import LEPConfig

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


class LEPOrchestrator:
    """Coordinates LEP instances during trace execution.

    Manages:
    - Creating LEP instances from LEPConfigs
    - Evaluating triggers against trace events
    - Coordinating injection across tool calls, memory, and handoffs
    - Tracking propagation through events
    - Resetting state between runs
    """

    def __init__(self):
        self._active_leps: Dict[str, Any] = {}  # lep_code -> LEP instance
        self._trigger_results: list = []

    def register_lep(self, lep_config: LEPConfig) -> None:
        """Register a LEP for execution."""
        code = lep_config.code
        if code not in LEP_REGISTRY:
            raise ValueError(f"Unknown LEP code: {code}. Available: {list(LEP_REGISTRY.keys())}")

        lep_class = LEP_REGISTRY[code]
        instance = lep_class(lep_config)
        self._active_leps[code] = instance
        logger.debug("Registered LEP: %s (%s)", code, LEP_NAMES.get(code, code))

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

        Returns dict mapping lep_code -> TriggerDecision for each LEP.
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
            file_path = kwargs.get("file_path", "")
            variant = kwargs.get("variant", "ignore_previous")
            return lep.inject_into_content(file_path, tool_result, variant)
        elif hasattr(lep, "poison"):
            memory_key = kwargs.get("memory_key", "")
            task_family = kwargs.get("task_family", "financial_analysis")
            return lep.poison(memory_key, task_family, **kwargs)
        elif hasattr(lep, "corrupt"):
            return lep.corrupt(event, tool_result, **kwargs)
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
