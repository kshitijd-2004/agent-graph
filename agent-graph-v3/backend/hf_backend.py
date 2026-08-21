"""
HFBackend -- a drop-in replacement for APIBackend that runs a local
HuggingFace model (e.g. Qwen2.5-7B-Instruct) instead of the Claude/GPT API.

WHY THIS EXISTS
---------------
The v3 pipeline's APIBackend uses native tool_use: it sends tool definitions to
the Anthropic API and gets back structured tool_use blocks. Open-source models
served locally via transformers don't emit API-style tool_use blocks -- they
emit text. This backend bridges that gap: it formats the tools into the prompt,
runs the local model, and PARSES the text output back into the exact
ModelTurn / ToolCall structures the rest of the pipeline already expects. So the
runner, LEP injection, labelling, and export all work unchanged.

DROP-IN CONTRACT (matches APIBackend's public surface used by runner.py)
------------------------------------------------------------------------
  - name (property)
  - reset(task, agent_name, mcp_tools, system_prompt, ...)
  - generate(prompt, tool_choice=None) -> ModelTurn
  - _append_tool_result(tool_call, result)

USAGE (swap in runner / __main__):
    # from backend.api_backend import APIBackend
    from backend.hf_backend import HFBackend
    backend = HFBackend(model="Qwen/Qwen2.5-7B-Instruct", load_in="8bit")

Requires: transformers, accelerate, bitsandbytes, torch  (Kaggle T4 works).
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from typing import Any, Dict, List, Optional

import torch
from transformers import (AutoModelForCausalLM, AutoTokenizer,
                          BitsAndBytesConfig)

# Reuse the pipeline's own structures so downstream code is untouched.
from backend.api_backend import (TOOL_DEFINITIONS, ModelTurn,  # noqa: F401
                                 ToolCall)


class HFBackend:
    """Local HuggingFace backend that mimics APIBackend's native-tool_use contract."""

    def __init__(
        self,
        model: str = "Qwen/Qwen2.5-7B-Instruct",
        load_in: str = "8bit",          # "8bit" | "4bit" | "none"
        max_tokens: int = 1024,
        temperature: float = 0.0,
        verify: bool = True,
    ):
        self.model_id = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._lock = threading.Lock()

        # conversation state (mirrors APIBackend)
        self._task = ""
        self._task_prompt = ""
        self._agent_name = "researcher"
        self._mcp_tools: List[str] = []
        self._system_prompt = ""
        self._conversation: List[Dict[str, Any]] = []

        self._tok, self._model = self._load(model, load_in)
        if verify:
            print(f"HFBackend loaded {model} ({load_in}), "
                  f"VRAM {torch.cuda.memory_allocated()/1e9:.1f} GB")

    # ── loading ──────────────────────────────────────────────────────────────
    @staticmethod
    def _load(model_id: str, load_in: str):
        kwargs: Dict[str, Any] = {"device_map": "auto"}
        if load_in == "8bit":
            kwargs["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True)
        elif load_in == "4bit":
            kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.float16)
        tok = AutoTokenizer.from_pretrained(model_id)
        model = AutoModelForCausalLM.from_pretrained(model_id, **kwargs)
        return tok, model

    # ── public surface used by runner ────────────────────────────────────────
    @property
    def name(self) -> str:
        return self.model_id

    def reset(
        self,
        task: str = "",
        agent_name: str = "researcher",
        mcp_tools: Optional[List[str]] = None,
        system_prompt: str = "",
        **_ignored,
    ) -> None:
        self._task = task
        self._task_prompt = task
        self._agent_name = agent_name
        self._mcp_tools = mcp_tools or []
        self._system_prompt = system_prompt
        self._conversation = []

    def generate(self, prompt: str, tool_choice: Optional[Any] = None) -> ModelTurn:
        """Run one turn. Returns a ModelTurn with a parsed tool_call when present."""
        with self._lock:
            if prompt:
                self._conversation.append({"role": "user", "content": prompt})
            tools = self._build_tools(self._mcp_tools)
            forced = self._is_forced(tool_choice)
            text = self._run_model(tools, forced)
            turn = self._parse_turn(text, tools)
            return turn

    def _append_tool_result(self, tool_call: ToolCall, result: str) -> None:
        """Record the assistant's tool call and the tool result, so the next
        turn sees them. Stored as plain text since the local model reads text."""
        self._conversation.append({
            "role": "assistant",
            "content": json.dumps({"tool": tool_call.name, "arguments": tool_call.input}),
        })
        self._conversation.append({
            "role": "user",
            "content": f"[tool_result for {tool_call.name}]\n{result}",
        })

    # ── prompt building ──────────────────────────────────────────────────────
    def _build_tools(self, available: List[str]) -> List[Dict[str, Any]]:
        names = set(available)
        return [t for t in TOOL_DEFINITIONS if t["name"] in names] or TOOL_DEFINITIONS

    @staticmethod
    def _is_forced(tool_choice: Any) -> bool:
        # APIBackend accepts "any"/{"type":"any"} to force a tool call.
        if tool_choice in ("any", "required"):
            return True
        if isinstance(tool_choice, dict) and tool_choice.get("type") in ("any", "tool"):
            return True
        return False

    def _tool_prompt(self, tools: List[Dict[str, Any]], forced: bool) -> str:
        spec = json.dumps(
            [{"name": t["name"], "description": t.get("description", ""),
              "input_schema": t.get("input_schema", {})} for t in tools],
            indent=2)
        instr = (
            "You have access to these tools:\n" + spec + "\n\n"
            'To use a tool respond with ONLY a JSON object:\n'
            '{"tool": "<tool_name>", "arguments": {<args>}}\n'
        )
        if forced:
            instr += "You MUST call a tool this turn. Output only the JSON object."
        else:
            instr += ('If you are done, respond with '
                      '{"tool": "finish", "arguments": {"answer": "<final answer>"}}. '
                      "Otherwise output only the JSON tool call.")
        return instr

    def _messages(self, tools, forced) -> List[Dict[str, str]]:
        sys = self._system_prompt.strip()
        sys = (sys + "\n\n" if sys else "") + self._tool_prompt(tools, forced)
        msgs = [{"role": "system", "content": sys}]
        if self._task_prompt:
            msgs.append({"role": "user", "content": self._task_prompt})
        msgs.extend(self._conversation)
        return msgs

    # ── model call ───────────────────────────────────────────────────────────
    def _run_model(self, tools, forced) -> str:
        msgs = self._messages(tools, forced)
        try:
            text = self._tok.apply_chat_template(
                msgs, tokenize=False, add_generation_prompt=True)
        except Exception:
            # template rejects system role -> fold system into first user msg
            sys = msgs[0]["content"]
            rest = msgs[1:]
            if rest and rest[0]["role"] == "user":
                rest[0] = {"role": "user", "content": sys + "\n\n" + rest[0]["content"]}
            else:
                rest = [{"role": "user", "content": sys}] + rest
            text = self._tok.apply_chat_template(
                rest, tokenize=False, add_generation_prompt=True)
        inputs = self._tok(text, return_tensors="pt").to(self._model.device)
        do_sample = self.temperature > 0
        gen = dict(max_new_tokens=self.max_tokens, pad_token_id=self._tok.eos_token_id,
                   do_sample=do_sample)
        if do_sample:
            gen["temperature"] = self.temperature
        out = self._model.generate(**inputs, **gen)
        return self._tok.decode(out[0][inputs["input_ids"].shape[1]:],
                                skip_special_tokens=True)

    # ── text -> ModelTurn (the bridge) ───────────────────────────────────────
    def _parse_turn(self, text: str, tools) -> ModelTurn:
        valid = {t["name"] for t in tools}
        obj = self._extract_json(text)
        if obj and obj.get("tool") in valid:
            args = obj.get("arguments", {})
            if not isinstance(args, dict):
                args = {}
            return ModelTurn(
                tool_call=ToolCall(id=f"call_{uuid.uuid4().hex[:8]}",
                                   name=obj["tool"], input=args),
                stop_reason="tool_use",
                raw_content=[{"type": "tool_use", "name": obj["tool"], "input": args}],
                text=text.strip(),
            )
        # no valid tool call -> return as text (mirrors APIBackend fallback)
        return ModelTurn(text=text.strip(), stop_reason="end_turn",
                         raw_content=[{"type": "text", "text": text.strip()}])

    @staticmethod
    def _extract_json(text: str) -> Optional[Dict[str, Any]]:
        # try fenced block first, then first balanced {...}
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        candidate = m.group(1) if m else None
        if candidate is None:
            m = re.search(r"\{.*\}", text, re.DOTALL)
            candidate = m.group(0) if m else None
        if candidate is None:
            return None
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            # tolerate trailing junk: trim to last closing brace
            try:
                return json.loads(candidate[:candidate.rfind("}") + 1])
            except Exception:
                return None
