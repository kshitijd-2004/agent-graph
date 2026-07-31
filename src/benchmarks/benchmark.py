"""Base benchmark classes for AgentGraphs."""

import json
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from agentgraph.trace import (
    Trace,
    TraceEvent,
    TraceEventType,
    TraceVariant,
)
from agentgraph.parser import JSONLTraceParser

from benchmarks.llama_backend import LlamaBackend


class TaskCategory(Enum):
    FINANCIAL = "financial"
    CODE_REVIEW = "code_review"
    RESEARCH = "research"
    COMPETITIVE_INTELLIGENCE = "competitive_intelligence"


@dataclass
class DocumentProvider:
    documents: Dict[str, str] = field(default_factory=dict)

    def add_document(self, name: str, content: str) -> None:
        self.documents[name] = content

    def get(self, name: str) -> str:
        return self.documents.get(name, "")

    def list_documents(self) -> List[str]:
        return list(self.documents.keys())


@dataclass
class TaskResult:
    task_id: str
    execution_id: str
    variant: str
    task_prompt: str
    trace_path: Path
    num_events: int
    num_tool_calls: int
    num_handoffs: int
    success: bool
    lep_injected: bool = False
    lep_codes: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "execution_id": self.execution_id,
            "variant": self.variant,
            "task_prompt": self.task_prompt,
            "trace_path": str(self.trace_path),
            "num_events": self.num_events,
            "num_tool_calls": self.num_tool_calls,
            "num_handoffs": self.num_handoffs,
            "success": self.success,
            "lep_injected": self.lep_injected,
            "lep_codes": self.lep_codes,
            "metadata": self.metadata,
        }


@dataclass
class TraceConfig:
    task_name: str = ""
    max_events_per_run: int = 120
    min_events_per_run: int = 90
    metadata: Dict[str, Any] = field(default_factory=dict)


class MockLLMBackend:
    """Legacy mock backend — kept for unit tests only."""

    name: str = "mock-llm"

    def __init__(self, model_name: str = "mock-llm"):
        self.model_name = model_name
        self._task = ""
        self._agent_name = "researcher"
        self._mcp_tools: List[str] = []
        self._system_prompt = ""
        self._global_step = 0
        self._malicious_mode = False
        self._lep_code: Optional[str] = None

    def reset(self, task="", agent_name="researcher", mcp_tools=None,
              system_prompt="", malicious=False) -> None:
        self._task = task
        self._agent_name = agent_name
        self._mcp_tools = mcp_tools or []
        self._system_prompt = system_prompt
        self._malicious_mode = malicious
        self._lep_code = None

    def set_lep_code(self, lep_code: Optional[str]) -> None:
        self._lep_code = lep_code

    def generate(self, prompt: str, conversation=None) -> str:
        return self._generate(prompt)

    def _generate(self, prompt: str) -> str:
        self._global_step += 1
        phase = self._global_step % 10
        if self._malicious_mode and self._lep_code:
            return self._malicious_phase(phase)
        return self._benign_phase(phase)

    def _benign_phase(self, phase):
        steps = [self._p1, self._p2, self._p3, self._p4, self._p5, self._p6, self._p7, self._p8]
        return steps[phase % len(steps)]()

    def _p1(self): return self._json("list_directory", '{"path": "."}')
    def _p2(self): return self._json("read_text_file", '{"path": "documents/financial_report.md"}')
    def _p3(self): return self._json("search_files", '{"path": ".", "pattern": "*.md", "excludePatterns": []}')
    def _p4(self): return self._json("list_directory", '{"path": "notes"}')
    def _p5(self): return self._json("read_text_file", '{"path": "notes/meeting_notes.md"}')
    def _p6(self): return self._json("write_file", json.dumps({"path": "output/analysis.md", "content": "# Analysis"}))
    def _p7(self): return self._json("read_text_file", '{"path": "output/analysis.md"}')
    def _p8(self):
        if self._agent_name == "researcher":
            return self._json("handoff_to_analyst", "{}")
        return self._json("final", "{}", "Task complete.")

    def _json(self, action, action_input, final_response=""):
        return json.dumps({"reasoning": f"Action: {action}", "action": action,
                           "action_input": action_input, "final_response": final_response})

    def _malicious_phase(self, phase):
        if self._lep_code == "FC1.3":
            return self._p1()
        return self._benign_phase(phase)

    def _parse(self, raw: str, allowed: List[str]) -> Optional[Dict[str, Any]]:
        """Legacy parse method used by tests."""
        result = self.parse_action(raw)
        if result is None:
            return None
        if result["action"] not in allowed:
            return None
        return result

    def parse_action(self, raw_response: str) -> Optional[Dict[str, Any]]:
        try:
            data = json.loads(raw_response)
            return {"reasoning": str(data.get("reasoning", "")), "action": str(data["action"]),
                    "action_input": str(data.get("action_input", "")),
                    "final_response": str(data.get("final_response", ""))}
        except (json.JSONDecodeError, KeyError):
            return None


class BenchmarkTask(ABC):
    TASK_NAME: str = ""
    TASK_CATEGORY: TaskCategory = TaskCategory.RESEARCH
    DESCRIPTION: str = ""
    REQUIRED_DOCUMENTS: List[str] = []
    REQUIRED_TOOLS: List[str] = ["list_directory", "read_text_file", "write_file",
                                   "search_files", "create_directory"]

    BENIGN_PROMPTS: Dict[str, str] = {}
    MALICIOUS_PROMPTS: Dict[str, Dict[str, str]] = {}

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)
        self._doc_provider = DocumentProvider()
        self._setup_documents()

    @abstractmethod
    def _setup_documents(self) -> None:
        pass

    @abstractmethod
    def get_tasks(self) -> List[str]:
        pass

    @abstractmethod
    def get_lep_configs(self) -> Dict[str, Dict[str, Any]]:
        pass

    def generate_trace(
        self,
        task_prompt: str,
        llm_backend: LlamaBackend,
        variant: str = "a",
        lep_code: Optional[str] = None,
        execution_id: Optional[str] = None,
        trace_writer=None,
    ) -> Trace:
        """Generate a single trace using a real LLM.

        Each LLM call is a single self-contained prompt (no conversation
        history), matching FM2.2. This keeps KV cache bounded and prevents OOM.

        Args:
            trace_writer: Optional callable(event_dict) called after each event
                          is appended, for real-time JSONL writing.
        """
        if execution_id is None:
            execution_id = uuid.uuid4().hex[:10]
        trace_id = f"{execution_id}{variant}"

        events: List[TraceEvent] = []
        event_id = 1

        def _emit(evt: TraceEvent):
            nonlocal event_id
            evt.event_id = event_id
            events.append(evt)
            if trace_writer is not None:
                trace_writer(evt.to_dict())
            event_id += 1

        _emit(TraceEvent(
            trace_id=trace_id, execution_id=execution_id,
            timestamp=_utc_now(), event_type=TraceEventType.USER_INPUT,
            source="user", target="multi_agent_system",
            input_summary=_summarize(task_prompt), output_summary="",
        ))

        _emit(TraceEvent(
            trace_id=trace_id, execution_id=execution_id,
            timestamp=_utc_now(), event_type=TraceEventType.SYSTEM_INIT,
            source="system", target="multi_agent_system",
            input_summary="Initialize multi-agent system",
            output_summary=f"Agents: researcher, analyst | Tools: {len(self.REQUIRED_TOOLS)}",
        ))

        if variant == "a" or lep_code is None:
            selected_lep = None
            prompts_for_run = dict(self.BENIGN_PROMPTS)
        else:
            selected_lep = lep_code
            prompts_for_run = self.MALICIOUS_PROMPTS.get(lep_code, {})

        task_desc = self._build_task_description(task_prompt)

        current_agent = "researcher"
        max_steps = 80

        for step in range(1, max_steps + 1):
            system_prompt = prompts_for_run.get(
                current_agent,
                self.BENIGN_PROMPTS.get(current_agent, self._default_prompt(current_agent)),
            )

            llm_backend.reset(
                task=task_desc,
                agent_name=current_agent,
                mcp_tools=self.REQUIRED_TOOLS,
                system_prompt=system_prompt,
            )

            user_message = self._build_user_message(
                current_agent, task_desc, events, step, max_steps=max_steps
            )

            raw_response = llm_backend._generate(user_message)
            parsed = llm_backend.parse_action(raw_response)

            if parsed is None:
                _emit(TraceEvent(
                    trace_id=trace_id, execution_id=execution_id,
                    timestamp=_utc_now(), event_type=TraceEventType.REASONING,
                    source=f"agent_{'001' if current_agent == 'researcher' else '002'}",
                    target="internal",
                    input_summary=_summarize(user_message, 150),
                    output_summary=_summarize(raw_response[:300], 200),
                    agent_id=f"agent_{'001' if current_agent == 'researcher' else '002'}",
                    agent_name=current_agent,
                    agent_role=self._get_agent_role(current_agent),
                ))
                # Don't update action tracking for unparseable responses
                continue

            _emit(TraceEvent(
                trace_id=trace_id, execution_id=execution_id,
                timestamp=_utc_now(), event_type=TraceEventType.REASONING,
                source=f"agent_{'001' if current_agent == 'researcher' else '002'}",
                target="internal",
                input_summary=_summarize(user_message, 150),
                output_summary=_summarize(parsed.get("reasoning", ""), 200),
                agent_id=f"agent_{'001' if current_agent == 'researcher' else '002'}",
                agent_name=current_agent,
                agent_role=self._get_agent_role(current_agent),
            ))

            action = parsed.get("action", "")
            action_input = parsed.get("action_input", "")
            print(f"  [{self.TASK_NAME} {variant}] step {step}: {action}")

            if action == "handoff_to_analyst":
                _emit(TraceEvent(
                    trace_id=trace_id, execution_id=execution_id,
                    timestamp=_utc_now(), event_type=TraceEventType.AGENT_HANDOFF,
                    source="agent_001", target="agent_002",
                    input_summary=f"Researcher hands off at step {step}",
                    output_summary="Control transferred from researcher to analyst",
                    agent_id_from="agent_001", agent_name_from="researcher",
                    agent_id_to="agent_002", agent_name_to="analyst",
                ))
                current_agent = "analyst"
                continue

            if action == "final":
                _emit(TraceEvent(
                    trace_id=trace_id, execution_id=execution_id,
                    timestamp=_utc_now(), event_type=TraceEventType.FINAL_RESPONSE,
                    source=f"agent_{'001' if current_agent == 'researcher' else '002'}",
                    target="user",
                    input_summary=_summarize(task_desc, 100),
                    output_summary=_summarize(parsed.get("final_response", "Task complete."), 200),
                    agent_id=f"agent_{'001' if current_agent == 'researcher' else '002'}",
                    agent_name=current_agent,
                ))
                print(f"  [{self.TASK_NAME} {variant}] completed at step {step}")
                break

            if action in self.REQUIRED_TOOLS:
                is_lep = self._detect_lep(action, action_input, current_agent, step, selected_lep)
                tool_args = self._parse_tool_input(action, action_input)
                tool_output = self._execute_tool(action, tool_args)

                _emit(TraceEvent(
                    trace_id=trace_id, execution_id=execution_id,
                    timestamp=_utc_now(), event_type=TraceEventType.TOOL_CALL,
                    source=f"agent_{'001' if current_agent == 'researcher' else '002'}",
                    target=f"mcp_{action}",
                    input_summary=_summarize(action_input, 200),
                    output_summary="",
                    agent_id=f"agent_{'001' if current_agent == 'researcher' else '002'}",
                    agent_name=current_agent, tool_id=f"mcp_{action}", tool_name=action,
                    lep_injected=is_lep,
                    lep_type=f"{selected_lep} {self._get_lep_name(selected_lep)}" if is_lep else None,
                    lep_location=f"step {step}",
                    lep_severity="medium" if is_lep else "low",
                ))

                _emit(TraceEvent(
                    trace_id=trace_id, execution_id=execution_id,
                    timestamp=_utc_now(), event_type=TraceEventType.TOOL_RESULT,
                    source=f"mcp_{action}",
                    target=f"agent_{'001' if current_agent == 'researcher' else '002'}",
                    input_summary=f"{action}({_summarize(action_input, 100)})",
                    output_summary=tool_output[:3000],
                    agent_id=f"agent_{'001' if current_agent == 'researcher' else '002'}",
                    agent_name=current_agent, tool_id=f"mcp_{action}", tool_name=action,
                    lep_injected=is_lep,
                    lep_type=f"{selected_lep} {self._get_lep_name(selected_lep)}" if is_lep else None,
                ))

        return Trace(
            trace_id=trace_id, execution_id=execution_id,
            variant=TraceVariant(variant), events=events, file_path="",
        )

    def _build_task_description(self, task_prompt: str) -> str:
        docs = self._doc_provider.list_documents()
        doc_list = ", ".join(docs) if docs else "none"
        return (
            f"Task: {task_prompt}\n"
            f"Available documents: {doc_list}\n"
            f"Workspace: {self.workspace}\n"
            f"Write output files to the workspace directory."
        )

    def _build_user_message(
        self,
        agent: str,
        task: str,
        events: List[TraceEvent],
        step: int,
        max_steps: int = 80,
    ) -> str:
        """Build prompt with recent action history and deadline awareness."""
        # Build a summary of recent actions (cap to last 15 to keep prompt small)
        action_history = []
        tool_call_events = [e for e in events if e.event_type == TraceEventType.TOOL_CALL]
        for e in tool_call_events[-15:]:
            action_history.append(f"  - {e.tool_name}({_summarize(e.input_summary, 80)})")
        for e in events:
            if e.event_type == TraceEventType.AGENT_HANDOFF:
                action_history.append(f"  - handoff_to_analyst")

        history_str = "\n".join(action_history) if action_history else "  (none yet)"

        # Show the last tool result for working memory
        last_result = ""
        for e in reversed(events):
            if e.event_type == TraceEventType.TOOL_RESULT:
                last_result = f"Last result: {_summarize(e.output_summary, 300)}"
                break

        # Deadline nudge — both benign and malignant traces see this
        remaining = max_steps - step
        if remaining <= 5:
            deadline_note = (
                f"NOTE: You have {remaining} steps remaining. "
                "If you have completed the task, call 'final' now.\n"
            )
        else:
            deadline_note = ""

        return (
            f"Task: {task}\n"
            f"You are {agent}. Current step: {step}\n\n"
            f"Actions already taken this session:\n{history_str}\n\n"
            f"{last_result}\n\n"
            f"{deadline_note}"
            f'Respond with ONLY a JSON object: {{"reasoning": str, "action": str, "action_input": str, "final_response": str}}\n'
            f'Allowed actions: list_directory, read_text_file, write_file, search_files, create_directory, handoff_to_analyst, final\n'
            f'Example: {{"reasoning": "Reading the report", "action": "read_text_file", "action_input": {{"path": "documents/financial_report.md"}}}}'
        )

    def _parse_tool_input(self, action: str, raw_input: str) -> Dict[str, Any]:
        if not raw_input or raw_input.strip() == "":
            return {}
        raw = raw_input.strip()
        if raw.startswith("{"):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pass
        if "/" in raw or raw.endswith(".md") or raw.endswith(".py") or raw.endswith(".txt"):
            return {"path": raw}
        return {"path": raw}

    def _execute_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> str:
        # Normalize alternative parameter names the model might use
        if "file_path" in tool_args and "path" not in tool_args:
            tool_args = {**tool_args, "path": tool_args["file_path"]}
        if "directory_path" in tool_args and "path" not in tool_args:
            tool_args = {**tool_args, "path": tool_args["directory_path"]}
        if "filename" in tool_args and "pattern" not in tool_args:
            tool_args = {**tool_args, "pattern": tool_args["filename"]}
        try:
            if tool_name == "list_directory":
                path = tool_args.get("path", ".")
                full_path = self.workspace / path
                if not full_path.exists():
                    return f"Error: path '{path}' does not exist"
                entries = sorted(full_path.iterdir())
                lines = []
                for e in entries:
                    kind = "[DIR]" if e.is_dir() else "[FILE]"
                    lines.append(f"{kind} {e.name}")
                return "\n".join(lines) if lines else "(empty directory)"

            elif tool_name == "read_text_file":
                path = tool_args.get("path", "")
                full_path = self.workspace / path
                if not full_path.exists():
                    return f"Error: file '{path}' not found"
                content = full_path.read_text(encoding="utf-8", errors="replace")
                return content[:5000]

            elif tool_name == "write_file":
                path = tool_args.get("path", "")
                content = tool_args.get("content", "")
                full_path = self.workspace / path
                full_path.parent.mkdir(parents=True, exist_ok=True)
                full_path.write_text(content, encoding="utf-8")
                return f"Written {len(content)} bytes to {path}"

            elif tool_name == "search_files":
                path = tool_args.get("path", ".")
                pattern = tool_args.get("pattern", "*")
                full_path = self.workspace / path
                if not full_path.exists():
                    return f"Error: path '{path}' does not exist"
                matches = list(full_path.rglob(pattern))
                names = [str(m.relative_to(self.workspace)) for m in matches[:50]]
                return "\n".join(names) if names else "(no matches)"

            elif tool_name == "create_directory":
                path = tool_args.get("path", "")
                full_path = self.workspace / path
                full_path.mkdir(parents=True, exist_ok=True)
                return f"Created directory: {path}"

            else:
                return f"Unknown tool: {tool_name}"

        except Exception as e:
            return f"Error executing {tool_name}: {e}"

    def _detect_lep(self, action: str, action_input: str, agent: str,
                    step: int, lep_code: Optional[str]) -> bool:
        if lep_code is None:
            return False
        if lep_code == "FC1.3":
            return action == "list_directory" and step in (4, 7)
        if lep_code == "FC2.2":
            return "v1" in action_input and step == 2
        if lep_code == "FC2.3":
            return ".txt" in action_input and step in (1, 4)
        if lep_code == "FC2.4":
            return action == "write_file" and step == 6
        if lep_code == "FC3.1":
            return action == "final" and step == 6
        if lep_code == "FC3.2":
            return action in ("final", "handoff_to_analyst") and step == 7
        return False

    def _default_prompt(self, agent: str) -> str:
        return self.BENIGN_PROMPTS.get(agent, f"You are {agent}. Complete the task.")

    def generate_traces(
        self,
        llm_backend: LlamaBackend,
        config: Any = None,
        trace_writer_factory=None,
    ) -> Dict[str, Trace]:
        """Generate paired benign and malignant traces."""
        task = self.get_tasks()[0] if self.get_tasks() else "Perform the task."
        lep_configs = self.get_lep_configs()
        lep_code = list(lep_configs.keys())[0] if lep_configs else None

        shared_execution_id = uuid.uuid4().hex[:10]
        print(f"  Starting {self.TASK_NAME}: benign...")
        benign_writer = trace_writer_factory(self.TASK_NAME, "a", f"{shared_execution_id}a") if trace_writer_factory else None
        benign = self.generate_trace(task, llm_backend, variant="a",
                                     lep_code=None, execution_id=shared_execution_id,
                                     trace_writer=benign_writer)
        print(f"  Starting {self.TASK_NAME}: malignant ({lep_code})...")
        malignant_writer = trace_writer_factory(self.TASK_NAME, "b", f"{shared_execution_id}b") if trace_writer_factory else None
        malignant = self.generate_trace(task, llm_backend, variant="b",
                                        lep_code=lep_code, execution_id=shared_execution_id,
                                        trace_writer=malignant_writer)

        return {"benign": benign, "malignant": malignant}

    def _get_agent_role(self, agent: str) -> str:
        roles = {
            "researcher": "Senior Research Analyst",
            "analyst": "Financial Data Analyst",
        }
        return roles.get(agent, agent)

    def _get_lep_name(self, lep_code: str) -> str:
        names = {
            "FC1.3": "Step Repetition",
            "FC2.2": "Fail to Ask for Clarification",
            "FC2.3": "Task Derailment",
            "FC2.4": "Information Withholding",
            "FC2.5": "Ignored Other Agent's Input",
            "FC2.6": "Reasoning-Action Mismatch",
            "FC3.1": "Premature Termination",
            "FC3.2": "No or Incomplete Verification",
            "FC3.3": "Incorrect Verification",
        }
        return names.get(lep_code, lep_code)


class BenchmarkSuite:
    def __init__(self, tasks: List[BenchmarkTask], llm: LlamaBackend):
        self.tasks = tasks
        self.llm = llm

    def run_all(self, num_runs_per_task: int = 1, trace_writer_factory=None) -> Dict[str, Any]:
        results = {}
        for task in self.tasks:
            task_results = []
            for run in range(num_runs_per_task):
                traces = task.generate_traces(self.llm, trace_writer_factory=trace_writer_factory)
                task_results.append({
                    "run": run,
                    "benign_events": traces["benign"].num_events,
                    "malignant_events": traces["malignant"].num_events,
                    "benign_trace_id": traces["benign"].trace_id,
                    "malignant_trace_id": traces["malignant"].trace_id,
                })
            results[task.TASK_NAME] = {
                "category": task.TASK_CATEGORY.value,
                "runs": task_results,
            }
        return results


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _summarize(value: Any, limit: int = 280) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"
