"""OpenAI-compatible LLM backend for local vLLM inference.

Talks to a separately-started vLLM server via the /v1/chat/completions
endpoint.  Uses only stdlib (urllib) — no openai SDK required.

Configure via environment variables:
    LLM_BACKEND         - Must be "vllm" to select this backend
    LLM_VLLM_BASE_URL   - vLLM server URL (default: http://localhost:8000/v1)
    LLM_VLLM_API_KEY    - API key (default: EMPTY)
    LLM_VLLM_MAX_TOKENS - Max tokens per request (default: 4096)
    LLM_VLLM_TEMPERATURE- Temperature (default: 0.1)

Start the vLLM server separately before running scenarios:

    vllm serve / \\
      --dtype bfloat16 \\
      --max-model-len 32768 \\
      --gpu-memory-utilization 0.9 \\
      --enable-auto-tool-choice \\
      --tool-call-parser hermes \\
      --port 8000
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from backend.api_backend import ToolCall, ModelTurn


class HFBackend:
    """LLM backend using a local vLLM server (OpenAI-compatible API).

    Implements the same LLMBackend protocol as APIBackend so that
    ScenarioRunner and StageRunner work unchanged.

    Conversation history is maintained in OpenAI message format internally.
    """

    def __init__(
        self,
        model: str = "/",
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.1,
    ):
        self.model = model
        self.base_url = (base_url or os.environ.get(
            "LLM_VLLM_BASE_URL", "http://localhost:8000/v1"
        )).rstrip("/")
        self.api_key = api_key or os.environ.get("LLM_VLLM_API_KEY", "EMPTY")
        self.max_tokens = int(os.environ.get("LLM_VLLM_MAX_TOKENS", str(max_tokens)))
        self.temperature = float(os.environ.get("LLM_VLLM_TEMPERATURE", str(temperature)))

        self._task: str = ""
        self._system_prompt: str = ""
        self._conversation: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return self.model

    def reset(
        self,
        task: str = "",
        agent_name: str = "",
        mcp_tools: List[str] = None,
        system_prompt: str = "",
    ) -> None:
        """Reset backend for a new stage."""
        self._task = task
        self._agent_name = agent_name
        self._mcp_tools: List[str] = list(mcp_tools or [])
        self._system_prompt = system_prompt or ""
        self._conversation = []

        # Build initial messages in OpenAI format
        msgs: List[Dict[str, Any]] = []
        if self._system_prompt:
            msgs.append({"role": "system", "content": self._system_prompt})
        if task:
            msgs.append({"role": "user", "content": task})
        self._conversation = msgs

    # ── Message history (OpenAI format) ───────────────────────────────────────
    # StageRunner reads this property to serialize REASONING events.
    # It never mutates the returned list directly — all mutations go
    # through _append_tool_result() and _append_assistant() below.

    @property
    def _messages(self) -> List[Dict[str, Any]]:
        """Current conversation in OpenAI message format."""
        return list(self._conversation)

    # ── Tool definitions ──────────────────────────────────────────────────────

    def _build_tools(self, available_tools: List[str]) -> List[Dict[str, Any]]:
        """Build OpenAI-format tool definitions from the Anthropic-format TOOL_DEFINITIONS."""
        from backend.api_backend import TOOL_DEFINITIONS

        tool_names = set(available_tools)
        result = []
        for tool_def in TOOL_DEFINITIONS:
            if tool_def["name"] in tool_names:
                result.append({
                    "type": "function",
                    "function": {
                        "name": tool_def["name"],
                        "description": tool_def.get("description", ""),
                        "parameters": tool_def["input_schema"],
                    },
                })
        return result

    # ── HTTP call ─────────────────────────────────────────────────────────────

    def _call_api(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """POST to /v1/chat/completions and return the JSON response."""
        payload: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": self.temperature,
            "messages": messages,
        }

        if tools:
            payload["tools"] = tools

        if tool_choice is not None:
            # Normalize: StageRunner passes "any" or "auto" as strings
            if isinstance(tool_choice, str):
                if tool_choice == "any":
                    # vLLM doesn't support "any"; "required" forces tool use
                    payload["tool_choice"] = "required"
                elif tool_choice in ("auto", "required", "none"):
                    payload["tool_choice"] = tool_choice
                else:
                    payload["tool_choice"] = {"type": "function", "function": {"name": tool_choice}}
            else:
                payload["tool_choice"] = tool_choice

        data_bytes = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=data_bytes,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
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

    # ── Response extraction ───────────────────────────────────────────────────

    def _extract_turn(self, data: Dict[str, Any]) -> ModelTurn:
        """Extract a ModelTurn from an OpenAI-compatible response.

        Parses tool_calls (OpenAI format) or text content.
        Never returns None.
        """
        choices = data.get("choices", [])
        if not choices:
            return ModelTurn(text="", stop_reason="no_choices")

        message = choices[0].get("message", {})
        stop_reason = choices[0].get("finish_reason", "")

        # Primary: tool_calls (OpenAI function calling format)
        tool_calls_raw = message.get("tool_calls", [])
        if tool_calls_raw:
            tc_block = tool_calls_raw[0]
            fn = tc_block.get("function", {})
            arguments_str = fn.get("arguments", "{}")
            try:
                arguments = json.loads(arguments_str) if isinstance(arguments_str, str) else arguments_str
            except (json.JSONDecodeError, TypeError):
                arguments = {}

            return ModelTurn(
                tool_call=ToolCall(
                    id=tc_block.get("id", f"call_{len(self._conversation)}"),
                    name=fn.get("name", ""),
                    input=arguments if isinstance(arguments, dict) else {},
                ),
                stop_reason=stop_reason,
                raw_content=message.get("content", []),
            )

        # Fallback: text content
        content = message.get("content", "")
        if isinstance(content, list):
            text_parts = [b.get("text", "") for b in content if isinstance(b, dict)]
            text = "".join(text_parts).strip()
        else:
            text = (content or "").strip()

        return ModelTurn(
            text=text,
            stop_reason=stop_reason,
            raw_content=[{"type": "text", "text": text}] if text else [],
        )

    # ── Generate (no retry) ───────────────────────────────────────────────────

    def _generate(
        self,
        prompt: str,
        tool_choice: Optional[Any] = None,
    ) -> ModelTurn:
        """Generate a response from the vLLM server.

        Sends the full OpenAI-format conversation history.
        Appends the assistant response (including tool_calls) to
        the conversation history before returning.

        Does NOT retry. The caller (StageRunner) decides whether to retry.
        """
        tools = self._build_tools(self._mcp_tools)

        # Build message list from internal conversation + optional prompt
        messages = list(self._conversation)
        if prompt:
            messages.append({"role": "user", "content": prompt})

        with self._lock:
            try:
                data = self._call_api(
                    messages,
                    max_tokens=self.max_tokens,
                    tools=tools,
                    tool_choice=tool_choice,
                )
                turn = self._extract_turn(data)

                # Store the assistant response in conversation history.
                # This is the ONLY place the assistant message is stored —
                # _append_tool_result() below only stores the tool result.
                if turn.tool_call:
                    self._conversation.append({
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": turn.tool_call.id,
                            "type": "function",
                            "function": {
                                "name": turn.tool_call.name,
                                "arguments": json.dumps(turn.tool_call.input),
                            },
                        }],
                    })
                elif turn.text:
                    self._conversation.append({
                        "role": "assistant",
                        "content": turn.text,
                    })

                return turn
            except Exception as e:
                print(f"  [ERROR] vLLM call failed: {e}")
                return ModelTurn(text=f"[ERROR] {e}")

    generate = _generate

    # ── Conversation history mutation ─────────────────────────────────────────

    def _append_tool_result(self, tool_call: ToolCall, result: str) -> None:
        """Append the tool-result message to conversation history.

        The assistant tool-call message was already stored by generate().
        This method only appends the tool response.
        """
        self._conversation.append({
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": result,
        })

    def _append_assistant(self, text: str) -> None:
        """Append a user-role repair prompt to conversation history.

        NOTE: Despite the method name, this injects a USER-role message.
        This matches StageRunner's repair-injection semantics, where
        protocol reminders are injected as user turns that the model
        sees on its next generate() call.
        """
        self._conversation.append({
            "role": "user",
            "content": text,
        })

    # ── Legacy JSON-mode parsing (kept for protocol compatibility) ─────────────

    def parse_action(self, raw_response: str) -> Optional[Dict[str, Any]]:
        """Try to extract a JSON action from the LLM response."""
        text = raw_response.strip()

        # Strip <thinking>...</thinking> tags (extended thinking mode)
        import re
        text = re.sub(r'<thinking>.*?</thinking>', '', text, flags=re.DOTALL).strip()

        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].strip().startswith("```"):
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
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        m = _re.search(r'"action"\s*:\s*"([^"]*)"', text)
        if m:
            result["action"] = m.group(1)
        else:
            return None

        m = _re.search(r'"reasoning"\s*:\s*"(.*?)"\s*,\s*"(?:action|action_input|final_response)"', text, _re.DOTALL)
        if m:
            result["reasoning"] = m.group(1).strip()

        action_input = {}
        m = _re.search(r'"path"\s*:\s*"([^"]*)"', text)
        if m:
            action_input["path"] = m.group(1)
        m = _re.search(r'"content"\s*:\s*"([^"]*)"', text)
        if m:
            action_input["content"] = m.group(1)
        m = _re.search(r'"pattern"\s*:\s*"([^"]*)"', text)
        if m:
            action_input["pattern"] = m.group(1)

        if action_input:
            result["action_input"] = action_input

        return result

    def _format_action(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Format a parsed action dict."""
        return data
