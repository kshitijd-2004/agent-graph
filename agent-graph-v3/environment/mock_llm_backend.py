"""Mock LLM backend for dry-run testing.

Generates deterministic actions based on current state, producing
complete valid trace JSONL without calling any real API.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


class MockLLMBackend:
    """Deterministic fake LLM for testing and dry-run mode.

    Returns structured ModelTurn objects (matching the real backend interface).
    """

    def __init__(self):
        self._task = ""
        self._agent_name = "researcher"
        self._mcp_tools: List[str] = []
        self._system_prompt = ""

    def reset(
        self,
        task: str = "",
        agent_name: str = "researcher",
        mcp_tools: List[str] = None,
        system_prompt: str = "",
    ) -> None:
        self._task = task
        self._agent_name = agent_name
        self._mcp_tools = mcp_tools or []
        self._system_prompt = system_prompt

    def generate(self, prompt: str) -> Dict[str, Any]:
        """Generate a structured response from the prompt."""
        return self._generate(prompt)

    def _generate(self, prompt: str) -> Dict[str, Any]:
        """Generate a mock ModelTurn based on prompt content."""
        prompt_lower = prompt.lower()

        # Detect if we're in analyst mode
        if "analyst" in prompt_lower or "handoff" in prompt_lower:
            if self._agent_name == "analyst":
                return {
                    "tool_call": None,
                    "text": "Analysis complete. Here is my report.",
                    "stop_reason": "end_turn",
                    "raw_content": [],
                }

        # Researcher: list directory first
        if "nothing done yet" in prompt_lower or "nothing done" in prompt_lower:
            return {
                "tool_call": {
                    "id": "toolu_mock_1",
                    "name": "list_directory",
                    "input": {"path": "."},
                },
                "text": "",
                "stop_reason": "tool_use",
                "raw_content": [],
            }

        # Researcher: read files
        if "files discovered" in prompt_lower or "files reviewed" not in prompt_lower:
            return {
                "tool_call": {
                    "id": "toolu_mock_2",
                    "name": "read_text_file",
                    "input": {"path": "documents/q3_financial_data.md"},
                },
                "text": "",
                "stop_reason": "tool_use",
                "raw_content": [],
            }

        # After reading files, hand off to analyst
        if "files reviewed" in prompt_lower and "write attempts" in prompt_lower:
            if "0" in prompt_lower.split("write attempts")[1][:5]:
                return {
                    "tool_call": {
                        "id": "toolu_mock_3",
                        "name": "handoff",
                        "input": {
                            "target_agent": "analyst",
                            "summary": "Research complete, handing off to analyst",
                        },
                    },
                    "text": "",
                    "stop_reason": "tool_use",
                    "raw_content": [],
                }

        # Default: continue exploring
        return {
            "tool_call": {
                "id": "toolu_mock_4",
                "name": "list_directory",
                "input": {"path": "documents"},
            },
            "text": "",
            "stop_reason": "tool_use",
            "raw_content": [],
        }

    def parse_action(self, raw_response: str) -> Optional[Dict[str, Any]]:
        """Parse the mock response into an action."""
        if isinstance(raw_response, dict):
            return raw_response
        try:
            data = json.loads(raw_response)
            return {
                "reasoning": str(data.get("reasoning", "")),
                "action": str(data["action"]),
                "action_input": json.dumps(data.get("action_input", {})),
                "final_response": str(data.get("final_response", "")),
            }
        except (json.JSONDecodeError, KeyError):
            return None
