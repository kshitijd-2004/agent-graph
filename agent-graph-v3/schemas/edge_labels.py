"""Edge-level label structures.

Edge annotations describe the relationship between two events
in the causal/propagation graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class PropagationRole(str, Enum):
    """Role of an edge in the propagation graph."""
    ORIGIN = "origin"                   # First injection event
    TRANSFER = "transfer"               # Direct handoff/forward
    TRANSFORMATION = "transformation"   # Information was modified
    STORAGE = "storage"                 # Written to memory/file
    CONVERGENCE = "convergence"         # Multiple sources combined
    RECOVERY = "recovery"               # Detection/correction
    TERMINAL_IMPACT = "terminal_impact" # Final failure event
    PROPAGATED = "propagated"           # Edge carries perturbation from upstream
    IS_INJECTION = "is_injection"       # Edge represents an injected/perturbed event
    UNKNOWN = "unknown"


@dataclass
class EdgeAnnotation:
    """Annotation for a directed edge between two events.

    Attributes:
        edge_id:              Unique edge identifier
        source_event_id:      Source event
        target_event_id:      Target event
        relation_type:        Kind of relationship
        carries_perturbation: Whether this edge carries perturbed info
        propagation_role:     Role in the propagation graph
        causal_strength:      Estimated causal strength (0.0-1.0)
        transformation_desc:  Description of any transformation
        metadata:             Additional fields
    """
    edge_id: str
    source_event_id: str
    target_event_id: str
    relation_type: str = "information_flow"
    carries_perturbation: bool = False
    propagation_role: str = PropagationRole.UNKNOWN.value
    causal_strength: float = 0.0
    transformation_desc: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
