"""LEP validation — ensures LEP codes are registered before use."""

from __future__ import annotations

import logging
from typing import Optional, Set

from schemas.lep_config import LEPConfig

logger = logging.getLogger(__name__)

# Cache of registered LEP codes
_REGISTERED: Optional[Set[str]] = None


def get_registered_codes() -> Set[str]:
    """Return the set of LEP codes currently registered in the registry."""
    global _REGISTERED
    if _REGISTERED is not None:
        return _REGISTERED
    try:
        from leps.registry import LEP_REGISTRY
        _REGISTERED = set(LEP_REGISTRY.keys())
    except ImportError:
        _REGISTERED = {
            "LEP_TOOL_RESULT_CORRUPTION",
            "LEP_INDIRECT_PROMPT_INJECTION",
            "LEP_MEMORY_POISONING",
            "LEP_HANDOFF_CORRUPTION",
            "LEP_INPUT_DISREGARD",
        }
    return _REGISTERED


def validate_lep_code(code: str, task_family: str = "") -> None:
    """Raise ValueError if LEP code is not registered."""
    registered = get_registered_codes()
    if code not in registered:
        raise ValueError(
            f"Unregistered LEP code '{code}' for task '{task_family}'. "
            f"Registered codes: {sorted(registered)}"
        )


def validate_lep_config(config: LEPConfig, task_family: str = "") -> None:
    """Validate a single LEPConfig."""
    validate_lep_code(config.code, task_family)


def validate_lep_configs(configs: list[LEPConfig], task_family: str = "") -> None:
    """Validate a list of LEPConfigs. Raises on the first failure."""
    for cfg in configs:
        validate_lep_config(cfg, task_family)
