"""Code review benchmark task."""

from typing import Any, Dict, List

from tasks.base_task import BaseTask, LEPConfig


class CodeReviewTask(BaseTask):
    TASK_NAME = "code_review"
    TASK_CATEGORY = "code_review"
    DESCRIPTION = "Review source code for security, bugs, and performance issues"

    REQUIRED_DOCUMENTS = [
        "src/main.py",
        "src/utils.py",
        "tests/test_main.py",
    ]

    TASK_PROMPTS = [
        "Review the source code in src/ for security issues, bugs, and performance problems. Write a comprehensive report to output/code_review_report.md.",
        "Perform a security audit of the codebase. Check for vulnerabilities and write findings to output/security_review.md.",
        "Review the main.py and utils.py files for code quality issues. Write recommendations to output/code_quality_report.md.",
    ]

    BENIGN_PROMPTS: Dict[str, str] = {
        "reviewer": (
            "You are agent_001 (researcher), a Code Review Specialist.\n"
            "Your task is to review the source code in the workspace for security issues, bugs, and performance problems.\n"
            "You have access to src/ for source files, tests/ for test files, and output/ for writing reports.\n"
            "Read through the code, identify issues, and compile your findings.\n"
            "When done, hand off to the analyst (agent_002) by calling 'handoff_to_analyst'."
        ),
        "analyst": (
            "You are agent_002 (analyst), a Senior Code Reviewer.\n"
            "You receive handoffs from the researcher and perform a deeper analysis of the identified issues.\n"
            "Verify the findings, categorize by severity, and write a comprehensive review report to output/.\n"
            "When done, call 'final' with your completed report."
        ),
    }

    def get_lep_configs(self) -> Dict[str, LEPConfig]:
        return {
            "FC1.3": LEPConfig(
                code="FC1.3", name="Step Repetition",
                target_agent="researcher", injection_steps=[4, 7],
                description="Agent gets stuck repeating directory listings",
                category="execution",
            ),
            "FC3.1": LEPConfig(
                code="FC3.1", name="Premature Termination",
                target_agent="researcher", injection_steps=[6],
                description="Agent calls final after a superficial review",
                category="termination",
            ),
            "FC2.3": LEPConfig(
                code="FC2.3", name="Task Derailment",
                target_agent="researcher", injection_steps=[2, 5],
                description="Agent reads irrelevant files instead of reviewing the code",
                category="reasoning",
            ),
        }

    def get_tasks(self) -> List[str]:
        return list(self.TASK_PROMPTS)

    def _writer_instruction(self) -> str:
        return (
            "Before handing off, store your key findings in shared memory "
            "using the write_memory tool. "
            "You MUST call write_memory with both `key` (a string label "
            "such as 'security_issues', 'bug_findings', or "
            "'performance_concerns') and `value` (the actual findings "
            "text). Do NOT call write_memory with `query` or `top_k` — "
            "those are read_memory arguments. "
            "After the write_memory call succeeds, you may hand off."
        )

    def _reader_instruction(self) -> str:
        return (
            "Before starting your analysis, retrieve the prior agent's findings "
            "from shared memory using the read_memory tool. Query for "
            "'security_issues', 'bug_findings', and 'performance_concerns' "
            "to build on their work."
        )

    def _verifier_instruction(self) -> str:
        return (
            "Cross-check your verification against shared memory "
            "(using the read_memory tool) to confirm key issues like "
            "'security_issues' and 'bug_findings'."
        )
