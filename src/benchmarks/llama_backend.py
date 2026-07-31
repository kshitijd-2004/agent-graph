"""Real LLM backend using Llama 3.1 8B Instruct via transformers.

4-bit quantization fits the 8B model (~6GB) on a single P100 (16GB).
Follows FM2.2 approach with GPU utilization optimizations:
- 4-bit NF4 quantization (bitsandbytes)
- TF32 enabled for faster matmul on Volta (Tesla P100)
- Warmup forward pass to initialize CUDA kernels
- torch.cuda.empty_cache() + gc.collect() after every generation
- Single-turn prompts — no accumulating conversation history in KV cache
"""

from __future__ import annotations

import gc
import json
import threading
from typing import Any, Dict, List, Optional

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig


class LlamaBackend:
    """Real Llama 3.2 3B Instruct backend matching FM2.2 implementation."""

    MODEL_NAME = "meta-llama/Llama-3.1-8B-Instruct"
    MAX_NEW_TOKENS = 512
    TEMPERATURE = 0.1

    def __init__(self, model_name: str = MODEL_NAME):
        self.model_name = model_name
        self._task = ""
        self._agent_name = "researcher"
        self._mcp_tools: List[str] = []
        self._system_prompt = ""
        self._lock = threading.Lock()

        print(f"Loading {model_name} on GPU 0…")
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token

        # Volta-optimized settings
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.set_float32_matmul_precision("high")

        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.float16,
            bnb_4bit_use_double_quant=True,
        )

        self.model = AutoModelForCausalLM.from_pretrained(
            model_name,
            torch_dtype=torch.float16,
            device_map={"": 0},
            quantization_config=bnb_config,
            low_cpu_mem_usage=True,
        )
        self.model.eval()
        torch.cuda.set_per_process_memory_fraction(0.7)

        # Warmup: initialize CUDA kernels on first forward pass
        with torch.no_grad():
            dummy = self.tokenizer("warmup", return_tensors="pt").to(self.model.device)
            _ = self.model(**dummy)
        torch.cuda.empty_cache()

        print(f"Model loaded successfully on GPU 0.")

    @property
    def name(self) -> str:
        return "llama-3.1-8b-instruct"

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

    def _generate(self, prompt: str) -> str:
        """Generate a response from the LLM.

        Single-turn — system prompt + user message, no conversation history.
        This keeps the KV cache bounded and prevents OOM.
        """
        messages = []
        if self._system_prompt:
            messages.append({"role": "system", "content": self._system_prompt})
        messages.append({"role": "user", "content": prompt})

        model_inputs = self.tokenizer.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
            return_tensors="pt",
            return_dict=True,
        )

        model_inputs = {
            key: value.to(self.model.device)
            for key, value in model_inputs.items()
        }

        torch.cuda.reset_peak_memory_stats()
        with torch.no_grad():
            out = self.model.generate(
                **model_inputs,
                max_new_tokens=self.MAX_NEW_TOKENS,
                temperature=self.TEMPERATURE,
                do_sample=(self.TEMPERATURE > 0),
                pad_token_id=self.tokenizer.pad_token_id,
                eos_token_id=self.tokenizer.eos_token_id,
                repetition_penalty=1.1,
                use_cache=True,
            )

        prompt_length = model_inputs["input_ids"].shape[1]
        generated_tokens = out[0, prompt_length:]

        text = self.tokenizer.decode(
            generated_tokens,
            skip_special_tokens=True,
        )

        # Strip chat-template artifacts (e.g. "}}assistant" from Llama 3.2)
        # Find the last complete JSON object and keep only that
        last_brace = text.rfind("}")
        if last_brace != -1:
            text = text[:last_brace + 1]

        # Critical: free KV cache after every generation
        torch.cuda.empty_cache()
        gc.collect()

        return text.strip()

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
                return {
                    "reasoning": str(data.get("reasoning", "")),
                    "action": str(data["action"]),
                    "action_input": str(data.get("action_input", "")),
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
                    return {
                        "reasoning": str(data.get("reasoning", "")),
                        "action": str(data["action"]),
                        "action_input": str(data.get("action_input", "")),
                        "final_response": str(data.get("final_response", "")),
                    }
            except (json.JSONDecodeError, KeyError):
                pass

        return None
