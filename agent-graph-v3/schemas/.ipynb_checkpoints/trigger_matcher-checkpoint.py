"""Semantic trigger matcher for LEP injection.

Watches the event stream and fires LEPs when semantic conditions match.
Implements the eligible → fired → exposed → consumed lifecycle.

Key invariants:
- Idempotent: a given trigger fires at most once per scenario
- Immutable snapshots: triggers only see the observable event data
- Decision logging: every evaluation is logged for audit
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, Set

from .triggers import (
    InjectionTrigger,
    TriggerDecision,
    TriggerState,
    TriggerType,
)
from .trace_event import TraceEvent

logger = logging.getLogger(__name__)


class TriggerMatcher:
    """Matches events against LEP injection triggers.

    Tracks occurrence counts per trigger to support occurrence-based
    matching (e.g., "fire on the 2nd read of this file").

    Usage:
        matcher = TriggerMatcher()
        for event in event_stream:
            decisions = matcher.evaluate(event)
            if decisions:
                # Apply injections...
    """

    def __init__(self) -> None:
        self._occurrence_counts: Dict[str, int] = {}
        self._fired_triggers: Set[str] = set()
        self._decisions: List[TriggerDecision] = []
        self._after_event_seen: Dict[str, bool] = {}

    def evaluate(
        self,
        trigger_id: str,
        trigger: InjectionTrigger,
        event: TraceEvent,
        event_index: int,
    ) -> TriggerDecision:
        """Evaluate a single trigger against a single event.

        Returns a TriggerDecision regardless of match result.
        Never raises — all conditions are checked defensively.
        """
        # Check index bounds
        if event_index < trigger.min_event_index:
            return self._log_decision(
                trigger_id, event, matched=False, fired=False,
                state=TriggerState.ELIGIBLE,
                reason=f"event_index {event_index} < min {trigger.min_event_index}",
            )

        if trigger.max_event_index is not None and event_index > trigger.max_event_index:
            return self._log_decision(
                trigger_id, event, matched=False, fired=False,
                state=TriggerState.ELIGIBLE,
                reason=f"event_index {event_index} > max {trigger.max_event_index}",
            )

        # Check idempotency — already fired
        if trigger_id in self._fired_triggers:
            return self._log_decision(
                trigger_id, event, matched=False, fired=False,
                state=TriggerState.ELIGIBLE,
                reason="already fired (idempotent)",
            )

        # Check "after" prerequisites
        if trigger.after_event_type and not self._after_event_seen.get(trigger.after_event_type, False):
            return self._log_decision(
                trigger_id, event, matched=False, fired=False,
                state=TriggerState.ELIGIBLE,
                reason=f"prerequisite event_type '{trigger.after_event_type}' not yet seen",
            )

        if trigger.after_tool_name and not self._after_event_seen.get(f"tool:{trigger.after_tool_name}", False):
            return self._log_decision(
                trigger_id, event, matched=False, fired=False,
                state=TriggerState.ELIGIBLE,
                reason=f"prerequisite tool '{trigger.after_tool_name}' not yet called",
            )

        # Check probability
        import random
        if trigger.probability < 1.0 and random.random() > trigger.probability:
            return self._log_decision(
                trigger_id, event, matched=False, fired=False,
                state=TriggerState.ELIGIBLE,
                reason=f"probability check failed ({trigger.probability})",
            )

        # Match conditions
        conditions = self._match_conditions(trigger, event)
        if not conditions["all_match"]:
            return self._log_decision(
                trigger_id, event, matched=False, fired=False,
                state=TriggerState.ELIGIBLE,
                reason=f"conditions not met: {conditions['unmatched']}",
                matched_conditions=conditions,
            )

        # Check occurrence
        count = self._occurrence_counts.get(trigger_id, 0) + 1
        self._occurrence_counts[trigger_id] = count

        if count < trigger.occurrence:
            return self._log_decision(
                trigger_id, event, matched=True, fired=False,
                state=TriggerState.ELIGIBLE,
                reason=f"occurrence {count} < required {trigger.occurrence}",
                matched_conditions=conditions,
                occurrence_count=count,
            )

        # Fire!
        self._fired_triggers.add(trigger_id)
        return self._log_decision(
            trigger_id, event, matched=True, fired=True,
            state=TriggerState.FIRED,
            reason=f"all conditions met on occurrence {count}",
            matched_conditions=conditions,
            occurrence_count=count,
        )

    def mark_event_type_seen(self, event_type: str) -> None:
        """Record that an event type has been seen (for 'after' prerequisites)."""
        self._after_event_seen[event_type] = True

    def mark_tool_seen(self, tool_name: str) -> None:
        """Record that a tool has been called (for 'after' prerequisites)."""
        self._after_event_seen[f"tool:{tool_name}"] = True

    def is_fired(self, trigger_id: str) -> bool:
        """Whether a trigger has already fired."""
        return trigger_id in self._fired_triggers

    def get_decisions(self) -> List[TriggerDecision]:
        """All logged trigger decisions."""
        return list(self._decisions)

    def reset(self) -> None:
        """Reset state for a new scenario."""
        self._occurrence_counts.clear()
        self._fired_triggers.clear()
        self._decisions.clear()
        self._after_event_seen.clear()

    # ── Private ─────────────────────────────────────────────────────────────

    def _match_conditions(
        self, trigger: InjectionTrigger, event: TraceEvent
    ) -> Dict[str, Any]:
        """Check all trigger conditions against an event.

        Returns dict with 'all_match' bool and 'unmatched' list.
        """
        unmatched = []

        if trigger.event_type:
            actual = event.event_type.value if hasattr(event.event_type, 'value') else str(event.event_type)
            if actual.lower() != trigger.event_type.lower():
                unmatched.append(f"event_type: {actual} != {trigger.event_type}")

        if trigger.source_agent:
            if event.agent_id != trigger.source_agent:
                unmatched.append(f"source_agent: {event.agent_id} != {trigger.source_agent}")

        if trigger.target_entity:
            if event.target_entity_id != trigger.target_entity:
                unmatched.append(f"target_entity: {event.target_entity_id} != {trigger.target_entity}")

        if trigger.tool_name:
            if event.tool_name != trigger.tool_name:
                unmatched.append(f"tool_name: {event.tool_name} != {trigger.tool_name}")

        if trigger.path_pattern:
            path = event.tool_arguments.get("path", "") if event.tool_arguments else ""
            if not fnmatch.fnmatch(path, trigger.path_pattern):
                unmatched.append(f"path_pattern: {path} !~ {trigger.path_pattern}")

        if trigger.content_pattern:
            search_text = ""
            if event.tool_arguments:
                search_text += json.dumps(event.tool_arguments)
            if event.input_text:
                search_text += " " + event.input_text
            if trigger.content_pattern not in search_text:
                unmatched.append(f"content_pattern not found")

        return {
            "all_match": len(unmatched) == 0,
            "unmatched": unmatched,
        }

    def _log_decision(
        self,
        trigger_id: str,
        event: TraceEvent,
        matched: bool,
        fired: bool,
        state: TriggerState,
        reason: str,
        matched_conditions: Optional[Dict[str, Any]] = None,
        occurrence_count: int = 0,
    ) -> TriggerDecision:
        decision = TriggerDecision(
            trigger_id=trigger_id,
            event_id=event.event_id,
            matched=matched,
            fired=fired,
            state=state,
            reason=reason,
            occurrence_count=occurrence_count,
            matched_conditions=matched_conditions or {},
        )
        self._decisions.append(decision)
        logger.debug(
            "Trigger %s on event %s: matched=%s fired=%s state=%s reason=%s",
            trigger_id, event.event_id, matched, fired, state.value, reason,
        )
        return decision
