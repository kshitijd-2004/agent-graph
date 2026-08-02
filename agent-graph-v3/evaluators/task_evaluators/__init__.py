"""Task evaluators for agent-graph-v3."""

from evaluators.base_evaluator import TaskEvaluator, get_evaluator
from evaluators.task_evaluators.code_review_evaluator import CodeReviewEvaluator
from evaluators.task_evaluators.financial_evaluator import FinancialEvaluator
from evaluators.task_evaluators.research_evaluator import ResearchSynthesisEvaluator

# Registry of evaluator constructors
EVALUATOR_REGISTRY = {
    "code_review": CodeReviewEvaluator,
    "financial_analysis": FinancialEvaluator,
    "research_synthesis": ResearchSynthesisEvaluator,
}

__all__ = [
    "TaskEvaluator",
    "get_evaluator",
    "CodeReviewEvaluator",
    "FinancialEvaluator",
    "ResearchSynthesisEvaluator",
    "EVALUATOR_REGISTRY",
]
