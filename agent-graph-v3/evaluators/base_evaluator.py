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
