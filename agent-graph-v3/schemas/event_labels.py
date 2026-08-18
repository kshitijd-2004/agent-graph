"""Event-level label structures.

Event labels annotate individual trace events. They are separate from
edge, path, and trace labels to avoid duplication and inconsistency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class EventLabelType(str, Enum):
    """Event-level annotation types."""
    # Injection lifecycle
    IS_INJECTION_ORIGIN = "is_injection_origin"

    # Consumption/transformation
    CONSUMES_PERTURBED_INFO = "consumes_perturbed_information"
    TRANSFORMS_PERTURBED_INFO = "transforms_perturbed_information"
    STORES_PERTURBED_INFO = "stores_perturbed_information"
    FORWARDS_PERTURBED_INFO = "forwards_perturbed_information"

    # Recovery
    RECOVERS_FROM_PERTURBATION = "recovers_from_perturbation"

    # Failure
    INTRODUCES_DOWNSTREAM_FAILURE = "introduces_downstream_failure"
    FAILURE_TYPE = "failure_type"
    AFFECTED_ENTITY_TYPE = "affected_entity_type"


class FailureType(str, Enum):
    """Types of failures an event can cause or represent."""
    NONE = "none"
    TOOL_ERROR = "tool_error"
    NUMERIC_ERROR = "numeric_error"
    FACTUAL_ERROR = "factual_error"
    OMISSION = "omission"
    MISATTRIBUTION = "misattribution"
    SAFETY_VIOLATION = "safety_violation"
    PREMATURE_TERMINATION = "premature_termination"
    LOOP = "loop"
    DERAILEMENT = "derailment"
    VERIFICATION_SKIP = "verification_skip"
    PROVENANCE_LOSS = "provenance_loss"


@dataclass
class EventLabels:
    """Annotations for a single trace event.

    These are the event-level labels from §9.1 of the spec.
    """
    # Injection
    is_injection_origin: bool = False

    # Perturbation flow
    consumes_perturbed_info: bool = False
    transforms_perturbed_info: bool = False
    stores_perturbed_info: bool = False
    forwards_perturbed_info: bool = False

    # Recovery
    recovers_from_perturbation: bool = False

    # Failure
    introduces_downstream_failure: bool = False
    failure_type: str = FailureType.NONE.value
    affected_entity_type: str = ""

    # Detection layer tags
    controlled_injection: bool = False
    abnormal_behavior: bool = False
    inferred_propagation: bool = False

    # Trigger tracking
    trigger_state: str = ""
    trigger_decision_log: List[Dict[str, Any]] = field(default_factory=list)
