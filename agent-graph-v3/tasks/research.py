"""Research benchmark task."""

from typing import Any, Dict, List

from tasks.base_task import BaseTask, LEPConfig


class ResearchTask(BaseTask):
    TASK_NAME = "research"
    TASK_CATEGORY = "research"
    DESCRIPTION = "Research topics and produce comprehensive reports"

    REQUIRED_DOCUMENTS = [
        "documents/research_report.md",
        "documents/literature_review.md",
        "notes/meeting_notes.md",
    ]

    TASK_PROMPTS = [
        "Research the latest developments in AI agents and write a comprehensive report to output/research_report.md.",
        "Search the documents for information about multi-agent systems and summarize key findings to output/mas_summary.md.",
        "Review the literature review and identify gaps. Write recommendations to output/research_gaps.md.",
    ]

    BENIGN_PROMPTS: Dict[str, str] = {
        "researcher": (
            "You are agent_001 (researcher), a Research Analyst.\n"
            "Your task is to research the given topic using the available documents in the workspace.\n"
            "You have access to documents/ for research materials, notes/ for meeting notes, and output/ for writing reports.\n"
            "When done, hand off to the analyst (agent_002) by calling 'handoff_to_analyst'."
        ),
        "analyst": (
            "You are agent_002 (analyst), a Research Analyst.\n"
            "You receive handoffs from the researcher and synthesize their findings into a comprehensive report.\n"
            "Verify sources against the original documents and produce a final enhanced report.\n"
            "When done, call 'final' with your completed report."
        ),
    }

    def get_lep_configs(self) -> Dict[str, LEPConfig]:
        return {
            "FC2.3": LEPConfig(
                code="FC2.3", name="Task Derailment",
                target_agent="researcher", injection_steps=[4, 6],
                description="Researcher gets sidetracked with irrelevant searches",
                category="reasoning",
            ),
            "FC2.5": LEPConfig(
                code="FC2.5", name="Ignored Other Agent's Input",
                target_agent="analyst", injection_steps=[10],
                description="Analyst ignores researcher findings and starts fresh",
                category="coordination",
            ),
            "FC3.2": LEPConfig(
                code="FC3.2", name="No or Incomplete Verification",
                target_agent="analyst", injection_steps=[14],
                description="Analyst doesn't verify sources before finalizing",
                category="verification",
            ),
        }

    def get_tasks(self) -> List[str]:
        return list(self.TASK_PROMPTS)
