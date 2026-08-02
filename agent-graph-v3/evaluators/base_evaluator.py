"""Base task evaluator protocol."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol

from schemas.trace import Trace
from schemas.scenario import ScenarioSpec
from evaluators.evaluation_result import EvaluationResult


class TaskEvaluator(Protocol):
    """Protocol for task-specific evaluators."""

    def evaluate(
        self,
        trace: Trace,
        workspace: Any,
        scenario: ScenarioSpec,
    ) -> EvaluationResult:
        """Evaluate a completed trace against task ground truth.

        Args:
            trace: The completed execution trace
            workspace: The task workspace (may contain output files)
            scenario: The scenario specification

        Returns:
            EvaluationResult with all scores and findings
        """
        ...


def get_evaluator(task_family: str, fixture_path: Any = None) -> Any:
    """Return an evaluator for the given task family.

    Args:
        task_family: Task family name (e.g. "code_review")
        fixture_path: Optional path to the task fixture directory

    Returns:
        Evaluator instance or None if no evaluator registered
    """
    from evaluators.task_evaluators import EVALUATOR_REGISTRY
    evaluator_class = EVALUATOR_REGISTRY.get(task_family)
    if evaluator_class is None:
        return None
    # Evaluators that need fixture_dir get it; others are created without args
    try:
        if fixture_path is not None:
            return evaluator_class(fixture_path=fixture_path)
        else:
            return evaluator_class()
    except TypeError:
        # Fallback: try with no args
        return evaluator_class()
