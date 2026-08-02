"""Path-level and trace-level label structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PropagationPath:
    """A causal chain from an injection origin to a terminal event.

    Attributes:
        path_id:                 Unique path identifier
        origin_event_id:         First event where LEP was injected
        first_consumption_event: First event that consumed perturbed info
        first_cross_agent_event: First event crossing an agent boundary
        first_memory_event:      First event crossing a memory boundary
        first_sensitive_event:   First event reaching a sensitive sink
        terminal_event_id:       Final failure or terminal event
        event_ids:               Ordered list of event IDs in the path
        edge_ids:                Ordered list of edge IDs in the path
        path_length:             Number of events
        temporal_delay:          Events between injection and first effect
        num_transformations:     How many times info was transformed
        num_agents_crossed:      How many agent boundaries crossed
        recovered:               Whether the path was recovered from
        contained:               Whether the perturbation was contained
        metadata:                Additional fields
    """
    path_id: str
    origin_event_id: str
    terminal_event_id: str
    event_ids: List[str] = field(default_factory=list)
    edge_ids: List[str] = field(default_factory=list)

    first_consumption_event: Optional[str] = None
    first_cross_agent_event: Optional[str] = None
    first_memory_event: Optional[str] = None
    first_sensitive_event: Optional[str] = None

    path_length: int = 0
    temporal_delay: int = 0
    num_transformations: int = 0
    num_agents_crossed: int = 0
    recovered: bool = False
    contained: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TraceLabels:
    """Trace-level outcome labels.

    These are the aggregate labels from §9.4 of the spec.
    Computed from event-level and path-level labels after a trace completes.
    """
    # Outcome
    task_success: bool = False
    downstream_failure: bool = False
    failure_type: str = ""

    # Perturbation exposure
    lep_exposed: bool = False
    lep_consumed: bool = False
    lep_propagated: bool = False

    # Recovery
    containment_success: bool = False
    recovery_success: bool = False

    # Propagation metrics
    propagation_depth: int = 0
    propagation_fanout: int = 0
    cross_agent_transfer_count: int = 0
    memory_boundary_count: int = 0
    time_to_first_effect: Optional[int] = None  # events
    time_to_failure: Optional[int] = None        # events

    # Multi-LEP
    number_of_contributing_leps: int = 0
    is_convergence_scenario: bool = False

    # Evaluation scores (populated by TaskEvaluator)
    factual_score: float = 0.0
    completeness_score: float = 0.0
    provenance_score: float = 0.0
    policy_score: float = 0.0
    action_safety_score: float = 0.0

    evaluator_confidence: float = 0.0
    evaluator_notes: List[str] = field(default_factory=list)
