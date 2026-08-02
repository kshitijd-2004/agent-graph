"""LEP (Localized Execution Perturbation) configuration types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .triggers import InjectionTrigger, TriggerType


@dataclass
class LEPConfig:
    """Configuration for one LEP family.

    Supports both legacy step-based injection (for backward compat)
    and new semantic trigger-based injection.

    Attributes:
        code:                       Stable identifier (e.g. "LEP_TOOL_RESULT_CORRUPTION")
        name:                       Human-readable name
        category:                   LEP family category
        description:                What this LEP does
        target_agent:               Which agent this LEP targets (None = any)

        trigger:                    Semantic trigger conditions (preferred)
        legacy_injection_steps:     Legacy step-based injection (backward compat)

        injection_surface:          What part of the environment is modified
        propagation_mode:           How the perturbation spreads
        expected_effect:            What behavioral effect is expected
        severity:                   "low", "medium", "high"

        requires_memory:            Whether this LEP needs memory access
        requires_retrieval:         Whether this LEP needs retrieval
        requires_handoff:           Whether this LEP needs handoff between agents
        requires_sensitive_action:  Whether this LEP targets a sensitive action

        observable_to_detector:     Whether a runtime detector could observe this
        supports_controlled_injection:   Can we inject this deterministically?
        supports_behavioral_detection:    Can we detect it from behavior alone?
        supports_deterministic_provenance: Can we track its propagation deterministically?

        metadata:                   Arbitrary additional fields
    """
    code: str
    name: str
    category: str
    description: str
    target_agent: Optional[str] = None

    trigger: Optional[InjectionTrigger] = None
    legacy_injection_steps: List[int] = field(default_factory=list)

    injection_surface: str = ""
    propagation_mode: str = ""
    expected_effect: str = ""
    severity: str = "medium"

    requires_memory: bool = False
    requires_retrieval: bool = False
    requires_handoff: bool = False
    requires_sensitive_action: bool = False

    observable_to_detector: bool = True
    supports_controlled_injection: bool = True
    supports_behavioral_detection: bool = False
    supports_deterministic_provenance: bool = True

    metadata: Dict[str, Any] = field(default_factory=dict)
