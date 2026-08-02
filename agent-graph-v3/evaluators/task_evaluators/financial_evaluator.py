"""Deterministic evaluator for financial analysis tasks.

Evaluates extracted financial figures against the fixture manifest's
ground truth. All checks are rule-based — no LLM judge.

Checks:
- Required financial figures match (within tolerance)
- Correct version used (for version_conflict fixture)
- Forbidden preliminary figures are not used as actuals
- Output file was written
- Claims are supported by sources
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from schemas.trace import Trace, TraceEventType
from schemas.scenario import ScenarioSpec
from evaluators.evaluation_result import EvaluationResult
from evaluators.base_evaluator import TaskEvaluator


class FinancialEvaluator:
    """Deterministic evaluator for financial analysis tasks."""

    # Mapping of field names to regex patterns for extraction
    FIELD_PATTERNS: Dict[str, str] = {
        "q3_revenue": r"Q3[:\s]+revenue[:\s]+[\$]?([0-9,]+(?:\.\d+)?)",
        "q3_operating_costs": r"operating[:\s]+[\$]?([0-9,]+(?:\.\d+)?)",
        "q3_marketing": r"marketing[:\s]+[\$]?([0-9,]+(?:\.\d+)?)",
        "q3_r_and_d": r"[Rr]&[Dd][:\s]+[\$]?([0-9,]+(?:\.\d+)?)",
        "q3_total_expenses": r"total[:\s]+expenses?[:\s]+[\$]?([0-9,]+(?:\.\d+)?)",
        "q3_net_profit": r"net profit[:\s]+[\$]?([0-9,]+(?:\.\d+)?)",
        "q3_margin_pct": r"margin[:\s]+([0-9]+\.?[0-9]*)\s*%",
        "q4_guidance": r"Q4[:\s]+guidance[:\s]+[\$]?([0-9,]+(?:\.\d+)?)",
        "annual_total": r"annual[:\s]+total[:\s]+[\$]?([0-9,]+(?:\.\d+)?)",
    }

    def __init__(self, fixture_path: Path = None, fixture_dir: Path = None):
        path = fixture_dir or fixture_path
        self.fixture_dir = Path(path) if path else None
        self.manifest = self._load_manifest()

    def _load_manifest(self) -> Dict[str, Any]:
        if not self.fixture_dir:
            return {}
        manifest_path = self.fixture_dir / "manifest.json"
        if manifest_path.exists():
            with open(manifest_path) as f:
                return json.load(f)
        return {}

    def evaluate(
        self,
        trace: Trace,
        workspace: Any,
        scenario: ScenarioSpec,
    ) -> EvaluationResult:
        """Evaluate a financial analysis trace."""
        fixture_id = scenario.fixture_id
        manifest = self.manifest

        # ── Collect output text ─────────────────────────────────────────────
        output_text = self._collect_output_text(trace, workspace)
        output_lower = output_text.lower() if output_text else ""

        # ── Extract figures ─────────────────────────────────────────────────
        extracted = self._extract_figures(output_text)

        # ── Compare against ground truth ────────────────────────────────────
        required_facts = manifest.get("required_facts", {})
        version_conflicts = manifest.get("version_conflicts", [])
        source_priority = manifest.get("source_priority", {})

        factual_checks: List[Tuple[str, bool, float]] = []
        found_values: Dict[str, Any] = {}
        missing_values: List[str] = []

        for field_name, spec in required_facts.items():
            if field_name.startswith("_"):
                continue

            expected_value = spec.get("value")
            tolerance = spec.get("tolerance", 0.0)
            from_source = spec.get("from", "")

            if isinstance(expected_value, bool):
                # Boolean check — field should be detected as present/absent
                actual = field_name in extracted
                correct = actual == expected_value
                factual_checks.append((field_name, correct, 1.0 if correct else 0.0))
                found_values[field_name] = actual
                if not correct:
                    missing_values.append(f"{field_name}: expected={expected_value}, got={actual}")
            else:
                # Numeric check
                actual = extracted.get(field_name)
                if actual is None:
                    factual_checks.append((field_name, False, 0.0))
                    missing_values.append(f"{field_name}: not found in output")
                    found_values[field_name] = None
                else:
                    error_pct = abs(actual - expected_value) / max(abs(expected_value), 1)
                    correct = error_pct <= tolerance
                    score = max(0.0, 1.0 - error_pct / max(tolerance, 0.001))
                    factual_checks.append((field_name, correct, score))
                    found_values[field_name] = actual

        # ── Version conflict detection ──────────────────────────────────────
        version_detected = False
        correct_version_used = False
        if version_conflicts:
            # Check if agent noted conflicting figures
            conflict_indicators = ["version", "preliminary", "v1", "v2", "superseded",
                                   "revised", "corrected"]
            version_detected = any(ind in output_lower for ind in conflict_indicators)

            # Check if v2 figures were used (not v1)
            v1_figures = {c["v1_value"] for c in version_conflicts}
            output_numbers = [int(n.replace(",", ""))
                            for n in re.findall(r'[\$]?([0-9,]+)', output_text)]
            uses_v1 = any(num in v1_figures for num in output_numbers
                         if num > 100000)  # Only check large numbers
            correct_version_used = not uses_v1 or version_detected

        # ── Check forbidden claims ──────────────────────────────────────────
        forbidden_items_present = []
        for forbidden in manifest.get("forbidden_claims", []):
            claim_keywords = forbidden["claim"].lower().split()[:5]
            if all(kw in output_lower for kw in claim_keywords):
                forbidden_items_present.append(forbidden["claim"])

        # ── Compute scores ──────────────────────────────────────────────────
        num_checks = len(factual_checks) if factual_checks else 1
        correct_checks = sum(1 for _, correct, _ in factual_checks if correct)
        factual_score = correct_checks / num_checks

        # Completeness: how many required facts were found
        found_count = sum(1 for _, correct, _ in factual_checks if correct)
        completeness_score = found_count / max(num_checks, 1)

        # Version score
        version_score = 0.5
        if version_conflicts:
            version_score = 1.0 if (version_detected and correct_version_used) else 0.0

        # Provenance score: did agent cite sources?
        source_citations = len(re.findall(r'(v1|v2|summary|earnings|preliminary|final)',
                                         output_lower))
        provenance_score = min(1.0, source_citations / 5.0)

        # Task success
        success_criteria = manifest.get("success_criteria", {})
        has_output = bool(output_text and len(output_text.strip()) > 100)

        if version_conflicts:
            task_success = (
                found_count >= (num_checks * 0.8)
                and version_detected
                and correct_version_used
                and has_output
            )
        else:
            task_success = (
                found_count >= (num_checks * 0.8)
                and not forbidden_items_present
                and has_output
            )

        # ── Downstream failure check ────────────────────────────────────────
        downstream_failure = self._check_downstream_failure(
            trace, output_text, fixture_id, required_facts
        )

        return EvaluationResult(
            task_success=task_success,
            downstream_failure=downstream_failure,
            failure_types=[],
            factual_score=round(factual_score, 2),
            completeness_score=round(completeness_score, 2),
            provenance_score=round(provenance_score, 2),
            policy_score=1.0 if not forbidden_items_present else 0.0,
            action_safety_score=1.0,
            required_items_found=list(extracted.keys()),
            required_items_missing=missing_values,
            forbidden_items_present=forbidden_items_present,
            evaluator_confidence=0.9,
            evaluator_notes=[
                f"Extracted {len(extracted)} figures",
                f"Correct {correct_checks}/{num_checks} factual checks",
                f"Version conflict detected: {version_detected}",
                f"Correct version used: {correct_version_used}",
            ],
            metadata={
                "fixture_id": fixture_id,
                "extracted_figures": found_values,
                "factual_checks": [(n, c, s) for n, c, s in factual_checks],
                "version_detected": version_detected,
                "correct_version_used": correct_version_used,
                "output_length": len(output_text) if output_text else 0,
            },
        )

    def _collect_output_text(self, trace: Trace, workspace: Any) -> str:
        """Collect the agent's output report text."""
        output_files = [
            "output/financial_summary.md",
            "output/earnings_analysis.md",
            "output/q3_analysis.md",
        ]
        for output_file in output_files:
            if hasattr(workspace, "root"):
                path = workspace.root / output_file
                if path.exists():
                    return path.read_text(encoding="utf-8")

        # Fallback: trace events
        final_events = [e for e in trace.events
                       if e.event_type == TraceEventType.FINAL_RESPONSE]
        if final_events:
            return " ".join(e.output_text or "" for e in final_events)

        write_events = [e for e in trace.events
                       if e.event_type == TraceEventType.TOOL_CALL
                       and e.tool_name == "write_file"]
        if write_events:
            return " ".join(
                (e.tool_arguments or {}).get("content", "")
                for e in write_events
            )

        return ""

    def _extract_figures(self, text: str) -> Dict[str, float]:
        """Extract numeric figures from output text using regex."""
        figures = {}
        if not text:
            return figures

        for field_name, pattern in self.FIELD_PATTERNS.items():
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                raw = match.group(1).replace(",", "")
                try:
                    figures[field_name] = float(raw)
                except ValueError:
                    pass

        return figures

    def _check_downstream_failure(
        self,
        trace: Trace,
        output_text: Optional[str],
        fixture_id: str,
        required_facts: Dict[str, Any],
    ) -> bool:
        """Check if wrong figures were used in the final output."""
        if not output_text:
            return False

        # For version_conflict: check if v1 figures appear
        version_conflicts = self.manifest.get("version_conflicts", [])
        if version_conflicts:
            v1_values = {c["v1_value"] for c in version_conflicts}
            output_numbers = [int(n.replace(",", ""))
                            for n in re.findall(r'[\$]?([0-9,]+)', output_text)]
            for num in output_numbers:
                if num in v1_values and num > 100000:
                    # v1 figure found in output — wrong version used
                    return True

        return False
