"""Observable export — exports only information available to a runtime detector.

Excludes:
- lep_id
- injection flags
- downstream labels
- causal ground truth
- hidden pre-perturbation content
- evaluator results

Also performs leakage detection to ensure no hidden fields leak through.
"""

from __future__ import annotations

import csv
import json
import logging
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ObservableExporter:
    """Export traces in detector-observable format.

    Produces JSONL files where each event contains only fields
    that a runtime detector could observe during execution.

    Runs leakage checks to ensure no forbidden content appears in the
    event data (filenames are exempt — they use internal naming conventions).
    """

    EXCLUDED_FIELDS = {
        "hidden_benchmark_metadata",
        "hidden",
        "lep_id",
        "lep_instance_id",
        "injection_event",
        "lep_consumed",
        "lep_transformed",
        "local_abnormality_label",
        "downstream_failure_label",
        "failure_type",
        "observable_features",
        "caused_by_event",
        "propagates_from",
        "propagates_to",
    }

    # Forbidden keys that must never appear in exported event dicts
    FORBIDDEN_KEYS = {
        "lep_code", "lep_injected", "condition", "counterfactual",
        "downstream_failure", "causal_path", "evaluator",
        "hidden", "benchmark_metadata", "original_value",
        "unmodified", "lep_transformed", "lep_consumed",
        "injection_event", "local_abnormality_label",
        "propagates_from", "propagates_to", "caused_by_event",
        "observable_features",
    }

    # Forbidden substrings in event DATA values (not IDs/filenames)
    # These indicate hidden/benchmark-specific content leaked into observable data
    FORBIDDEN_SUBSTRINGS = [
        "LEP_",           # Direct LEP code references in values
        "counterfactual", # Scenario condition labels
        "downstream_failure", # Evaluation labels
        "causal_path",    # Causal annotation data
        "evaluator_",     # Evaluator conclusions
        "benchmark_",     # Benchmark metadata
        "original_value", # Pre-perturbation values
        "unmodified",     # Pre-perturbation content
        "lep_consumed",   # LEP tracking flags
        "lep_transformed", # LEP tracking flags
        "injection_event", # LEP tracking flags
    ]

    # Filename-only patterns (OK if they appear in trace_id, event_id, etc.)
    FILENAME_ONLY = {"benign", "malignant"}

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.leakage_findings: List[Dict[str, Any]] = []

    def check_event_leakage(self, event_dict: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Check a single event dict for leakage of hidden fields.

        Skips ID fields (trace_id, event_id, etc.) which legitimately contain
        internal naming conventions.
        """
        findings = []
        id_fields = {"trace_id", "event_id", "execution_id", "source_entity_id",
                     "target_entity_id", "agent_id"}

        # Check for forbidden keys
        for key in event_dict:
            if key in self.FORBIDDEN_KEYS:
                findings.append({
                    "type": "forbidden_key",
                    "key": key,
                    "value_preview": str(event_dict[key])[:100],
                })

        # Check string values for forbidden substrings (skip ID fields)
        for key, value in event_dict.items():
            if key in id_fields:
                continue
            if not isinstance(value, str):
                continue
            for substr in self.FORBIDDEN_SUBSTRINGS:
                if substr in value:
                    findings.append({
                        "type": "forbidden_substring",
                        "key": key,
                        "substring": substr,
                        "value_preview": value[:100],
                    })

        return findings

    def check_filename_leakage(self, filename: str) -> List[Dict[str, Any]]:
        """Check if filename contains forbidden terms.

        Filenames are exempt from the 'benign/malignant' rule since those
        are standard internal naming conventions. We only flag if they contain
        LEP-specific or evaluator-specific content.
        """
        findings = []
        lower_fname = filename.lower()
        # Only flag LEP/evaluator-specific terms in filenames, not benign/malignant
        forbidden_in_fnames = ["lep_", "injection", "evaluator", "causal",
                                "counterfactual", "downstream_failure"]
        for term in forbidden_in_fnames:
            if term in lower_fname:
                findings.append({
                    "type": "filename_leakage",
                    "term": term,
                    "filename": filename,
                })
        return findings

    def export_trace(self, trace: Any, filename: str | None = None) -> Path:
        """Export a single trace in observable format."""
        if filename is None:
            task_family = trace.metadata.get("task_family", "unknown") if hasattr(trace, "metadata") else "unknown"
            filename = f"{task_family}_{trace.trace_id}.jsonl"

        output_path = self.output_dir / filename

        # Check filename for leakage
        self.leakage_findings.extend(self.check_filename_leakage(filename))

        events = []
        for event in trace.events:
            event_dict = self._sanitize_event(event)

            # Leakage check
            findings = self.check_event_leakage(event_dict)
            self.leakage_findings.extend(findings)

            events.append(event_dict)

        with open(output_path, "w", encoding="utf-8") as f:
            for evt in events:
                f.write(json.dumps(evt) + "\n")

        logger.info("Exported observable trace to %s (%d events, %d leakage findings)",
                    output_path, len(events), len(self.leakage_findings))
        return output_path

    def export_traces(self, traces: list, prefix: str = "observable") -> Path:
        """Export multiple traces."""
        output_path = self.output_dir / f"{prefix}_batch.jsonl"
        all_events = []

        for trace in traces:
            trace_meta = {
                "trace_id": trace.trace_id,
                "execution_id": trace.execution_id,
                "variant": trace.variant.value if hasattr(trace.variant, 'value') else str(trace.variant),
                "task_family": trace.metadata.get("task_family", ""),
                "fixture_id": trace.metadata.get("fixture_id", ""),
                "topology": trace.metadata.get("topology", ""),
                "condition": trace.metadata.get("condition", ""),
            }
            for event in trace.events:
                evt = self._sanitize_event(event)
                evt.update(trace_meta)
                findings = self.check_event_leakage(evt)
                self.leakage_findings.extend(findings)
                all_events.append(evt)

        with open(output_path, "w", encoding="utf-8") as f:
            for evt in all_events:
                f.write(json.dumps(evt) + "\n")

        logger.info("Exported %d observable events from %d traces to %s",
                    len(all_events), len(traces), output_path)
        return output_path

    def _sanitize_event(self, event: Any) -> Dict[str, Any]:
        """Remove hidden fields from an event using explicit observable serialization.

        Uses event.to_observable_dict() when available, which provides a
        structural guarantee that hidden fields are never included.
        Falls back to asdict + field removal for backward compatibility.
        """
        if hasattr(event, "to_observable_dict"):
            return event.to_observable_dict()
        if hasattr(event, "to_dict"):
            d = event.to_dict()
        else:
            d = asdict(event) if hasattr(event, "__dataclass_fields__") else dict(event)

        # Remove excluded fields (backward-compat fallback)
        for field_name in self.EXCLUDED_FIELDS:
            d.pop(field_name, None)

        # Remove event_labels (internal annotation, not detector-visible)
        d.pop("event_labels", None)

        # Convert enum values to strings
        if "event_type" in d and hasattr(d["event_type"], "value"):
            d["event_type"] = d["event_type"].value
        if "variant" in d and hasattr(d["variant"], "value"):
            d["variant"] = d["variant"].value

        # Remove None values for cleanliness
        d = {k: v for k, v in d.items() if v is not None}

        return d

    def assert_no_forbidden_keys(self, obj: Any) -> None:
        """Recursively assert that no forbidden keys appear at any depth.

        Raises AssertionError on the first forbidden key found.
        Use this in tests to guarantee leakage-free exports.
        """
        if isinstance(obj, dict):
            for key, value in obj.items():
                assert key not in self.FORBIDDEN_KEYS, \
                    f"Forbidden key {key!r} found in exported data: {str(value)[:80]}"
                self.assert_no_forbidden_keys(value)
        elif isinstance(obj, list):
            for item in obj:
                self.assert_no_forbidden_keys(item)

    def get_leakage_summary(self) -> Dict[str, Any]:
        """Return summary of all leakage findings."""
        by_type: Dict[str, int] = {}
        for f in self.leakage_findings:
            t = f.get("type", "unknown")
            by_type[t] = by_type.get(t, 0) + 1
        return {
            "total_findings": len(self.leakage_findings),
            "by_type": by_type,
            "findings": self.leakage_findings[:20],  # cap for readability
        }
