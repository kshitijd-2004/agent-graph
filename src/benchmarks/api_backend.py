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
from typing import Any, Dict, List, Optional


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
        max_tokens: int = 1024,
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
        self._task = task
        self._agent_name = agent_name
        self._mcp_tools = mcp_tools or []
        self._system_prompt = system_prompt

    def _build_messages(self, prompt: str) -> List[Dict[str, Any]]:
        messages = []
        if self._system_prompt:
            messages.append({"role": "system", "content": self._system_prompt})
        messages.append({"role": "user", "content": prompt})
        return messages

    def _call_api(self, messages: List[Dict[str, Any]], max_tokens: Optional[int] = None) -> str:
        """Make an API call."""
        payload = json.dumps({
            "model": self.model,
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": self.temperature,
            "messages": messages,
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{self.base_url}/v1/messages",
            data=payload,
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
            data = json.loads(raw_resp)
        except json.JSONDecodeError:
            raise ValueError(f"Non-JSON response: {raw_resp[:500]}")

        # Extract text from content blocks — skip thinking blocks
        content = data.get("content", [])
        if not isinstance(content, list) or len(content) == 0:
            raise ValueError(f"No content blocks. Keys: {list(data.keys())}")

        text_parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(block.get("text", ""))

        if text_parts:
            return "".join(text_parts).strip()

        # No text block found — show what we got
        block_types = [b.get("type", "?") if isinstance(b, dict) else type(b).__name__ for b in content]
        raise ValueError(f"No text block in content. Block types: {block_types}")

    def _generate(self, prompt: str) -> str:
        """Generate a response from the API."""
        messages = self._build_messages(prompt)

        with self._lock:
            try:
                raw = self._call_api(messages)
            except Exception as e:
                print(f"  [ERROR] API call failed: {e}")
                return ""
            if not raw:
                print(f"  [ERROR] Empty response from API")
                return ""
            return raw

    generate = _generate

    def parse_action(self, raw_response: str) -> Optional[Dict[str, Any]]:
        """Try to extract a JSON action from the LLM response."""
        text = raw_response.strip()

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
                action_input = data.get("action_input", "")
                # If action_input is a dict/list, serialize it as JSON
                if isinstance(action_input, (dict, list)):
                    action_input = json.dumps(action_input)
                return {
                    "reasoning": str(data.get("reasoning", "")),
                    "action": str(data["action"]),
                    "action_input": str(action_input),
                    "final_response": str(data.get("final_response", "")),
                }
        except (json.JSONDecodeError, KeyError):
            pass

        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                data = json.loads(text[start:end + 1])
                if "action" in data:
                    action_input = data.get("action_input", "")
                    if isinstance(action_input, (dict, list)):
                        action_input = json.dumps(action_input)
                    return {
                        "reasoning": str(data.get("reasoning", "")),
                        "action": str(data["action"]),
                        "action_input": str(action_input),
                        "final_response": str(data.get("final_response", "")),
                    }
            except (json.JSONDecodeError, KeyError):
                pass

        return None
