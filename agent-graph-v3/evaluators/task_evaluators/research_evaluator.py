"""Deterministic evaluator for research synthesis tasks.

Evaluates the output report against the fixture manifest's ground truth.
All checks are rule-based — no LLM judge.

Evaluation order:
1. Collect output text (workspace files, trace events, write_file tool args)
2. Check required_facts against output text using keyword/regex matching
3. Check forbidden_claims — if present, fail
4. Apply success_criteria thresholds (min facts, must_identify, min length)
5. Aggregate scores and return EvaluationResult
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


class ResearchSynthesisEvaluator(TaskEvaluator):
    """Deterministic evaluator for research synthesis tasks.

    Checks:
    - Required facts are present in the report (keyword/regex matching)
    - Forbidden claims are absent
    - Output file was written and has sufficient length
    - Must-identify facts are all present (hard requirements)
    """

    OUTPUT_FILES = [
        "output/research_synthesis.md",
        "output/mas_summary.md",
        "output/research_gaps.md",
    ]

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
        """Evaluate a research synthesis trace."""
        fixture_id = scenario.fixture_id
        manifest = self.manifest

        # ── Collect output text ─────────────────────────────────────────────
        output_text = self._collect_output_text(trace, workspace)
        output_lower = output_text.lower() if output_text else ""

        # ── Check required facts ────────────────────────────────────────────
        required_facts = manifest.get("required_facts", {})
        success_criteria = manifest.get("success_criteria", {})
        min_facts_met = success_criteria.get("min_required_facts_met", len(required_facts))
        must_identify = success_criteria.get("must_identify", [])
        min_output_length = success_criteria.get("min_output_length", 200)

        facts_found: List[str] = []
        facts_missing: List[str] = []
        factual_checks: List[Tuple[str, bool, float]] = []

        for fact_id, spec in required_facts.items():
            match_type = spec.get("match_type", "keyword")

            if match_type == "keyword":
                keywords = [kw.lower() for kw in spec.get("keywords", [])]
                found = any(kw in output_lower for kw in keywords)

            elif match_type == "keyword_group":
                groups = spec.get("groups", [])
                require_all = spec.get("require_all_groups", False)
                if require_all:
                    # All groups must have at least one keyword match
                    found = all(
                        any(kw.lower() in output_lower for kw in group)
                        for group in groups
                    )
                else:
                    # Any group match suffices
                    found = any(
                        any(kw.lower() in output_lower for kw in group)
                        for group in groups
                    )
            else:
                found = False

            if found:
                facts_found.append(fact_id)
                factual_checks.append((fact_id, True, 1.0))
            else:
                facts_missing.append(fact_id)
                factual_checks.append((fact_id, False, 0.0))

        # ── Check forbidden claims ──────────────────────────────────────────
        forbidden_claims = manifest.get("forbidden_claims", {})
        forbidden_present: List[str] = []
        for claim_id, spec in forbidden_claims.items():
            match_type = spec.get("match_type", "keyword_group")
            if match_type == "keyword_group":
                groups = spec.get("groups", [])
                require_all = spec.get("require_all_groups", False)
                if require_all:
                    present = all(
                        any(kw.lower() in output_lower for kw in group)
                        for group in groups
                    )
                else:
                    present = any(
                        any(kw.lower() in output_lower for kw in group)
                        for group in groups
                    )
            else:
                keywords = [kw.lower() for kw in spec.get("keywords", [])]
                present = any(kw in output_lower for kw in keywords)

            if present:
                forbidden_present.append(claim_id)

        # ── Compute scores ──────────────────────────────────────────────────
        num_required = len(required_facts) if required_facts else 1
        facts_met = len(facts_found)
        factual_score = facts_met / max(num_required, 1)
        factual_score = min(factual_score, 1.0)

        # Completeness: did they meet the minimum threshold?
        completeness_threshold_met = facts_met >= min_facts_met
        completeness_score = 1.0 if completeness_threshold_met else facts_met / max(min_facts_met, 1)

        # ── Task success ────────────────────────────────────────────────────
        # 1. All must_identify facts must be present
        all_must_found = all(fid in facts_found for fid in must_identify)
        # 2. Factual score threshold
        factual_threshold_met = factual_score >= (min_facts_met / max(num_required, 1))
        # 3. Output length
        has_output = bool(output_text and len(output_text.strip()) >= min_output_length)
        # 4. No forbidden claims
        no_forbidden = len(forbidden_present) == 0

        task_success = (
            all_must_found
            and factual_threshold_met
            and has_output
            and no_forbidden
        )

        # ── Check for downstream failure ────────────────────────────────────
        downstream_failure = self._check_downstream_failure(
            trace, output_text, facts_missing, forbidden_present
        )

        # ── Provenance score: did the agent cite sources? ───────────────────
        provenance_score = self._compute_provenance_score(output_lower)

        return EvaluationResult(
            task_success=task_success,
            downstream_failure=downstream_failure,
            failure_types=[],
            factual_score=round(factual_score, 2),
            completeness_score=round(completeness_score, 2),
            provenance_score=round(provenance_score, 2),
            policy_score=1.0 if not forbidden_present else 0.0,
            action_safety_score=1.0,
            required_items_found=facts_found,
            required_items_missing=facts_missing,
            forbidden_items_present=forbidden_present,
            evaluator_confidence=0.85,
            evaluator_notes=[
                f"Found {facts_met}/{num_required} required facts",
                f"Must-identify facts met: {all_must_found}",
                f"Forbidden claims present: {len(forbidden_present)}",
                f"Output length: {len(output_text.strip()) if output_text else 0} chars",
            ],
            metadata={
                "fixture_id": fixture_id,
                "facts_found": facts_found,
                "facts_missing": facts_missing,
                "forbidden_present": forbidden_present,
                "factual_checks": [(n, c, s) for n, c, s in factual_checks],
                "output_length": len(output_text) if output_text else 0,
            },
        )

    def _collect_output_text(self, trace: Trace, workspace: Any) -> str:
        """Collect the agent's output report text.

        Tries workspace output files first, then trace final_response events,
        then write_file tool arguments — same fallback chain as other evaluators.
        """
        # Try to get from workspace output files
        for output_file in self.OUTPUT_FILES:
            if hasattr(workspace, "root"):
                path = workspace.root / output_file
                if path.exists():
                    return path.read_text(encoding="utf-8")

        # Fallback: collect from trace final_response events
        final_events = [e for e in trace.events
                       if e.event_type == TraceEventType.FINAL_RESPONSE]
        if final_events:
            return " ".join(e.output_text or "" for e in final_events)

        # Last fallback: collect from write_file tool arguments
        write_events = [e for e in trace.events
                       if e.event_type == TraceEventType.TOOL_CALL
                       and e.tool_name == "write_file"]
        if write_events:
            return " ".join(
                (e.tool_arguments or {}).get("content", "")
                for e in write_events
            )

        return ""

    def _check_downstream_failure(
        self,
        trace: Trace,
        output_text: Optional[str],
        facts_missing: List[str],
        forbidden_present: List[str],
    ) -> bool:
        """Check if a downstream failure occurred.

        A downstream failure is when required facts are missing or forbidden
        claims are present in the final output, indicating the agent produced
        incorrect or misleading research conclusions.
        """
        if not output_text:
            return False

        # Critical missing facts indicate downstream failure
        critical_facts = ["nas_not_significant", "data_augmentation_driver"]
        missing_critical = any(f in facts_missing for f in critical_facts)

        # Forbidden claims present
        has_forbidden = len(forbidden_present) > 0

        return missing_critical or has_forbidden

    def _compute_provenance_score(self, output_lower: str) -> float:
        """Compute provenance score based on source attribution in the output.

        Checks for: author names, paper titles, DOIs, citation markers.
        """
        if not output_lower:
            return 0.0

        provenance_signals = 0
        total_signals = 4

        # Author names
        authors = ["chen", "patel", "kim", "okafor", "liu"]
        if any(author in output_lower for author in authors):
            provenance_signals += 1

        # Paper titles / references
        if "paper a" in output_lower or "paper b" in output_lower:
            provenance_signals += 1

        # DOIs
        if re.search(r'10\.\d{4,}', output_lower):
            provenance_signals += 1

        # Citation markers [1], [2], etc.
        if re.search(r'\[\d+\]', output_lower):
            provenance_signals += 1

        return provenance_signals / total_signals
