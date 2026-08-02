"""Evaluation result type for task evaluators."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class EvaluationResult:
    """Result of evaluating a completed trace against task ground truth.

    Attributes:
        task_success:              Whether the task was completed successfully
        downstream_failure:        Whether a downstream failure occurred
        failure_types:             Types of failures detected

        factual_score:             Accuracy of factual claims (0.0-1.0)
        completeness_score:        How completely requirements were met (0.0-1.0)
        provenance_score:          How well sources were attributed (0.0-1.0)
        policy_score:              Adherence to policy/constraints (0.0-1.0)
        action_safety_score:       Safety of any actions taken (0.0-1.0)

        required_items_found:      Required items that were found
        required_items_missing:    Required items that were missing
        forbidden_items_present:   Forbidden items that appeared

        sensitive_action_taken:    Whether a sensitive action was taken
        sensitive_action_authorized: Whether it was properly authorized

        evaluator_confidence:      Confidence in this evaluation (0.0-1.0)
        evaluator_notes:           Free-text notes for debugging
        metadata:                  Additional fields

        # Runner-consumed fields
        passed:                    Evaluator pass/fail flag (defaults to task_success)
        errors:                    List of error descriptions
    """
    task_success: bool = False
    downstream_failure: bool = False
    failure_types: List[str] = field(default_factory=list)

    factual_score: float = 0.0
    completeness_score: float = 0.0
    provenance_score: float = 0.0
    policy_score: float = 0.0
    action_safety_score: float = 0.0

    required_items_found: List[str] = field(default_factory=list)
    required_items_missing: List[str] = field(default_factory=list)
    forbidden_items_present: List[str] = field(default_factory=list)

    sensitive_action_taken: bool = False
    sensitive_action_authorized: bool = False

    evaluator_confidence: float = 0.0
    evaluator_notes: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Runner-consumed fields
    passed: bool = True
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

