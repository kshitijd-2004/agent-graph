"""Analysis export — exports complete benchmark labels for training and offline analysis.

Contains all labels including:
- Event-level labels
- Edge-level annotations
- Path-level propagation data
- Trace-level outcome labels
- LEP metadata (for supervised auxiliary tasks)
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class AnalysisExporter:
    """Export traces with full benchmark labels for analysis."""

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def export_trace(self, trace: Any, evaluation: Any = None,
                     filename: str | None = None) -> Path:
        """Export a single trace with all labels."""
        if filename is None:
            task_family = trace.metadata.get("task_family", "unknown") if hasattr(trace, "metadata") else "unknown"
            filename = f"{task_family}_{trace.trace_id}.json"

        output_path = self.output_dir / filename

        data = {
            "trace_metadata": self._trace_metadata(trace),
            "events": [self._event_dict(e) for e in trace.events],
            "labels": self._compute_trace_labels(trace, evaluation),
            "lep_instances": self._collect_lep_data(trace),
            "propagation_paths": self._collect_propagation_paths(trace),
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)

        logger.info("Exported analysis trace to %s", output_path)
        return output_path

    def export_traces(self, traces: list, evaluations: Dict[str, Any] | None = None,
                      prefix: str = "analysis") -> Path:
        """Export multiple traces."""
        csv_path = self.output_dir / f"{prefix}_events.csv"
        rows = []

        for trace in traces:
            eval_result = evaluations.get(trace.trace_id) if evaluations else None
            labels = self._compute_trace_labels(trace, eval_result)

            for event in trace.events:
                row = self._event_row(event, trace, labels)
                rows.append(row)

        if rows:
            fieldnames = list(rows[0].keys())
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)

        logger.info("Exported %d analysis events from %d traces to %s",
                    len(rows), len(traces), csv_path)
        return csv_path

    def _trace_metadata(self, trace: Any) -> Dict[str, Any]:
        """Extract trace metadata."""
        return {
            "trace_id": trace.trace_id,
            "execution_id": trace.execution_id,
            "variant": trace.variant.value if hasattr(trace.variant, 'value') else str(trace.variant),
            "num_events": trace.num_events,
            "task_family": trace.metadata.get("task_family", ""),
            "fixture_id": trace.metadata.get("fixture_id", ""),
            "topology": trace.metadata.get("topology", ""),
            "condition": trace.metadata.get("condition", ""),
            "lep_codes": trace.metadata.get("lep_codes", []),
            "runtime_seconds": trace.metadata.get("runtime_seconds", 0),
        }

    def _event_dict(self, event: Any) -> Dict[str, Any]:
        """Full event dict with all labels."""
        if hasattr(event, "to_dict"):
            d = event.to_dict()
        else:
            d = asdict(event) if hasattr(event, "__dataclass_fields__") else dict(event)
        if hasattr(d.get("event_type"), "value"):
            d["event_type"] = d["event_type"].value
        return d

    def _event_row(self, event: Any, trace: Any, labels: Dict) -> Dict[str, Any]:
        """Flatten event + trace labels for CSV export."""
        d = self._event_dict(event)
        d.update({
            "trace_id": trace.trace_id,
            "variant": trace.variant.value if hasattr(trace.variant, 'value') else str(trace.variant),
            "task_family": trace.metadata.get("task_family", ""),
            "topology": trace.metadata.get("topology", ""),
            "condition": trace.metadata.get("condition", ""),
            # Trace-level labels
            "trace_downstream_failure": labels.get("downstream_failure", False),
            "trace_task_success": labels.get("task_success", False),
            "trace_propagation_depth": labels.get("propagation_depth", 0),
            "trace_containment_success": labels.get("containment_success", False),
            "trace_recovery_success": labels.get("recovery_success", False),
        })
        return d

    def _compute_trace_labels(self, trace: Any, evaluation: Any = None) -> Dict[str, Any]:
        """Compute trace-level labels from events and evaluation."""
        labels = {
            "task_success": False,
            "downstream_failure": False,
            "failure_type": "",
            "lep_exposed": False,
            "lep_consumed": False,
            "lep_propagated": False,
            "containment_success": False,
            "recovery_success": False,
            "propagation_depth": 0,
            "propagation_fanout": 0,
            "cross_agent_transfer_count": 0,
            "memory_boundary_count": 0,
            "time_to_first_effect": None,
            "time_to_failure": None,
            "number_of_contributing_leps": 0,
            "is_convergence_scenario": trace.metadata.get("condition", "") == "convergence",
        }

        if evaluation is not None:
            if hasattr(evaluation, "task_success"):
                labels["task_success"] = evaluation.task_success
            if hasattr(evaluation, "downstream_failure"):
                labels["downstream_failure"] = evaluation.downstream_failure
            if hasattr(evaluation, "failure_types"):
                labels["failure_type"] = evaluation.failure_types[0] if evaluation.failure_types else ""

        # Event-level aggregation
        lep_events = [e for e in trace.events if getattr(e, "lep_injected", False)]
        if lep_events:
            labels["lep_exposed"] = True
            labels["number_of_contributing_leps"] = len(set(
                getattr(e, "lep_type", None) for e in lep_events if getattr(e, "lep_type", None)
            ))

        failure_events = [e for e in trace.events if getattr(e, "downstream_failure", False)]
        if failure_events:
            labels["downstream_failure"] = True
            labels["time_to_failure"] = failure_events[0].event_index

        # Propagation metrics
        handoffs = [e for e in trace.events if e.event_type.value == "agent_handoff"]
        labels["cross_agent_transfer_count"] = len(handoffs)

        memory_events = [e for e in trace.events
                         if e.event_type.value in ("memory_write", "memory_retrieval")]
        labels["memory_boundary_count"] = len(memory_events)

        return labels

    def _collect_lep_data(self, trace: Any) -> List[Dict[str, Any]]:
        """Collect LEP instance data from trace metadata."""
        lep_data = []
        lep_codes = trace.metadata.get("lep_codes", [])
        for code in lep_codes:
            lep_data.append({
                "lep_code": code,
                "trace_id": trace.trace_id,
                "variant": trace.variant.value if hasattr(trace.variant, 'value') else str(trace.variant),
            })
        return lep_data

    def _collect_propagation_paths(self, trace: Any) -> List[Dict[str, Any]]:
        """Collect propagation paths from trace events."""
        paths = []
        lep_events = [e for e in trace.events if getattr(e, "lep_injected", False)]
        if not lep_events:
            return paths

        # Build simple propagation path from first LEP event to first failure
        origin = lep_events[0]
        failure_events = [e for e in trace.events if getattr(e, "downstream_failure", False)]

        if failure_events:
            path_events = [e.event_index for e in trace.events
                           if origin.event_index <= e.event_index
                           and e.event_index <= failure_events[0].event_index]
            paths.append({
                "path_id": f"path_{trace.trace_id}_{origin.event_index}",
                "origin_event_id": origin.event_id,
                "terminal_event_id": failure_events[0].event_id,
                "event_indices": path_events,
                "event_ids": [e.event_id for e in trace.events
                              if origin.event_index <= e.event_index
                              and e.event_index <= failure_events[0].event_index],
                "path_length": len(path_events),
                "recovered": False,
            })

        return paths
