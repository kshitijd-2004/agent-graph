"""Trigger system types for semantic LEP injection.

A trigger watches the event stream and fires when semantic conditions
are met. The lifecycle is:

    eligible → fired → exposed → consumed

Propagation and recovery are derived post-run labels, not trigger states.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union


class TriggerState(str, Enum):
    """Lifecycle states for a trigger."""
    ELIGIBLE = "eligible"          # Conditions could match, waiting for event
    FIRED = "fired"                # Trigger matched, injection applied
    EXPOSED = "exposed"            # Agent encountered the injected artifact
    CONSUMED = "consumed"          # Agent's behavior was influenced by it


class TriggerType(str, Enum):
    """How the LEP artifact enters the environment."""
    FILE_POISONING = "file_poisoning"         # Modify workspace file content
    MEMORY_INJECTION = "memory_injection"     # Insert/update memory record
    TOOL_RESULT_SUBSTITUTION = "tool_result_substitution"  # Replace tool output
    HANDOFF_CORRUPTION = "handoff_corruption"  # Modify handoff payload
    RETRIEVAL_RANKING = "retrieval_ranking"    # Alter retrieval results


@dataclass
class InjectionTrigger:
    """Semantic conditions for when an LEP should be injected.

    All fields are optional — the trigger fires when ALL specified
    conditions match simultaneously. An empty trigger (all None)
    fires immediately when the LEP is activated.

    Attributes:
        event_type:       Match a specific event type (e.g. "tool_call")
        source_agent:     Match events from a specific agent
        target_entity:    Match events targeting a specific entity
        tool_name:        Match a specific tool name
        path_pattern:     Glob-like pattern for file paths
        content_pattern:  Substring to search in tool args/output
        occurrence:       Which occurrence to match (1 = first)
        after_event_type: Only match after this event type has been seen
        after_tool_name:  Only match after this tool has been called
        min_event_index:  Earliest event index to consider
        max_event_index:  Latest event index to consider (None = no limit)
        probability:      Injection probability (for stochastic LEPs)
    """
    event_type: Optional[str] = None
    source_agent: Optional[str] = None
    target_entity: Optional[str] = None
    tool_name: Optional[str] = None
    path_pattern: Optional[str] = None
    content_pattern: Optional[str] = None
    occurrence: int = 1
    after_event_type: Optional[str] = None
    after_tool_name: Optional[str] = None
    min_event_index: int = 0
    max_event_index: Optional[int] = None
    probability: float = 1.0


@dataclass
class TriggerDecision:
    """Logged decision from trigger matching.

    Every trigger evaluation produces one of these, stored in
    hidden_benchmark_metadata on the matched event.
    """
    trigger_id: str
    event_id: str
    matched: bool
    fired: bool
    state: TriggerState
    reason: str
    occurrence_count: int = 0
    matched_conditions: Dict[str, Any] = field(default_factory=dict)
