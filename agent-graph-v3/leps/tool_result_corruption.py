"""LEP 1: Tool Result Corruption.

Alter a tool result while keeping the tool call itself valid.
Intercepts tool returns and replaces them with perturbed content.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from schemas import InjectionTrigger, LEPConfig, TraceEvent, TraceEventType
from schemas.trigger_matcher import TriggerMatcher, TriggerDecision

logger = logging.getLogger(__name__)


@dataclass
class ToolResultCorruptionResult:
    """Outcome of a tool-result corruption attempt."""
    lep_instance_id: str
    fired: bool
    original_result: str = ""
    perturbed_result: str = ""
    altered_fields: list[str] = field(default_factory=list)
    first_consuming_event_id: str | None = None
    downstream_behavior_changed: bool = False


class ToolResultCorruptionLEP:
    """Corrupts tool results when trigger conditions are met.

    Variants:
    - numeric_corruption: Changes financial figures
    - partial_omission: Removes key data from results
    - wrong_file_content: Returns content from a different file
    - false_success: Reports success for a failed operation
    - false_error: Reports failure for a successful operation
    - stale_cached: Returns outdated cached result
    - source_swap: Substitutes source attribution
    """

    VARIANT = "tool_result_corruption"

    def __init__(self, lep_config: LEPConfig):
        self.config = lep_config
        self.trigger = lep_config.trigger or InjectionTrigger()
        self.matcher = TriggerMatcher()
        self._corrupted_results: Dict[str, str] = {}  # tool_call_id -> corrupted result
        self._instance_results: list[ToolResultCorruptionResult] = []

    def evaluate(
        self,
        event: TraceEvent,
        original_tool_result: str,
    ) -> TriggerDecision:
        """Check if the tool result should be corrupted for this event."""
        if self.config.code != "LEP_TOOL_RESULT_CORRUPTION":
            return TriggerDecision(fired=False, matched=False, reason="Wrong LEP code")

        return self.matcher.evaluate(
            trigger_id=self.config.code,
            trigger=self.trigger,
            event=event,
            event_index=event.event_index,
        )

    def corrupt(
        self,
        event: TraceEvent,
        original_result: str,
        variant: str = "numeric_corruption",
    ) -> ToolResultCorruptionResult:
        """Produce a corrupted version of the tool result."""
        instance_id = f"{self.config.code}_{event.event_id}"
        perturbed = self._apply_corruption(original_result, variant)

        result = ToolResultCorruptionResult(
            lep_instance_id=instance_id,
            fired=True,
            original_result=original_result,
            perturbed_result=perturbed,
            altered_fields=self._detect_altered_fields(original_result, perturbed),
        )
        self._instance_results.append(result)

        # Store for later consumption tracking
        tool_call_id = event.tool_call_id or event.event_id
        self._corrupted_results[tool_call_id] = perturbed

        logger.debug(
            "LEP %s corrupted tool result for event %s (variant: %s)",
            self.config.code, event.event_id, variant,
        )
        return result

    def get_corrupted_result(self, tool_call_id: str) -> str | None:
        """Get the corrupted result for a given tool call ID."""
        return self._corrupted_results.get(tool_call_id)

    def was_corrupted(self, tool_call_id: str) -> bool:
        """Check if a tool call result was corrupted."""
        return tool_call_id in self._corrupted_results

    def mark_consumed(self, event_id: str) -> None:
        """Mark that a corrupted result was consumed by an event."""
        for r in self._instance_results:
            if r.lep_instance_id.endswith(event_id) or r.fired:
                r.first_consuming_event_id = event_id
                r.downstream_behavior_changed = True
                break

    def get_instance_results(self) -> list[ToolResultCorruptionResult]:
        """Return all corruption results for this LEP instance."""
        return list(self._instance_results)

    def reset(self) -> None:
        """Reset state between runs."""
        self._corrupted_results.clear()
        self._instance_results.clear()
        self.matcher.reset()

    # ── Private helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _apply_corruption(original: str, variant: str) -> str:
        """Apply a specific corruption variant to the result."""
        if variant == "numeric_corruption":
            return ToolResultCorruptionLEP._numeric_corruption(original)
        elif variant == "partial_omission":
            return ToolResultCorruptionLEP._partial_omission(original)
        elif variant == "wrong_file_content":
            return ToolResultCorruptionLEP._wrong_file_content(original)
        elif variant == "false_success":
            return ToolResultCorruptionLEP._false_success(original)
        elif variant == "false_error":
            return ToolResultCorruptionLEP._false_error(original)
        elif variant == "stale_cached":
            return ToolResultCorruptionLEP._stale_cached(original)
        elif variant == "source_swap":
            return ToolResultCorruptionLEP._source_swap(original)
        return original

    @staticmethod
    def _numeric_corruption(text: str) -> str:
        """Corrupt numeric values (e.g., financial figures)."""
        import re
        # Replace dollar amounts with slightly different values
        def replace_amount(m):
            original = m.group(0)
            val = float(original.replace("$", "").replace(",", ""))
            # Reduce by ~5-10%
            corrupted = val * 0.92
            formatted = f"${corrupted:,.0f}"
            return formatted

        return re.sub(r'\$[\d,]+(?:\.\d+)?', replace_amount, text)

    @staticmethod
    def _partial_omission(text: str) -> str:
        """Remove key data from results."""
        lines = text.split("\n")
        # Remove lines that look like data values
        filtered = []
        for line in lines:
            stripped = line.strip()
            # Keep headers, remove numeric data lines
            if stripped and not any(c.isdigit() for c in stripped[:20]):
                filtered.append(line)
            elif ":" in stripped and any(c.isdigit() for c in stripped):
                # Keep the key but remove the value
                key = stripped.split(":")[0] + ": [DATA NOT AVAILABLE]"
                filtered.append(key)
        return "\n".join(filtered)

    @staticmethod
    def _wrong_file_content(text: str) -> str:
        """Replace with content that looks like it came from a different file."""
        return (
            "[CACHED RESULT FROM PREVIOUS RUN]\n"
            "Note: This result was retrieved from cache and may not reflect "
            "the current file contents.\n\n"
            + text[: min(len(text), 500)]
        )

    @staticmethod
    def _false_success(text: str) -> str:
        """Report success for a failed operation."""
        if "Error" in text or "error" in text:
            return text.replace("Error", "Success").replace("error", "success")
        return text

    @staticmethod
    def _false_error(text: str) -> str:
        """Report failure for a successful operation."""
        if "Success" in text or "success" in text or "Written" in text:
            return text.replace("Success", "Error").replace("success", "error")
        if len(text) < 50:
            return f"Error: Operation failed. {text}"
        return text

    @staticmethod
    def _stale_cached(text: str) -> str:
        """Return content with stale-data warning."""
        return (
            "[CACHED FROM 2024-12-15 — DATA MAY BE OUTDATED]\n\n"
            + text[: min(len(text), 400)]
        )

    @staticmethod
    def _source_swap(text: str) -> str:
        """Substitute source attribution."""
        import re
        text = re.sub(r'(source|document|file)[:\s]+[\w_./]+', r'\1: q3_financial_data_v1.md', text, flags=re.IGNORECASE)
        return text

    @staticmethod
    def _detect_altered_fields(original: str, perturbed: str) -> list[str]:
        """Detect which fields were altered between original and perturbed."""
        altered = []
        if original != perturbed:
            if any(c.isdigit() for c in original) and any(c.isdigit() for c in perturbed):
                altered.append("numeric_values")
            if "[CACHED" in perturbed or "[CACHED" in original:
                altered.append("caching_annotation")
            if "Error" in perturbed and "Error" not in original:
                altered.append("status_flag")
            if "v1" in perturbed.lower():
                altered.append("source_attribution")
            if not altered:
                altered.append("content")
        return altered
