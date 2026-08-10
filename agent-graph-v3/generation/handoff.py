"""Handoff payload for inter-agent communication.

Defines the structured data passed between agents when one hands off to another.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class HandoffPayload:
    """Structured handoff payload passed between agents.

    Carries the sending agent's findings, context, and metadata so the
    receiving agent can continue the task without re-doing completed work.
    """
    from_agent: str
    to_agent: str
    findings: List[str] = field(default_factory=list)
    source_paths: List[str] = field(default_factory=list)
    report_path: str = ""
    uncertainty: List[str] = field(default_factory=list)
    verification_requests: List[str] = field(default_factory=list)
    provenance_event_ids: List[str] = field(default_factory=list)
    summary: str = ""
    raw_output: str = ""
    contains_corrupted_data: bool = False
    corrupted_tool_call_event_id: str = ""
    # Arbitrary metadata for LEP interventions (input_disregard, etc.)
    extra: dict = field(default_factory=dict)

    def to_context_string(self) -> str:
        """Render the payload as a context block for the receiving agent."""
        parts = [f"[Handoff from {self.from_agent} to {self.to_agent}]"]
        if self.summary:
            parts.append(f"Summary: {self.summary}")
        if self.findings:
            parts.append("Findings:")
            for f in self.findings[:5]:
                parts.append(f"  - {f[:200]}")
        if self.source_paths:
            parts.append(f"Source paths: {', '.join(self.source_paths[:5])}")
        if self.report_path:
            parts.append(f"Report path: {self.report_path}")
        if self.uncertainty:
            parts.append("Uncertainties:")
            for u in self.uncertainty[:3]:
                parts.append(f"  - {u}")
        return "\n".join(parts)
