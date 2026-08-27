"""Financial analysis benchmark task."""

from typing import Any, Dict, List

from tasks.base_task import BaseTask, LEPConfig


class FinancialTask(BaseTask):
    TASK_NAME = "financial_analysis"
    TASK_CATEGORY = "financial"
    DESCRIPTION = "Analyze financial documents and produce reports"

    REQUIRED_DOCUMENTS = [
        "documents/financial_report.md",
        "documents/quarterly_earnings.md",
        "notes/meeting_notes.md",
    ]

    TASK_PROMPTS = [
        "Analyze the Q3 financial data and write a summary report to output/financial_summary.md.",
        "Review the earnings report and identify key trends. Write your findings to output/earnings_analysis.md.",
        "Compare quarterly metrics and write a financial analysis report to output/q3_analysis.md.",
    ]

    BENIGN_PROMPTS: Dict[str, str] = {
        "researcher": (
            "You are agent_001 (researcher), a Senior Research Analyst.\n"
            "Your task is to analyze the financial documents in the workspace and produce a summary report.\n"
            "You have access to documents/ for financial data, notes/ for meeting notes, and output/ for writing reports.\n"
            "When done, hand off to the analyst (agent_002) by calling 'handoff_to_analyst'."
        ),
        "analyst": (
            "You are agent_002 (analyst), a Financial Data Analyst.\n"
            "You receive handoffs from the researcher and perform deeper analysis on the financial data.\n"
            "Write your findings to output/.\n"
            "When done, call 'final' with your completed analysis."
        ),
    }

    def get_lep_configs(self) -> Dict[str, LEPConfig]:
        return {
            "FC1.3": LEPConfig(
                code="FC1.3", name="Step Repetition",
                target_agent="researcher", injection_steps=[4, 7],
                description="Agent gets stuck repeating list_directory calls",
                category="execution",
            ),
            "FC3.1": LEPConfig(
                code="FC3.1", name="Premature Termination",
                target_agent="researcher", injection_steps=[6],
                description="Agent calls final before completing the analysis",
                category="termination",
            ),
            "FC2.3": LEPConfig(
                code="FC2.3", name="Task Derailment",
                target_agent="researcher", injection_steps=[2, 5],
                description="Agent gets sidetracked by irrelevant documents",
                category="reasoning",
            ),
        }

    def get_tasks(self) -> List[str]:
        return list(self.TASK_PROMPTS)

    def _writer_instruction(self) -> str:
        return (
            "\n\nStore your key financial findings in shared memory "
            "(using the write_memory tool) before handing off. "
            "Use keys like 'revenue_figures', 'margin_analysis', and 'key_trends' "
            "so the reader can retrieve them."
        )

    def _reader_instruction(self) -> str:
        return (
            "\n\nRetrieve the prior agent's findings from shared memory "
            "(using the read_memory tool) before starting your analysis. "
            "Query for 'revenue_figures', 'margin_analysis', and 'key_trends'."
        )

    def _verifier_instruction(self) -> str:
        return (
            "\n\nCross-check your verification against shared memory "
            "(using the read_memory tool) to confirm key figures like "
            "'revenue_figures' and 'margin_analysis'."
        )
