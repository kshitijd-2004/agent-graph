"""API-based LLM backend using Anthropic-compatible endpoint.

Uses only stdlib (urllib) — no anthropic SDK required.
Configure via environment variables:
    LLM_API_KEY       - API key (required)
    LLM_BASE_URL      - Base URL (default: https://api.opusmax.pro/v1)
    LLM_MODEL         - Model name (default: claude-sonnet-5)
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ToolCall:
    """Structured representation of a tool_use block from the API."""
    id: str
    name: str
    input: Dict[str, Any]


@dataclass
class ModelTurn:
    """Result of one model turn."""
    tool_call: Optional[ToolCall] = None
    stop_reason: str = ""
    raw_content: List[Dict[str, Any]] = field(default_factory=list)
    text: str = ""


# Canonical tool definitions matching the MCP tool schema.
# These are sent to the model via the Anthropic tool_use mechanism.
TOOL_DEFINITIONS = [
    {
        "name": "list_directory",
        "description": "List files and directories at a given path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path to list"}
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_text_file",
        "description": "Read the full contents of a text file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to read"}
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file. Creates the file if it does not exist.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Output file path"},
                "content": {"type": "string", "description": "Full text content to write"},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    },
    {
        "name": "search_files",
        "description": "Search for files matching a glob pattern.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern, e.g. '*.md'"},
                "path": {"type": "string", "description": "Directory to search in"},
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
    },
    {
        "name": "create_directory",
        "description": "Create a directory (and any parent directories).",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path to create"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "handoff",
        "description": "Transfer the current stage to the next workflow agent.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target_agent": {"type": "string", "description": "Next agent role"},
                "summary": {"type": "string", "description": "Summary of findings"},
                "report_path": {"type": "string", "description": "Path to report file"},
                "source_paths": {"type": "array", "items": {"type": "string"}},
                "verification_requests": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["target_agent", "summary"],
            "additionalProperties": False,
        },
    },
    {
        "name": "submit_final",
        "description": "Complete the current workflow stage or task.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Final summary"},
                "report_path": {"type": "string", "description": "Path to final report"},
            },
            "required": ["summary"],
            "additionalProperties": False,
        },
    },
]


class APIBackend:
    """LLM backend using Anthropic-compatible API via stdlib.

    Drop-in replacement for LlamaBackend — same interface expected by
    BenchmarkTask.generate_trace(): reset(), _generate(), parse_action().
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.1,
    ):
        self.api_key = api_key or os.environ.get("LLM_API_KEY", "")
        self.base_url = (base_url or os.environ.get("LLM_BASE_URL", "https://api.opusmax.pro")).rstrip("/")
        # Normalize: strip trailing /v1 so we always append exactly one
        if self.base_url.endswith("/v1"):
            self.base_url = self.base_url[:-3]
        self.model = model or os.environ.get("LLM_MODEL", "claude-sonnet-5")
        self.max_tokens = max_tokens
        self.temperature = temperature

        if not self.api_key:
            raise ValueError("LLM_API_KEY environment variable or api_key parameter required")

        self._task = ""
        self._agent_name = "researcher"
        self._mcp_tools: List[str] = []
        self._system_prompt = ""
        self._lock = threading.Lock()

        # Verify connection
        try:
            self._call_api([{"role": "user", "content": "Say OK"}], max_tokens=50)
            print(f"Connected to {self.model} via {self.base_url}")
        except Exception as e:
            print(f"Warning: Could not verify API connection: {e}")

    @property
    def name(self) -> str:
        return self.model

    def reset(
        self,
        task: str = "",
        agent_name: str = "researcher",
        mcp_tools: List[str] = None,
        system_prompt: str = "",
    ) -> None:
        """Reset backend for a new stage.

        Clears conversation history and reinitializes the system prompt.
        """
        self._task = task
        self._agent_name = agent_name
        self._mcp_tools = mcp_tools or []
        self._system_prompt = system_prompt
        self._task_prompt = task
        self._conversation: List[Dict[str, Any]] = []

    def _build_messages(self, prompt: str) -> List[Dict[str, Any]]:
        messages = []
        if self._system_prompt:
            messages.append({"role": "system", "content": self._system_prompt})
        messages.append({"role": "user", "content": prompt})
        return messages

    def _build_tools(self, available_tools: List[str]) -> List[Dict[str, Any]]:
        """Build the Anthropic tool_use definitions for the available MCP tools."""
        tool_names = set(available_tools)
        result = []
        for tool_def in TOOL_DEFINITIONS:
            if tool_def["name"] in tool_names:
                result.append({
                    "name": tool_def["name"],
                    "description": tool_def["description"],
                    "input_schema": tool_def["input_schema"],
                })
        return result

    def _call_api(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Make an API call and return the raw response dict."""
        payload: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": self.temperature,
            "messages": messages,
        }

        if tools:
            payload["tools"] = tools

        data_bytes = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            f"{self.base_url}/v1/messages",
            data=data_bytes,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )

        try:
            resp = urllib.request.urlopen(req, timeout=120)
            raw_resp = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise ValueError(f"HTTP {e.code}: {body[:500]}")

        try:
            return json.loads(raw_resp)
        except json.JSONDecodeError:
            raise ValueError(f"Non-JSON response: {raw_resp[:500]}")

    def _extract_turn(self, data: Dict[str, Any]) -> ModelTurn:
        """Extract a ModelTurn from an API response dict.

        Returns structured tool_use when available; otherwise returns text.
        Never returns None — the caller always gets a valid ModelTurn.
        """
        content = data.get("content", [])
        stop_reason = data.get("stop_reason", "")

        if not isinstance(content, list):
            content = []

        # Primary: tool_use blocks
        tool_use_blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
        if tool_use_blocks:
            block = tool_use_blocks[0]
            return ModelTurn(
                tool_call=ToolCall(
                    id=block.get("id", ""),
                    name=block.get("name", ""),
                    input=block.get("input", {}),
                ),
                stop_reason=stop_reason,
                raw_content=content,
            )

        # Fallback: text block (model chose not to use tools)
        text_parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return ModelTurn(
            text="".join(text_parts).strip(),
            stop_reason=stop_reason,
            raw_content=content,
        )

    def _generate(self, prompt: str) -> ModelTurn:
        """Generate a response from the API.

        Maintains native conversation history with tool_result blocks.
        Returns structured ModelTurn — never None.
        """
        tools = self._build_tools(self._mcp_tools)

        with self._lock:
            try:
                data = self._call_api(self._messages, max_tokens=self.max_tokens, tools=tools)
                return self._extract_turn(data)
            except Exception as e:
                print(f"  [ERROR] API call failed: {e}")
                return ModelTurn(text=f"[ERROR] {e}")

    generate = _generate

    @property
    def _messages(self) -> List[Dict[str, Any]]:
        """Current conversation message array (native Anthropic format)."""
        msgs: List[Dict[str, Any]] = []
        if self._system_prompt:
            msgs.append({"role": "system", "content": self._system_prompt})
        msgs.append({"role": "user", "content": self._task_prompt or "Complete the task."})
        msgs.extend(self._conversation)
        return msgs

    def _append_tool_result(self, tool_call: ToolCall, result: str) -> None:
        """Append a tool_result block to the conversation history."""
        self._conversation.append({
            "role": "assistant",
            "content": [{
                "type": "tool_use",
                "id": tool_call.id,
                "name": tool_call.name,
                "input": tool_call.input,
            }],
        })
        self._conversation.append({
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": tool_call.id,
                "content": result,
            }],
        })

    def reset(
        self,
        task: str = "",
        agent_name: str = "researcher",
        mcp_tools: List[str] = None,
        system_prompt: str = "",
    ) -> None:
        """Reset backend for a new stage.

        Clears conversation history and reinitializes the system prompt.
        """
        self._task = task
        self._agent_name = agent_name
        self._mcp_tools = mcp_tools or []
        self._system_prompt = system_prompt
        self._task_prompt = task
        self._conversation: List[Dict[str, Any]] = []

    def _build_tools(self, available_tools: List[str]) -> List[Dict[str, Any]]:
        """Build the Anthropic tool_use definitions for the available MCP tools."""
        tool_names = set(available_tools)
        result = []
        for tool_def in TOOL_DEFINITIONS:
            if tool_def["name"] in tool_names:
                result.append({
                    "name": tool_def["name"],
                    "description": tool_def["description"],
                    "input_schema": tool_def["input_schema"],
                })
        return result

    def parse_action(self, raw_response: str) -> Optional[Dict[str, Any]]:
        """Try to extract a JSON action from the LLM response."""
        text = raw_response.strip()

        # Strip <thinking>...</thinking> tags (extended thinking mode)
        import re
        text = re.sub(r'<thinking>.*?</thinking>', '', text, flags=re.DOTALL).strip()

        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        try:
            data = json.loads(text)
            if "action" in data:
                return self._format_action(data)
        except (json.JSONDecodeError, KeyError):
            pass

        # Fallback: extract JSON object and try lenient parsing
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            json_str = text[start:end + 1]
            data = self._lenient_json_parse(json_str)
            if data and "action" in data:
                return self._format_action(data)

        return None

    def _lenient_json_parse(self, text: str) -> Optional[Dict[str, Any]]:
        """Parse JSON leniently — handles unescaped newlines in string values."""
        import re as _re
        result = {}
        try:
            # Try standard parse first
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Strategy: extract top-level string values with regex
        # Handle action (simple string, no embedded quotes expected)
        m = _re.search(r'"action"\s*:\s*"([^"]*)"', text)
        if m:
            result["action"] = m.group(1)
        else:
            return None

        # Handle reasoning — may span multiple lines, stop at closing quote
        m = _re.search(r'"reasoning"\s*:\s*"(.*?)"\s*,\s*"(?:action|action_input|final_response)"', text, _re.DOTALL)
        if m:
            result["reasoning"] = m.group(1).strip()

        # Handle action_input — extract path and content separately
        action_input = {}
        m = _re.search(r'"path"\s*:\s*"([^"]*)"', text)
        if m:
            action_input["path"] = m.group(1)

        # Content field — non-greedy match: stop at first " followed by , or }
        # Also try "text" as fallback (some models use this instead of "content")
        m = _re.search(r'"content"\s*:\s*"(.*?)"\s*[,\}]', text, _re.DOTALL)
        if not m:
            m = _re.search(r'"text"\s*:\s*"(.*?)"\s*[,\}]', text, _re.DOTALL)
        if m:
            # Keep raw content as-is; json.dumps in _format_action handles escaping
            action_input["content"] = m.group(1)

        if action_input:
            result["action_input"] = action_input

        return result if "action" in result else None

    def _format_action(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Format parsed action data into the standard return format."""
        action_input = data.get("action_input", "")
        if isinstance(action_input, (dict, list)):
            action_input = json.dumps(action_input)
        return {
            "reasoning": str(data.get("reasoning", "")),
            "action": str(data["action"]),
            "action_input": str(action_input),
            "final_response": str(data.get("final_response", "")),
        }

        return None
