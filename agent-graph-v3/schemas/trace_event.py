"""Core trace event type — v3 redesign with observable/hidden split."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from schemas.event_labels import EventLabels


class TraceEventType(str, Enum):
    """Types of events in an agent execution trace."""
    USER_INPUT = "user_input"
    SYSTEM_INIT = "system_init"
    REASONING = "reasoning"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    AGENT_HANDOFF = "agent_handoff"
    TOPOLOGY_TRANSITION = "topology_transition"
    LLM_OUTPUT = "llm_output"
    MEMORY_RETRIEVAL = "memory_retrieval"
    MEMORY_WRITE = "memory_write"
    FINAL_RESPONSE = "final_response"

    @classmethod
    def from_string(cls, s: str) -> "TraceEventType":
        mapping = {
            "user_input": cls.USER_INPUT,
            "system_init": cls.SYSTEM_INIT,
            "reasoning": cls.REASONING,
            "tool_call": cls.TOOL_CALL,
            "tool_result": cls.TOOL_RESULT,
            "agent_handoff": cls.AGENT_HANDOFF,
            "topology_transition": cls.TOPOLOGY_TRANSITION,
            "llm_output": cls.LLM_OUTPUT,
            "memory_retrieval": cls.MEMORY_RETRIEVAL,
            "memory_write": cls.MEMORY_WRITE,
            "final_response": cls.FINAL_RESPONSE,
        }
        key = s.strip().lower()
        if key not in mapping:
            raise ValueError(f"Unknown event type: {s!r}")
        return mapping[key]


@dataclass
class TraceEvent:
    """One execution event in a trace.

    Attributes are split into:
    - Identity fields (always present)
    - observable: dict — available to a runtime detector
    - hidden: dict — benchmark-only, excluded from model inputs
    - event_labels: EventLabels — event-level annotations
    """
    # ── Identity ────────────────────────────────────────────────────────────
    trace_id: str
    event_id: str
    event_index: int           # global monotonic index within the trace
    timestamp: str
    event_type: Union[TraceEventType, str]
    stage_event_index: Optional[int] = None  # local index within the stage, None if not applicable

    # ── Entity references ───────────────────────────────────────────────────
    source_entity_id: str = ""
    target_entity_id: str = ""
    source_entity_type: str = ""
    target_entity_type: str = ""

    # ── Agent context ───────────────────────────────────────────────────────
    agent_id: str = ""
    agent_role: str = ""

    # ── Tool context ────────────────────────────────────────────────────────
    tool_name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_arguments: Optional[Dict[str, Any]] = None
    tool_result: Optional[str] = None
    tool_error: Optional[str] = None

    # ── Content ─────────────────────────────────────────────────────────────
    input_text: Optional[str] = None
    output_text: Optional[str] = None
    input_hash: Optional[str] = None
    output_hash: Optional[str] = None

    # ── Memory context ──────────────────────────────────────────────────────
    memory_key: Optional[str] = None
    memory_scope: Optional[str] = None

    # ── Provenance ──────────────────────────────────────────────────────────
    document_path: Optional[str] = None
    provenance_ids: List[str] = field(default_factory=list)
    depends_on: List[str] = field(default_factory=list)

    # ── Observable (detector-visible) ───────────────────────────────────────
    observable: Dict[str, Any] = field(default_factory=dict)

    # ── Hidden (benchmark-only) ─────────────────────────────────────────────
    hidden: Dict[str, Any] = field(default_factory=dict)

    # ── Event-level labels ──────────────────────────────────────────────────
    event_labels: EventLabels = field(default_factory=EventLabels)

    def __post_init__(self) -> None:
        if isinstance(self.event_type, str):
            self.event_type = TraceEventType.from_string(self.event_type)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for JSON storage (internal use only)."""
        d = asdict(self)
        d["event_type"] = self.event_type.value if isinstance(self.event_type, TraceEventType) else self.event_type
        return d

    def to_observable_dict(self) -> Dict[str, Any]:
        """Serialize to a dictionary safe for model-observable export.

        Explicitly excludes hidden fields, event labels, and benchmark metadata.
        This is the security boundary — never add hidden data here.
        """
        d: Dict[str, Any] = {
            "trace_id": self.trace_id,
            "event_id": self.event_id,
            "event_index": self.event_index,
            "timestamp": self.timestamp,
            "event_type": self.event_type.value if isinstance(self.event_type, TraceEventType) else self.event_type,
            "source_entity_id": self.source_entity_id,
            "target_entity_id": self.target_entity_id,
            "source_entity_type": self.source_entity_type,
            "target_entity_type": self.target_entity_type,
            "agent_id": self.agent_id,
            "agent_role": self.agent_role,
            "tool_name": self.tool_name,
            "tool_call_id": self.tool_call_id,
            "tool_arguments": self.tool_arguments,
            "tool_result": self.tool_result,
            "tool_error": self.tool_error,
            "input_text": self.input_text,
            "output_text": self.output_text,
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "memory_key": self.memory_key,
            "memory_scope": self.memory_scope,
            "document_path": self.document_path,
            "provenance_ids": list(self.provenance_ids),
            "depends_on": list(self.depends_on),
            "observable": dict(self.observable),
        }
        # Prune None values for cleaner output
        return {k: v for k, v in d.items() if v is not None}

    def to_analysis_dict(self) -> Dict[str, Any]:
        """Serialize for analysis/ground-truth export (includes event labels)."""
        d = self.to_observable_dict()
        # Event labels are analysis-only, not model-observable
        if self.event_labels:
            d["event_labels"] = self.event_labels.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TraceEvent":
        """Deserialize from dictionary."""
        d = dict(d)
        if "event_type" in d and isinstance(d["event_type"], str):
            d["event_type"] = TraceEventType.from_string(d["event_type"])
        if "event_labels" in d and isinstance(d["event_labels"], dict):
            d["event_labels"] = EventLabels(**d["event_labels"])
        return cls(**d)
