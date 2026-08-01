"""Main entry point for generating agent traces with environmental LEP injection.

Generates paired benign and malignant traces for each task. Both variants
use the same prompt and same base workspace. The malignant variant has
one additional poisoned artifact injected into the environment.

Usage:
    python run_traces.py --tasks all --runs 3
    python run_traces.py --tasks financial_analysis research --runs 1
"""

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from collections import Counter

# Add path for imports
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agentgraph import (
    EntityGraphBuilder,
    ExportManager,
    TraceVariant,
)
from trace_events import Trace, TraceEvent, TraceEventType
from backend.api_backend import APIBackend
from environment.workspace import Workspace
from memory.memory_store import MemoryStore, MemoryRecord
from memory.retriever import MemoryRetriever
from tasks.base_task import BaseTask, LEPConfig
from tasks.code_review import CodeReviewTask
from tasks.competitive_intelligence import CompetitiveIntelligenceTask
from tasks.financial import FinancialTask
from tasks.research import ResearchTask


# ── Paths ──────────────────────────────────────────────────────────────────

V2_DIR = Path(__file__).parent
WORKSPACE_BASE = V2_DIR / "workspace_base"
TRACE_DIR = V2_DIR / "traces"
OUTPUT_DIR = V2_DIR / "output"


def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _summarize(value: Any, limit: int = 280) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


# ── Task registry ────────────────────────────────────────────────────────────

ALL_TASKS: Dict[str, BaseTask] = {
    "financial_analysis": FinancialTask(WORKSPACE_BASE),
    "code_review": CodeReviewTask(WORKSPACE_BASE),
    "research": ResearchTask(WORKSPACE_BASE),
    "competitive_intelligence": CompetitiveIntelligenceTask(WORKSPACE_BASE),
}


# ── Trace generation ─────────────────────────────────────────────────────────

def generate_trace(
    task: BaseTask,
    task_prompt: str,
    llm: APIBackend,
    lep_code: Optional[str],
    run_idx: int,
    variant: str,
) -> Trace:
    """Generate a single trace.

    When lep_code is None, produces a clean benign trace.
    When lep_code is set, injects the LEP into the workspace first.
    """
    exec_id = uuid.uuid4().hex[:10]

    # ── Clean stale workspaces from crashed runs ─────────────────────────
    import glob
    for stale in V2_DIR.glob(f"_ws_{task.TASK_NAME}_*"):
        import shutil
        shutil.rmtree(stale, ignore_errors=True)

    # ── Streaming writer ─────────────────────────────────────────────────
    trace_path = TRACE_DIR / task.TASK_NAME / f"{task.TASK_NAME}_{variant}_{exec_id}.jsonl"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(trace_path, "w", buffering=1)

    def writer(evt_dict):
        fh.write(json.dumps(evt_dict) + "\n")

    # ── Workspace ────────────────────────────────────────────────────────
    ws_label = f"{variant}_r{run_idx}"
    workspace = Workspace(V2_DIR / f"_ws_{ws_label}")
    memory = MemoryStore()
    retriever = MemoryRetriever(memory, top_k=3)

    try:
        task.setup_workspace(workspace)
        if lep_code:
            task.inject_lep(workspace, memory, lep_code)
        memory.seed_from_workspace(workspace.root, workspace.execute)
        trace = _run_trace(
            task=task, task_prompt=task_prompt, llm=llm,
            workspace=workspace, memory=memory, retriever=retriever,
            variant=variant, execution_id=exec_id,
            lep_code=lep_code, trace_writer=writer,
        )
        if lep_code:
            task.cleanup_lep(workspace, memory, lep_code)
    finally:
        fh.close()
        if workspace.root.exists():
            import shutil
            shutil.rmtree(workspace.root)

    return trace, trace_path


def _run_trace(
    task: BaseTask,
    task_prompt: str,
    llm: APIBackend,
    workspace: Workspace,
    memory: MemoryStore,
    retriever: MemoryRetriever,
    variant: str,
    execution_id: str,
    lep_code: Optional[str],
    trace_writer=None,
) -> Trace:
    """Run a single trace with memory-augmented agent loop."""
    trace_id = f"{execution_id}{variant}"
    events: List[TraceEvent] = []
    event_id = 1
    current_agent = "researcher"
    max_steps = 80
    step = 0

    # Seed memory with workspace files for context
    memory.seed_from_workspace(workspace.root, workspace.execute)

    def _emit(evt: TraceEvent):
        nonlocal event_id
        evt.event_id = event_id
        events.append(evt)
        if trace_writer is not None:
            trace_writer(evt.to_dict())
        event_id += 1

    # User input
    _emit(TraceEvent(
        trace_id=trace_id, execution_id=execution_id,
        timestamp=_utc_now(), event_type=TraceEventType.USER_INPUT,
        source="user", target="multi_agent_system",
        input_summary=_summarize(task_prompt), output_summary="",
    ))

    # System init
    _emit(TraceEvent(
        trace_id=trace_id, execution_id=execution_id,
        timestamp=_utc_now(), event_type=TraceEventType.SYSTEM_INIT,
        source="system", target="multi_agent_system",
        input_summary="Initialize multi-agent system",
        output_summary=f"Agents: researcher, analyst | Tools: {len(task.REQUIRED_TOOLS)}",
    ))

    # Agent loop
    tool_history = []  # track all tool calls for behavioral LEP detection
    for step in range(1, max_steps + 1):
        system_prompt = task.get_prompt(current_agent)

        # Retrieve relevant memories
        memory_context = retriever.get_context(task_prompt)
        full_prompt = _build_user_message(
            current_agent, task_prompt, events, step, memory_context, max_steps
        )

        llm.reset(
            task=task_prompt,
            agent_name=current_agent,
            mcp_tools=task.REQUIRED_TOOLS,
            system_prompt=system_prompt,
        )

        raw_response = llm._generate(full_prompt)
        parsed = llm.parse_action(raw_response)

        # Emit reasoning event
        _emit(TraceEvent(
            trace_id=trace_id, execution_id=execution_id,
            timestamp=_utc_now(), event_type=TraceEventType.REASONING,
            source=f"agent_{'001' if current_agent == 'researcher' else '002'}",
            target="internal",
            input_summary=_summarize(full_prompt, 150),
            output_summary=_summarize(raw_response[:300], 200),
            agent_id=f"agent_{'001' if current_agent == 'researcher' else '002'}",
            agent_name=current_agent,
            agent_role=current_agent,
        ))

        if parsed is None:
            continue

        action = parsed.get("action", "")
        action_input = parsed.get("action_input", "")

        # Emit memory retrieval event
        if memory_context:
            _emit(TraceEvent(
                trace_id=trace_id, execution_id=execution_id,
                timestamp=_utc_now(), event_type=TraceEventType.MEMORY_RETRIEVAL,
                source="memory_store", target=f"agent_{'001' if current_agent == 'researcher' else '002'}",
                input_summary=_summarize(task_prompt, 100),
                output_summary=_summarize(memory_context, 200),
            ))

        # Handle handoff
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
            # Reset memory for new agent (or keep shared — configurable)
            continue

        # Handle final
        if action == "final":
            _emit(TraceEvent(
                trace_id=trace_id, execution_id=execution_id,
                timestamp=_utc_now(), event_type=TraceEventType.FINAL_RESPONSE,
                source=f"agent_{'001' if current_agent == 'researcher' else '002'}",
                target="user",
                input_summary=_summarize(task_prompt, 100),
                output_summary=_summarize(parsed.get("final_response", "Task complete."), 200),
                agent_id=f"agent_{'001' if current_agent == 'researcher' else '002'}",
                agent_name=current_agent,
            ))
            break

        # Execute tool
        if action in task.REQUIRED_TOOLS:
            tool_args = _parse_tool_input(action, action_input)
            tool_output = workspace.execute(action, tool_args)

            # Layer 1: LEP detection via behavioral patterns
            is_lep = False
            if lep_code:
                is_lep = _detect_lep(
                    action, action_input, current_agent, step,
                    lep_code, tool_history, V2_DIR
                )

            tool_history.append({"action": action, "action_input": action_input, "step": step})

            _emit(TraceEvent(
                trace_id=trace_id, execution_id=execution_id,
                timestamp=_utc_now(), event_type=TraceEventType.TOOL_CALL,
                source=f"agent_{'001' if current_agent == 'researcher' else '002'}",
                target=f"mcp_{action}",
                input_summary=_summarize(action_input, 200),
                output_summary="",
                agent_id=f"agent_{'001' if current_agent == 'researcher' else '002'}",
                agent_name=current_agent,
                tool_id=f"mcp_{action}", tool_name=action,
                lep_injected=is_lep,
                lep_type=f"{lep_code}" if is_lep else None,
                lep_location=f"step {step}",
                lep_severity="medium" if is_lep else "low",
            ))

            _emit(TraceEvent(
                trace_id=trace_id, execution_id=execution_id,
                timestamp=_utc_now(), event_type=TraceEventType.TOOL_RESULT,
                source=f"mcp_{action}",
                target=f"agent_{'001' if current_agent == 'researcher' else '002'}",
                input_summary=f"{action}({_summarize(action_input, 100)})",
                output_summary=_summarize(tool_output[:3000], 300),
                agent_id=f"agent_{'001' if current_agent == 'researcher' else '002'}",
                agent_name=current_agent, tool_id=f"mcp_{action}", tool_name=action,
                lep_injected=is_lep,
            ))

            # Layer 2: Deterministic task-completion check
            # If the agent just wrote to a required output file and it has
            # real content, terminate automatically rather than letting it
            # re-enter the agent loop.
            if not is_lep and action == "write_file":
                written_path = tool_args.get("path", "")
                full_path = workspace.root / written_path
                if full_path.exists() and full_path.stat().st_size > 100:
                    print(f"  [DETERMINISTIC] Task complete — output written: {written_path}")
                    _emit(TraceEvent(
                        trace_id=trace_id, execution_id=execution_id,
                        timestamp=_utc_now(), event_type=TraceEventType.FINAL_RESPONSE,
                        source=f"agent_{'001' if current_agent == 'researcher' else '002'}",
                        target="user",
                        input_summary=f"Output written to {written_path}",
                        output_summary=_summarize(tool_output[:200], 200),
                        agent_id=f"agent_{'001' if current_agent == 'researcher' else '002'}",
                        agent_name=current_agent,
                    ))
                    break

    return Trace(
        trace_id=trace_id, execution_id=execution_id,
        variant=TraceVariant(variant), events=events, file_path="",
    )


def _build_user_message(
    agent: str,
    task: str,
    events: List[TraceEvent],
    step: int,
    memory_context: str = "",
    max_steps: int = 80,
) -> str:
    """Build prompt that preserves working state across steps and detects loops."""
    tool_call_events = [e for e in events if e.event_type == TraceEventType.TOOL_CALL]
    tool_result_events = {e.event_id: e for e in events if e.event_type == TraceEventType.TOOL_RESULT}

    # === Build working state ===
    write_attempts = 0
    last_write_step = 0

    for e in tool_call_events:
        name = e.tool_name or ""
        inp = e.input_summary or ""

        if name == "write_file":
            write_attempts += 1
            last_write_step = e.step if hasattr(e, 'step') else step

    # === Collect data for state display ===
    all_files = set()
    reviewed_set = set()
    for e in tool_call_events:
        name = e.tool_name or ""
        inp = (e.input_summary or "").strip().strip('"')

        if name == "list_directory":
            result_event = tool_result_events.get(e.event_id)
            if result_event:
                output = result_event.output_summary or ""
                for line in output.split("\n"):
                    line = line.strip()
                    if line and not line.startswith("Total") and not line.startswith("---"):
                        all_files.add(line)
        elif name == "read_text_file":
            reviewed_set.add(inp)

    # === Loop detection ===
    dir_counts = Counter(e.input_summary or "" for e in tool_call_events if (e.tool_name or "") == "list_directory")
    read_counts = Counter(e.input_summary or "" for e in tool_call_events if (e.tool_name or "") == "read_text_file")

    loop_warnings = []
    for d, cnt in dir_counts.items():
        if cnt > 2:
            loop_warnings.append(f"WARNING: You have listed '{d}' {cnt} times. You already know its contents.")
    for p, cnt in read_counts.items():
        if cnt > 2:
            loop_warnings.append(f"WARNING: You have read '{p}' {cnt} times. File contents do not change — use what you already know.")

    in_loop = len(loop_warnings) > 0

    # === Working state summary ===
    state_lines = []
    if all_files:
        state_lines.append(f"  Files discovered: {', '.join(sorted(all_files))}")
    if reviewed_set:
        state_lines.append(f"  Files reviewed: {', '.join(sorted(reviewed_set))}")
    if write_attempts > 0:
        state_lines.append(f"  Write attempts: {write_attempts} (last at step {last_write_step})")

    # Is the task effectively done?
    has_files = bool(all_files) or bool(reviewed_set)
    task_done = has_files and write_attempts > 0 and not in_loop

    state_str = "\n".join(state_lines) if state_lines else "  (nothing done yet)"

    # === Last meaningful tool result ===
    last_result = ""
    for e in reversed(events):
        if e.event_type == TraceEventType.TOOL_RESULT:
            last_result = f"Last result: {_summarize(e.output_summary, 300)}"
            break

    # === Deadline awareness ===
    remaining = max_steps - step
    if task_done:
        deadline_note = (
            f"STOP. You have already read the source files and attempted to write the output. "
            f"Call 'final' now with your findings. Do NOT read files again.\n\n"
        )
    elif in_loop and remaining <= 10:
        deadline_note = (
            f"CRITICAL LOOP DETECTED. You are repeating the same actions. "
            f"{' '.join(loop_warnings)} "
            f"You have {remaining} steps left. STOP reading and call 'final' now.\n\n"
        )
    elif remaining <= 10:
        deadline_note = (
            f"CRITICAL: {remaining} steps remaining. "
            "If you have completed the task, call 'final' now with a final_response. "
            "Do NOT continue reading the same file repeatedly.\n\n"
        )
    elif remaining <= 20:
        deadline_note = (
            f"WARNING: {remaining} steps remaining. "
            "If you have enough information, write your output and call 'final'.\n\n"
        )
    else:
        deadline_note = ""

    prompt = (
        f"Task: {task}\n"
        f"You are {agent}. Current step: {step}\n\n"
        f"WORKING STATE (this persists across all steps — do NOT redo completed work):\n"
        f"{state_str}\n\n"
        f"{last_result}\n\n"
        f"{deadline_note}"
    )

    if memory_context:
        prompt += f"{memory_context}\n\n"

    if loop_warnings and not task_done:
        prompt += f"{' '.join(loop_warnings)}\n\n"

    prompt += (
        f'IMPORTANT: Respond with ONLY a single JSON object. No extra text.\n'
        f'Format: {{"reasoning": "...", "action": "tool_name", "action_input": {{"key": "value"}}, "final_response": "..."}}\n'
        f'Allowed actions: list_directory, read_text_file, write_file, search_files, create_directory, handoff_to_analyst, final\n'
        f'Example: {{"reasoning": "Writing report", "action": "write_file", "action_input": {{"path": "output/report.md", "content": "report text here"}}}}\n'
        f'To finish: {{"reasoning": "done", "action": "final", "action_input": {{}}, "final_response": "your findings"}}\n'
        f'CRITICAL: When action is "write_file", you MUST include "content" with the actual file text. '
        f'Never call write_file with only a path — include the full report content inline.'
    )
    return prompt


def _parse_tool_input(action: str, raw_input: str) -> Dict[str, Any]:
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


def _detect_lep(
    action: str, action_input: str, agent: str, step: int,
    lep_code: Optional[str],
    tool_history: Optional[list] = None,
    workspace_path: Optional[Path] = None,
) -> bool:
    """Check if this tool call exhibits a LEP pattern.

    Detection is behavioral, not step-based:
    - FC1.3: same tool called 3+ times consecutively with similar input
    - FC3.1: final called before reading any source files (premature)
    - FC2.3: agent reads a file that wasn't part of the base workspace
    - FC2.5: analyst reads from handoff/ directory instead of waiting
    """
    if lep_code is None:
        return False

    if lep_code == "FC1.3":
        # Detect repetition: same tool + same input called 3+ times in a row
        if tool_history and len(tool_history) >= 3:
            last_three = tool_history[-3:]
            if all(t["action"] == action and t["action_input"] == action_input
                   for t in last_three):
                return True
        return False

    if lep_code == "FC3.1":
        # Premature termination with incorrect output: agent writes output
        # containing figures from poisoned memory instead of reading source files.
        # Detect if the write_file action_input contains the wrong figures.
        if action == "write_file":
            input_str = action_input if isinstance(action_input, str) else str(action_input)
            # Check if the content contains poisoned figures
            poisoned_figures = ["$1,200,000", "$850,000", "$350,000", "29% margin"]
            has_poisoned = any(fig in input_str for fig in poisoned_figures)
            if has_poisoned:
                return True
        return False

    if lep_code == "FC2.3":
        # Task derailment: agent reads a file that wasn't in the base workspace
        if workspace_path and action == "read_text_file":
            input_str = action_input if isinstance(action_input, str) else str(action_input)
            # Extract path from input
            try:
                args = json.loads(input_str) if input_str.startswith("{") else {"path": input_str}
                path = args.get("path", "")
            except (json.JSONDecodeError, TypeError):
                path = str(action_input)
            # Check if this file exists in the base workspace
            base_path = workspace_path / "workspace_base" / path
            if not base_path.exists() and path:
                return True
        return False

    if lep_code == "FC2.5":
        # Ignored handoff: analyst reads from handoff/ directory
        if action == "read_text_file" and agent == "analyst":
            input_str = action_input if isinstance(action_input, str) else str(action_input)
            if "handoff/" in input_str:
                return True
        return False

    return False


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate agent traces with environmental LEP injection")
    parser.add_argument("--tasks", nargs="+", default=["all"],
                        help="Tasks to run (or 'all')")
    parser.add_argument("--runs", type=int, default=1,
                        help="Number of repetitions per LEP (1 benign is shared across all)")
    parser.add_argument("--lep", type=str, default=None,
                        help="Specific LEP code to inject (default: first available)")
    parser.add_argument("--all-leps", action="store_true",
                        help="Run all LEPs for each task (default: only first LEP)")
    args = parser.parse_args()

    # Resolve tasks
    if "all" in args.tasks:
        tasks = list(ALL_TASKS.values())
    else:
        tasks = [ALL_TASKS[t] for t in args.tasks if t in ALL_TASKS]

    # Setup LLM backend
    api_key = os.environ.get("LLM_API_KEY", "")
    if not api_key:
        print("ERROR: Set LLM_API_KEY environment variable")
        sys.exit(1)

    llm = APIBackend(api_key=api_key)

    # Create output directories
    TRACE_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)

    all_results = {}

    for task in tasks:
        task_trace_dir = TRACE_DIR / task.TASK_NAME
        task_trace_dir.mkdir(exist_ok=True)
        task_output_dir = OUTPUT_DIR / task.TASK_NAME
        task_output_dir.mkdir(exist_ok=True)

        task_results = []
        lep_configs = task.get_lep_configs()

        # Determine which LEPs to run
        if args.lep:
            lep_codes = [args.lep]
        elif args.all_leps:
            lep_codes = list(lep_configs.keys())
        else:
            lep_codes = [list(lep_configs.keys())[0]]

        run_idx = 0

        # ── One benign trace per task ──────────────────────────────────────
        print(f"\n=== {task.TASK_NAME}: generating benign baseline ===")
        benign, benign_path = generate_trace(
            task=task, task_prompt=task.get_tasks()[0], llm=llm,
            lep_code=None, run_idx=run_idx, variant="a",
        )
        print(f"  Benign: {benign_path} ({benign.num_events} events)")

        # Build benign graph once
        builder = EntityGraphBuilder()
        benign_graph = builder.build(benign)
        print(f"  Benign graph: {benign_graph.num_nodes} nodes, {benign_graph.num_edges} edges")

        # Export benign graph
        exporter = ExportManager(OUTPUT_DIR)
        benign_csv = exporter.export_dyglib_dataset(
            [benign_graph], f"{task.TASK_NAME}_benign"
        )
        print(f"  Exported benign: {benign_csv}")

        # ── One malignant trace per LEP ────────────────────────────────────
        for lep_code in lep_codes:
            print(f"\n=== {task.TASK_NAME}: generating malignant (LEP: {lep_code}) ===")

            malignant, malignant_path = generate_trace(
                task=task, task_prompt=task.get_tasks()[0], llm=llm,
                lep_code=lep_code, run_idx=run_idx, variant="b",
            )
            print(f"  Malignant: {malignant_path} ({malignant.num_events} events)")

            # Build malignant graph
            malignant_graph = builder.build(malignant)
            print(f"  Malignant graph: {malignant_graph.num_nodes} nodes, {malignant_graph.num_edges} edges")

            # Export malignant graph
            malignant_csv = exporter.export_dyglib_dataset(
                [malignant_graph], f"{task.TASK_NAME}_{lep_code}"
            )
            print(f"  Exported malignant: {malignant_csv}")

            task_results.append({
                "run": run_idx,
                "lep_code": lep_code,
                "benign_events": benign.num_events,
                "malignant_events": malignant.num_events,
                "benign_trace_id": benign.trace_id,
                "malignant_trace_id": malignant.trace_id,
                "benign_graph_nodes": benign_graph.num_nodes,
                "benign_graph_edges": benign_graph.num_edges,
                "malignant_graph_nodes": malignant_graph.num_nodes,
                "malignant_graph_edges": malignant_graph.num_edges,
            })
            run_idx += 1

        all_results[task.TASK_NAME] = {
            "category": task.TASK_CATEGORY,
            "lep_codes": lep_codes,
            "runs": task_results,
        }

    # Save benchmark results
    results_path = OUTPUT_DIR / "benchmark_results.json"
    with open(results_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nResults saved to {results_path}")


if __name__ == "__main__":
    main()
