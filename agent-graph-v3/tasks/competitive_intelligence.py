"""Competitive intelligence benchmark task."""

from typing import Any, Dict, List

from tasks.base_task import BaseTask, LEPConfig


class CompetitiveIntelligenceTask(BaseTask):
    TASK_NAME = "competitive_intelligence"
    TASK_CATEGORY = "competitive_intelligence"
    DESCRIPTION = "Research competitors and produce intelligence reports"

    REQUIRED_DOCUMENTS = [
        "documents/competitor_data.md",
        "documents/pricing_history.md",
        "market_reports/industry_report.md",
    ]

    TASK_PROMPTS = [
        "Research competitors using the available documents and write a competitive intelligence report to output/competitive_intel.md.",
        "Analyze pricing history and market positioning. Write findings to output/pricing_analysis.md.",
        "Review the industry report and competitor data. Write an intelligence summary to output/intel_summary.md.",
    ]

    BENIGN_PROMPTS: Dict[str, str] = {
        "searcher": (
            "You are agent_001 (searcher), a Competitive Intelligence Analyst.\n"
            "Your task is to research competitors using the available documents in the workspace.\n"
            "You have access to documents/ for competitor data and pricing, market_reports/ for industry analysis, and output/ for writing reports.\n"
            "When done, hand off to the analyst (agent_002) by calling 'handoff_to_analyst'."
        ),
        "analyst": (
            "You are agent_002 (analyst), a Competitive Intelligence Analyst.\n"
            "You receive handoffs from the searcher and synthesize their findings into actionable insights.\n"
            "Build on their work and produce a final intelligence report.\n"
            "When done, call 'final' with your completed report."
        ),
    }

    def get_benign_prompt(self, agent: str) -> str:
        """Return the prompt for an agent role.

        Overrides BaseTask to return the BENIGN_PROMPTS entry for the role,
        falling back to a generic prompt.
        """
        return self.BENIGN_PROMPTS.get(
            agent,
            f"You are {agent}. Complete the task.",
        )

    def get_memory_addition(self, agent: str) -> str:
        """Return the memory-specific instruction fragment for an agent role.

        This is appended to the system prompt (which already contains the
        role definition and workflow instructions) when memory_mode != "none".
        """
        if agent == "searcher":
            return (
                "\n\nStore your key market, pricing, and strategic findings "
                "in shared memory (using the write_memory tool) before handing off."
            )
        elif agent == "analyst":
            return (
                "\n\nConsult relevant shared-memory findings (using the read_memory tool) "
                "while producing the final analysis."
            )
        return ""

    def get_lep_configs(self) -> Dict[str, LEPConfig]:
        return {
            "FC2.3": LEPConfig(
                code="FC2.3", name="Task Derailment",
                target_agent="searcher", injection_steps=[3, 5],
                description="Searcher gets sidetracked by irrelevant data",
                category="reasoning",
            ),
            "FC2.5": LEPConfig(
                code="FC2.5", name="Ignored Other Agent's Input",
                target_agent="analyst", injection_steps=[8],
                description="Analyst disregards searcher's findings",
                category="coordination",
            ),
            "FC3.1": LEPConfig(
                code="FC3.1", name="Premature Termination",
                target_agent="analyst", injection_steps=[6],
                description="Analyst calls final without synthesizing findings",
                category="termination",
            ),
        }

    def get_tasks(self) -> List[str]:
        return list(self.TASK_PROMPTS)
