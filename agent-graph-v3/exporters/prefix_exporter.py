"""Prefix exporter — generates graph/sequence prefixes for early-warning evaluation.

For each trace of length T, produces prefixes at key milestones:
- pre-injection prefix
- injection prefix
- first-consumption prefix
- first-cross-agent prefix
- pre-failure prefix
- failure prefix
- final prefix

Plus regular sampling (every Nth event for long traces).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class PrefixRecord:
    """A single prefix record for early-warning evaluation."""
    trace_id: str
    prefix_end_event: int
    normalized_progress: float
    future_downstream_failure: bool
    failure_within_5_events: bool
    failure_within_10_events: bool
    time_to_failure: int | None
    origin_observed: bool
    propagation_observed: bool
    events_in_prefix: int
    prefix_events: List[Dict[str, Any]] = field(default_factory=list)


class PrefixExporter:
    """Generate prefix datasets from traces."""

    def __init__(self, output_dir: Path, sampling_interval: int = 5):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.sampling_interval = sampling_interval

    def _is_lep_injected(self, event: Any) -> bool:
        """Check if an event has an LEP injection (either direct or via event_labels)."""
        if hasattr(event, "lep_injected") and event.lep_injected:
            return True
        labels = getattr(event, "event_labels", None)
        if labels and hasattr(labels, "is_injection_origin") and labels.is_injection_origin:
            return True
        if labels and hasattr(labels, "controlled_injection") and labels.controlled_injection:
            return True
        return False

    def _is_failure(self, event: Any) -> bool:
        """Check if an event is a downstream failure."""
        if hasattr(event, "downstream_failure") and event.downstream_failure:
            return True
        labels = getattr(event, "event_labels", None)
        if labels and hasattr(labels, "introduces_downstream_failure") and labels.introduces_downstream_failure:
            return True
        return False

    def export_prefixes(self, trace: Any) -> List[PrefixRecord]:
        """Generate all prefix records for a single trace."""
        events = trace.events
        if not events:
            return []

        records = []
        total_events = len(events)

        # Find key milestone events
        injection_events = [e for e in events if self._is_lep_injected(e)]
        failure_events = [e for e in events if self._is_failure(e)]
        handoff_events = [e for e in events if e.event_type.value == "agent_handoff"]

        first_injection_idx = injection_events[0].event_index if injection_events else None
        first_failure_idx = failure_events[0].event_index if failure_events else None
        first_handoff_idx = handoff_events[0].event_index if handoff_events else None

        # Determine milestone prefix points (use event_index for arithmetic)
        milestone_points = set()
        if first_injection_idx is not None:
            milestone_points.add(first_injection_idx)
            milestone_points.add(min(first_injection_idx + 1, total_events))
        if first_failure_idx is not None:
            milestone_points.add(max(first_failure_idx - 1, 1))
            milestone_points.add(first_failure_idx)
        if first_handoff_idx is not None:
            milestone_points.add(first_handoff_idx)
        milestone_points.add(total_events)  # Always include final

        # Regular sampling
        for i in range(0, total_events, self.sampling_interval):
            milestone_points.add(min(i + 1, total_events))

        # Generate prefix at each milestone
        for end_idx in sorted(milestone_points):
            if end_idx < 1 or end_idx > total_events:
                continue

            prefix_events = [e.to_dict() if hasattr(e, "to_dict") else e
                            for e in events[:end_idx]]

            record = PrefixRecord(
                trace_id=trace.trace_id,
                prefix_end_event=end_idx,
                normalized_progress=round(end_idx / total_events, 3),
                future_downstream_failure=bool(failure_events),
                failure_within_5_events=self._failure_within(end_idx, first_failure_idx, 5, total_events),
                failure_within_10_events=self._failure_within(end_idx, first_failure_idx, 10, total_events),
                time_to_failure=(first_failure_idx - end_idx) if first_failure_idx is not None and first_failure_idx > end_idx else None,
                origin_observed=first_injection_idx is not None and first_injection_idx <= end_idx,
                propagation_observed=any(
                    getattr(e, "propagates_to", None) for e in events[:end_idx]
                ),
                events_in_prefix=end_idx,
                prefix_events=prefix_events,
            )
            records.append(record)

        return records

    def export_prefixes_batch(self, traces: list, prefix: str = "prefixes") -> Path:
        """Export prefixes for multiple traces."""
        csv_path = self.output_dir / f"{prefix}_summary.csv"
        json_path = self.output_dir / f"{prefix}_full.jsonl"

        all_records = []
        for trace in traces:
            records = self.export_prefixes(trace)
            all_records.extend(records)

        # Write CSV summary
        if all_records:
            fieldnames = [
                "trace_id", "prefix_end_event", "normalized_progress",
                "future_downstream_failure", "failure_within_5_events",
                "failure_within_10_events", "time_to_failure",
                "origin_observed", "propagation_observed", "events_in_prefix",
            ]
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                for r in all_records:
                    writer.writerow({
                        "trace_id": r.trace_id,
                        "prefix_end_event": r.prefix_end_event,
                        "normalized_progress": r.normalized_progress,
                        "future_downstream_failure": r.future_downstream_failure,
                        "failure_within_5_events": r.failure_within_5_events,
                        "failure_within_10_events": r.failure_within_10_events,
                        "time_to_failure": r.time_to_failure,
                        "origin_observed": r.origin_observed,
                        "propagation_observed": r.propagation_observed,
                        "events_in_prefix": r.events_in_prefix,
                    })

        # Write full JSONL with prefix events
        with open(json_path, "w", encoding="utf-8") as f:
            for r in all_records:
                f.write(json.dumps({
                    "trace_id": r.trace_id,
                    "prefix_end_event": r.prefix_end_event,
                    "normalized_progress": r.normalized_progress,
                    "future_downstream_failure": r.future_downstream_failure,
                    "failure_within_5_events": r.failure_within_5_events,
                    "failure_within_10_events": r.failure_within_10_events,
                    "time_to_failure": r.time_to_failure,
                    "origin_observed": r.origin_observed,
                    "propagation_observed": r.propagation_observed,
                    "events_in_prefix": r.events_in_prefix,
                    "prefix_events": r.prefix_events,
                }) + "\n")

        logger.info("Exported %d prefix records from %d traces to %s",
                    len(all_records), len(traces), csv_path)
        return csv_path

    @staticmethod
    def _failure_within(current_pos: int, failure_pos: int | None,
                        window: int, total: int) -> bool:
        """Check if a failure occurs within `window` events of current position."""
        if failure_pos is None:
            return False
        return 0 < (failure_pos - current_pos) <= window
