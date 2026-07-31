"""AgentGraphs Benchmark Suite.

Provides benchmark tasks for generating agent traces with known LEP injections.
Each task defines benign and malicious system prompts that genuinely change
agent behavior — not annotation-based labeling.
"""

from benchmarks.benchmark import (
    BenchmarkSuite,
    BenchmarkTask,
    DocumentProvider,
    MockLLMBackend,
    TaskCategory,
    TaskResult,
    TraceConfig,
)
from benchmarks.tasks import (
    CodeReviewTask,
    CompetitiveIntelligenceTask,
    FinancialTask,
    ResearchTask,
)

__all__ = [
    "BenchmarkSuite",
    "BenchmarkTask",
    "DocumentProvider",
    "MockLLMBackend",
    "TaskCategory",
    "TaskResult",
    "TraceConfig",
    "FinancialTask",
    "CodeReviewTask",
    "ResearchTask",
    "CompetitiveIntelligenceTask",
]
