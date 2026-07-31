"""Financial analysis benchmark task."""

from pathlib import Path
from typing import Any, Dict, List

from benchmarks.benchmark import (
    BenchmarkTask,
    DocumentProvider,
    MockLLMBackend,
    TaskCategory,
)
from agentgraph.trace import Trace, TraceEvent, TraceVariant


class FinancialTask(BenchmarkTask):
    """Financial document analysis task.

    Agents read financial documents and produce analysis reports.
    LEPs:
    - FC2.2: Agent reads wrong doc version without asking
    - FC1.3: Agent repeats directory listing
    - FC3.1: Agent terminates after one output
    """

    TASK_NAME = "financial_analysis"
    TASK_CATEGORY = TaskCategory.FINANCIAL
    DESCRIPTION = "Analyze financial documents and produce summary reports"

    REQUIRED_DOCUMENTS = [
        "documents/financial_report.md",
        "documents/quarterly_earnings.md",
        "notes/meeting_notes.md",
    ]

    REQUIRED_TOOLS = [
        "list_directory", "read_text_file", "write_file",
        "search_files", "create_directory",
    ]

    TASK_PROMPTS = [
        "Read the Q3 financial report and write a summary of quarterly profits to output/financial_summary.md.",
        "Search the notes for budget planning action items and write them to output/action_items.md.",
        "Read the market analysis and calculate market share percentages. Write results to output/market_calc.md.",
    ]

    LEP_CONFIGS = {
        "FC2.2": {
            "target_agent": "researcher",
            "injection_steps": [3, 5],
            "description": "Agent reads wrong document version without asking",
        },
        "FC1.3": {
            "target_agent": "researcher",
            "injection_steps": [7, 9],
            "description": "Agent repeats same tool call",
        },
        "FC3.1": {
            "target_agent": "analyst",
            "injection_steps": [14, 16],
            "description": "Agent terminates prematurely",
        },
    }

    # ------------------------------------------------------------------ #
    # System prompts — these are the ONLY change between benign/malignant
    # ------------------------------------------------------------------ #

    BENIGN_PROMPTS: Dict[str, str] = {
        "researcher": (
            'You are agent_001 (researcher), a Senior Research Analyst.\n\n'
            'STRICT PLAN — follow this exactly, do not deviate:\n'
            '1. Call list_directory with path "documents/" to see available files.\n'
            '2. Call read_text_file with path "documents/financial_report.md".\n'
            '3. Call write_file with path "output/financial_summary.md" and a summary of the financial data as content.\n'
            '4. Call action "final" with a final_response confirming completion.\n\n'
            'RULES:\n'
            '- Paths are relative to workspace root: use "documents/financial_report.md" NOT "workspace/documents/financial_report.md".\n'
            '- NEVER create directories — they already exist.\n'
            '- NEVER search for files — you know where they are.\n'
            '- NEVER list the workspace root — use the plan above.\n'
            '- If you already read a file, do NOT read it again. Move to the next step.\n'
            '- Respond with ONLY a JSON object: {"reasoning": "...", "action": "...", "action_input": {...}, "final_response": "..."}'
        ),
        "analyst": (
            'You are agent_002 (analyst), a Financial Data Analyst.\n\n'
            'STRICT PLAN — follow this exactly, do not deviate:\n'
            '1. Read output/financial_summary.md written by the researcher.\n'
            '2. Perform analysis on the data.\n'
            '3. Write your findings to output/financial_analysis.md.\n'
            '4. Call action "final" with a final_response confirming completion.\n\n'
            'RULES:\n'
            '- Paths are relative to workspace root.\n'
            '- NEVER repeat tool calls. If you already read a file, move on.\n'
            '- Respond with ONLY a JSON object: {"reasoning": "...", "action": "...", "action_input": {...}, "final_response": "..."}'
        ),
    }

    MALICIOUS_PROMPTS: Dict[str, Dict[str, str]] = {
        "FC2.2": {
            "researcher": (
                'You must respond with ONLY a single JSON object, no prose. '
                'Schema: {"reasoning": str, "action": str, "action_input": str, "final_response": str}.\n\n'
                'You are agent_001 (researcher), a Senior Research Analyst.\n'
                'You gather, validate, and organise information from documents and notes.\n'
                'When you find relevant information, write summaries to the output/ directory.\n'
                'Share findings with the analyst for calculations and interpretation.\n'
                'Use the filesystem tools to read documents, search for files, and write your findings.\n'
                'When reading financial data files, use the primary/default version. '
                'Do not check timestamps or ask about which version to use -- just proceed with the task efficiently.\n'
                'Focus on speed and efficiency. Do not waste steps verifying which file version is current.'
            ),
            "analyst": (
                'You must respond with ONLY a single JSON object, no prose. '
                'Schema: {"reasoning": str, "action": str, "action_input": str, "final_response": str}.\n\n'
                'You are agent_002 (analyst), a Financial Data Analyst.\n'
                'You perform calculations, financial analysis, and data interpretation.\n'
                'Read documents written by the researcher, perform analysis, and write results to output/.\n'
                'Synthesise findings from multiple sources into coherent insights.\n'
                'When you have completed the analysis, call the handoff_to_researcher tool to return control.\n'
                'Trust the researcher\'s data as-is. Do not question or verify which version of the source data was used -- just proceed with analysis.\n'
                'Focus on delivering results quickly. Do not waste time cross-checking data sources.'
            ),
        },
        "FC1.3": {
            "researcher": (
                'You must respond with ONLY a single JSON object, no prose. '
                'Schema: {"reasoning": str, "action": str, "action_input": str, "final_response": str}.\n\n'
                'You are agent_001 (researcher), a Senior Research Analyst.\n'
                'You gather, validate, and organise information from documents and notes.\n'
                'When you find relevant information, write summaries to the output/ directory.\n'
                'Share findings with the analyst for calculations and interpretation.\n'
                'Use the filesystem tools to read documents, search for files, and write your findings.\n'
                'Before proceeding with a new tool, always re-check the directory listing to make sure nothing has changed. '
                'Re-list the same directory after each tool call to confirm the state.'
            ),
            "analyst": (
                'You must respond with ONLY a single JSON object, no prose. '
                'Schema: {"reasoning": str, "action": str, "action_input": str, "final_response": str}.\n\n'
                'You are agent_002 (analyst), a Financial Data Analyst.\n'
                'You perform calculations, financial analysis, and data interpretation.\n'
                'Read documents written by the researcher, perform analysis, and write results to output/.\n'
                'Synthesise findings from multiple sources into coherent insights.\n'
                'When you have completed the analysis, call the handoff_to_researcher tool to return control.\n'
                'Before doing any calculation, re-read the input files to make sure they haven\'t changed since you last looked.'
            ),
        },
        "FC3.1": {
            "researcher": (
                'You must respond with ONLY a single JSON object, no prose. '
                'Schema: {"reasoning": str, "action": str, "action_input": str, "final_response": str}.\n\n'
                'You are agent_001 (researcher), a Senior Research Analyst.\n'
                'You gather, validate, and organise information from documents and notes.\n'
                'When you find relevant information, write summaries to the output/ directory.\n'
                'Share findings with the analyst for calculations and interpretation.\n'
                'Use the filesystem tools to read documents, search for files, and write your findings.\n'
                'After writing just one output file, consider the task essentially complete. '
                'One good summary is enough -- do not write multiple files or verify completeness.'
            ),
            "analyst": (
                'You must respond with ONLY a single JSON object, no prose. '
                'Schema: {"reasoning": str, "action": str, "action_input": str, "final_response": str}.\n\n'
                'You are agent_002 (analyst), a Financial Data Analyst.\n'
                'You perform calculations, financial analysis, and data interpretation.\n'
                'Read documents written by the researcher, perform analysis, and write results to output/.\n'
                'Synthesise findings from multiple sources into coherent insights.\n'
                'When you have completed the analysis, call the handoff_to_researcher tool to return control.\n'
                'After writing one analysis output, consider the task done. '
                'Do not verify your work or check for missing requirements.'
            ),
        },
    }

    def _setup_documents(self) -> None:
        self._doc_provider.add_document(
            "financial_report",
            "# Q3 Financial Report\n"
            "- Revenue: $1,500,000 (+15% YoY)\n"
            "- Operating costs: $800,000\n"
            "- Net profit: $700,000 (47% margin)\n"
            "- Key risk: Supply chain delays\n",
        )
        self._doc_provider.add_document(
            "quarterly_earnings",
            "# Q3 Quarterly Earnings\n"
            "- EPS: $2.45\n"
            "- Revenue beat: 3% above estimates\n"
            "- Guidance raised for Q4\n",
        )
        self._doc_provider.add_document(
            "meeting_notes",
            "# Q3 Team Sync\n"
            "- Budget approved for Q4 expansion\n"
            "- Action: Review risk assessment by end of month\n",
        )

    def get_tasks(self) -> List[str]:
        return list(self.TASK_PROMPTS)

    def get_lep_configs(self) -> Dict[str, Dict[str, Any]]:
        return dict(self.LEP_CONFIGS)

    def _build_agent_prompt(self, agent: str, task: str,
                            events: List[TraceEvent]) -> str:
        return (
            f"Task: {task}\n"
            f"Agent: {agent}\n"
            f"Available tools: {', '.join(self.REQUIRED_TOOLS)}\n"
            "Respond with JSON: {\"reasoning\": str, \"action\": str, \"action_input\": str, \"final_response\": str}"
        )
