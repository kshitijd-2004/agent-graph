"""Trace event and trace data structures for AgentGraphs.

TraceEvent: One execution event (one JSON line in the trace file)
Trace:      One complete execution run (one trace file)
TraceVariant: Benign (a) or Malignant (b) variant of a paired run
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Union


class TraceEventType(str, Enum):
    """Types of events in an agent execution trace.

    These map directly to the event types in the JSONL trace files.
    The ordering reflects the typical lifecycle of a multi-agent run.
    """

    USER_INPUT = "user_input"
    SYSTEM_INIT = "system_init"
    REASONING = "reasoning"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    AGENT_HANDOFF = "agent_handoff"
    LLM_OUTPUT = "llm_output"
    MEMORY_RETRIEVAL = "memory_retrieval"
    MEMORY_WRITE = "memory_write"
    FINAL_RESPONSE = "final_response"

    @classmethod
    def from_string(cls, s: str) -> TraceEventType:
        """Parse event type from string (case-insensitive)."""
        mapping = {
            "user_input": cls.USER_INPUT,
            "system_init": cls.SYSTEM_INIT,
            "reasoning": cls.REASONING,
            "tool_call": cls.TOOL_CALL,
            "tool_result": cls.TOOL_RESULT,
            "agent_handoff": cls.AGENT_HANDOFF,
            "llm_output": cls.LLM_OUTPUT,
            "memory_retrieval": cls.MEMORY_RETRIEVAL,
            "memory_write": cls.MEMORY_WRITE,
            "final_response": cls.FINAL_RESPONSE,
        }
        key = s.strip().lower()
        if key not in mapping:
            raise ValueError(f"Unknown event type: {s!r}. Must be one of {list(mapping)}")
        return mapping[key]


class TraceVariant(str, Enum):
    """Which variant of a paired run this trace represents."""

    BENIGN = "a"
    MALIGNANT = "b"


@dataclass
class TraceEvent:
    """One execution event — corresponds to one JSON line in a trace file.

    Attributes:
        trace_id:         Per-file unique ID (e.g. "abc123a" or "abc123b")
        execution_id:     Shared between paired traces (e.g. "abc123")
        event_id:         1-based sequential index within this trace
        timestamp:        ISO-8601 UTC timestamp
        event_type:       Type of event (from TraceEventType)
        source:           Source entity ID (e.g. "agent_001")
        target:           Target entity ID (e.g. "tool_read_file")
        input_summary:    Brief summary of event input (truncated)
        output_summary:   Brief summary of event output (truncated)
        agent_id:         Agent entity ID (if applicable)
        agent_name:       Agent name (if applicable)
        agent_role:       Agent role description (if applicable)
        tool_id:          Tool entity ID (if applicable)
        tool_name:        Tool name (if applicable)
        expected_behavior: What should have happened
        observed_behavior: What actually happened
        lep_injected:     Whether this event was affected by an LEP
        lep_type:         LEP code + name (e.g. "FC2.2 Fail to Ask for Clarification")
        lep_category:     LEP category (e.g. "FC2")
        lep_location:     Where in the execution the LEP was injected
        lep_severity:     Severity level ("low", "medium", "high")
        risk_tags:        Additional risk classification tags
        caused_by_event:  Event ID of the root cause (if this is a downstream effect)
        depends_on:       List of event IDs this event depends on
        propagates_to:    List of event IDs this event's effects propagate to
        agent_id_from:    Source agent ID (for handoffs)
        agent_name_from:  Source agent name (for handoffs)
        agent_id_to:      Destination agent ID (for handoffs)
        agent_name_to:    Destination agent name (for handoffs)
        downstream_failure: Whether this event is a downstream failure
        failure_type:     Type of failure (if applicable)
        failure_event:    Event ID of the failure (if applicable)
    """

    # Identity
    trace_id: str
    execution_id: str
    timestamp: str
    event_type: Union[TraceEventType, str]
    source: str = ""
    target: str = ""
    input_summary: str = ""
    output_summary: str = ""
    event_id: int = 0  # set by _emit() after construction

    # Agent context
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None
    agent_role: Optional[str] = None

    # Tool context
    tool_id: Optional[str] = None
    tool_name: Optional[str] = None

    # Behavior
    expected_behavior: str = ""
    observed_behavior: str = ""

    # LEP fields
    lep_injected: bool = False
    lep_type: Optional[str] = None
    lep_category: Optional[str] = None
    lep_location: Optional[str] = None
    lep_severity: Optional[str] = None

    # Forensic
    risk_tags: List[str] = field(default_factory=list)

    # Causality
    caused_by_event: Optional[int] = None
    depends_on: List[int] = field(default_factory=list)
    propagates_to: List[int] = field(default_factory=list)

    # Handoff
    agent_id_from: Optional[str] = None
    agent_name_from: Optional[str] = None
    agent_id_to: Optional[str] = None
    agent_name_to: Optional[str] = None

    # Failure outcome
    downstream_failure: bool = False
    failure_type: Optional[str] = None
    failure_event: Optional[int] = None

    def __post_init__(self) -> None:
        if isinstance(self.event_type, str):
            self.event_type = TraceEventType.from_string(self.event_type)

    @property
    def is_perturbation(self) -> bool:
        """Whether this event carries an LEP perturbation."""
        return self.lep_injected

    @property
    def is_tool_event(self) -> bool:
        """Whether this is a tool call or tool result."""
        return self.event_type in (TraceEventType.TOOL_CALL, TraceEventType.TOOL_RESULT)

    @property
    def is_agent_event(self) -> bool:
        """Whether this event involves an agent."""
        return bool(self.agent_id)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a dictionary (for JSON storage)."""
        d = asdict(self)
        # Convert enums to their values
        d["event_type"] = self.event_type.value if isinstance(self.event_type, TraceEventType) else self.event_type
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> TraceEvent:
        """Deserialize from a dictionary."""
        d = dict(d)
        if "event_type" in d and isinstance(d["event_type"], str):
            d["event_type"] = TraceEventType.from_string(d["event_type"])
        return cls(**d)


@dataclass
class Trace:
    """One complete execution run — corresponds to one trace file.

    A trace contains a sequence of events from a single run of a multi-agent
    system. Paired traces (benign + malignant) share the same execution_id
    but have different trace_ids (suffix 'a' vs 'b').

    Attributes:
        trace_id:         Unique ID for this trace file (execution_id + variant)
        execution_id:     Shared ID linking paired benign/malignant traces
        variant:          Which variant (benign='a', malignant='b')
        events:           Ordered list of TraceEvents
        metadata:         Additional metadata (model, temperature, etc.)
        file_path:        Path to the JSONL file (if loaded from disk)
    """

    trace_id: str
    execution_id: str
    variant: TraceVariant
    events: List[TraceEvent] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    file_path: Optional[str] = None

    @property
    def num_events(self) -> int:
        """Number of events in this trace."""
        return len(self.events)

    @property
    def is_benign(self) -> bool:
        """Whether this is the benign variant."""
        return self.variant == TraceVariant.BENIGN

    @property
    def is_malignant(self) -> bool:
        """Whether this is the malignant variant."""
        return self.variant == TraceVariant.MALIGNANT

    @property
    def perturbation_events(self) -> List[TraceEvent]:
        """Events that carry LEP perturbations."""
        return [e for e in self.events if e.is_perturbation]

    @property
    def failure_events(self) -> List[TraceEvent]:
        """Events marked as downstream failures."""
        return [e for e in self.events if e.downstream_failure]

    @property
    def agent_ids(self) -> List[str]:
        """Unique agent IDs present in this trace."""
        return list({e.agent_id for e in self.events if e.agent_id})

    @property
    def tool_names(self) -> List[str]:
        """Unique tool names called in this trace."""
        return list({e.tool_name for e in self.events if e.tool_name})

    def get_events_by_type(self, event_type: TraceEventType) -> List[TraceEvent]:
        """Get all events of a specific type."""
        return [e for e in self.events if e.event_type == event_type]

    def get_events_by_agent(self, agent_id: str) -> List[TraceEvent]:
        """Get all events involving a specific agent."""
        return [e for e in self.events if e.agent_id == agent_id]

    def get_events_by_tool(self, tool_name: str) -> List[TraceEvent]:
        """Get all events involving a specific tool."""
        return [e for e in self.events if e.tool_name == tool_name]

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a dictionary."""
        return {
            "trace_id": self.trace_id,
            "execution_id": self.execution_id,
            "variant": self.variant.value,
            "num_events": len(self.events),
            "events": [e.to_dict() for e in self.events],
            "metadata": self.metadata,
            "file_path": self.file_path,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> Trace:
        """Deserialize from a dictionary."""
        events = [TraceEvent.from_dict(e) for e in d.get("events", [])]
        variant = TraceVariant(d["variant"]) if isinstance(d["variant"], str) else d["variant"]
        return cls(
            trace_id=d["trace_id"],
            execution_id=d["execution_id"],
            variant=variant,
            events=events,
            metadata=d.get("metadata", {}),
            file_path=d.get("file_path"),
        )
