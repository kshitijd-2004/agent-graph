"""Trace-level structures."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional

from .trace_event import TraceEvent, TraceEventType
from .trace_labels import TraceLabels
from .edge_labels import EdgeAnnotation
from .provenance import ProvenanceChain


class TraceVariant(str, Enum):
    """Which variant of a paired run this trace represents."""
    BENIGN = "a"
    MALIGNANT = "b"


@dataclass
class Trace:
    """One complete execution run.

    A trace contains a sequence of events from a single run.
    Paired traces (benign + malignant) share execution_id
    but have different trace_ids (suffix 'a' vs 'b').

    Attributes:
        trace_id:         Unique ID (execution_id + variant)
        execution_id:     Shared ID linking paired traces
        variant:          BENIGN or MALIGNANT
        schema_version:   Schema version for compatibility
        events:           Ordered list of TraceEvents
        edge_annotations: Inter-event relationship annotations
        paths:            Propagation paths
        provenance_chains: Provenance chains for key information
        labels:           Trace-level outcome labels
        metadata:         Additional metadata
        file_path:        Path to JSONL file (if loaded from disk)
    """
    trace_id: str
    execution_id: str
    variant: TraceVariant
    schema_version: str = "3.0.0"
    events: List[TraceEvent] = field(default_factory=list)
    edge_annotations: List[EdgeAnnotation] = field(default_factory=list)
    paths: List[PropagationPath] = field(default_factory=list)
    provenance_chains: List[ProvenanceChain] = field(default_factory=list)
    labels: TraceLabels = field(default_factory=TraceLabels)
    metadata: Dict[str, Any] = field(default_factory=dict)
    file_path: Optional[str] = None

    # ── Convenience properties ──────────────────────────────────────────────

    @property
    def num_events(self) -> int:
        return len(self.events)

    @property
    def is_benign(self) -> bool:
        return self.variant == TraceVariant.BENIGN

    @property
    def is_malignant(self) -> bool:
        return self.variant == TraceVariant.MALIGNANT

    @property
    def perturbation_events(self) -> List[TraceEvent]:
        return [e for e in self.events if e.event_labels.is_injection_origin]

    @property
    def failure_events(self) -> List[TraceEvent]:
        return [e for e in self.events if e.event_labels.introduces_downstream_failure]

    def get_events_by_type(self, event_type: TraceEventType) -> List[TraceEvent]:
        return [e for e in self.events if e.event_type == event_type]

    def get_events_by_agent(self, agent_id: str) -> List[TraceEvent]:
        return [e for e in self.events if e.agent_id == agent_id]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "execution_id": self.execution_id,
            "variant": self.variant.value,
            "schema_version": self.schema_version,
            "num_events": len(self.events),
            "events": [e.to_dict() for e in self.events],
            "metadata": self.metadata,
            "file_path": self.file_path,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Trace":
        events = [TraceEvent.from_dict(e) for e in d.get("events", [])]
        variant = TraceVariant(d["variant"]) if isinstance(d["variant"], str) else d["variant"]
        return cls(
            trace_id=d["trace_id"],
            execution_id=d["execution_id"],
            variant=variant,
            schema_version=d.get("schema_version", "3.0.0"),
            events=events,
            metadata=d.get("metadata", {}),
            file_path=d.get("file_path"),
        )
