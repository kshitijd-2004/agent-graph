"""Localized Execution Perturbation (LEP) implementations.

LEPs introduce controlled abnormalities into agent workflow execution
to study propagation patterns and downstream failure modes.

Available LEP families:
- Tool Result Corruption (LEP_TOOL_RESULT_CORRUPTION)
- Indirect Prompt Injection (LEP_INDIRECT_PROMPT_INJECTION)
- Memory Poisoning (LEP_MEMORY_POISONING)
- Handoff Corruption (LEP_HANDOFF_CORRUPTION)
- Input Disregard (LEP_INPUT_DISREGARD)
"""

from leps.registry import (
    LEP_REGISTRY,
    LEP_NAMES,
    LEPOrchestrator,
    create_lep_instance,
    get_available_lep_codes,
    get_lep_name,
)
from leps.tool_result_corruption import ToolResultCorruptionLEP, ToolResultCorruptionResult
from leps.indirect_prompt_injection import IndirectPromptInjectionLEP, IndirectInjectionResult
from leps.memory_poisoning import MemoryPoisoningLEP, MemoryPoisoningResult
from leps.handoff_corruption import HandoffCorruptionLEP, HandoffCorruptionResult
from leps.input_disregard import InputDisregardLEP, InputDisregardResult

__all__ = [
    # Registry
    "LEP_REGISTRY",
    "LEP_NAMES",
    "LEPOrchestrator",
    "create_lep_instance",
    "get_available_lep_codes",
    "get_lep_name",
    # LEP classes
    "ToolResultCorruptionLEP",
    "ToolResultCorruptionResult",
    "IndirectPromptInjectionLEP",
    "IndirectInjectionResult",
    "MemoryPoisoningLEP",
    "MemoryPoisoningResult",
    "HandoffCorruptionLEP",
    "HandoffCorruptionResult",
    "InputDisregardLEP",
    "InputDisregardResult",
]
