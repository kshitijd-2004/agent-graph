#!/usr/bin/env python3
"""Minimal standalone test: API backend + trace generation, no torch needed.

Tests that Claude Sonnet produces valid structured actions and completes
the financial analysis task. Saves step-by-step output to stdout.
"""

# Mock torch and transformers so imports work without installing them
import json
import sys
import types
from pathlib import Path

for mod_name in ['torch', 'transformers', 'bitsandbytes', 'torch_geometric',
                 'torch_geometric.data', 'torch_geometric.loader']:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)

# Sub-modules
sys.modules['torch'].nn = types.ModuleType('torch.nn')
sys.modules['torch'].nn.functional = types.ModuleType('torch.nn.functional')
sys.modules['torch'].cuda = types.ModuleType('torch.cuda')
sys.modules['transformers'].AutoModelForCausalLM = None
sys.modules['transformers'].AutoTokenizer = None
sys.modules['transformers'].BitsAndBytesConfig = None

sys.path.insert(0, str(Path(__file__).parent / "src"))

from benchmarks.benchmark import (
    TraceEvent,
    TraceEventType,
    _utc_now,
    _summarize,
)
from benchmarks.api_backend import APIBackend
from benchmarks.tasks.financial import FinancialTask

WORKSPACE = Path("workspace")


def run_test():
    task = FinancialTask(WORKSPACE)
    prompts = task.get_tasks()
    task_prompt = prompts[0]

    llm = APIBackend(max_tokens=1024, temperature=0.1)

    task_desc = task._build_task_description(task_prompt)
    llm.reset(task=task_desc, agent_name="researcher",
              mcp_tools=task.REQUIRED_TOOLS,
              system_prompt=task.BENIGN_PROMPTS.get("researcher", ""))

    events = []
    event_id = 1
    trace_id = "test_api"
    tool_calls = 0
    completed = False

    # Open JSONL file for real-time writing
    trace_path = Path("traces") / "test_api_trace.jsonl"
    trace_path.parent.mkdir(exist_ok=True)
    trace_file = open(trace_path, "w", buffering=1)

    def _emit(evt):
        nonlocal event_id
        evt.event_id = event_id
        events.append(evt)
        trace_file.write(json.dumps(evt.to_dict()) + "\n")
        event_id += 1

    # Seed events
    for evt_data in [
        ("user_input", "user", "multi_agent_system", task_prompt[:100], ""),
        ("system_init", "system", "multi_agent_system",
         "Initialize multi-agent system",
         f"Agents: researcher, analyst | Tools: {len(task.REQUIRED_TOOLS)}"),
    ]:
        etype, src, tgt, inp, out = evt_data
        _emit(TraceEvent(
            trace_id=trace_id, execution_id="test",
            timestamp=_utc_now(), event_type=TraceEventType(etype),
            source=src, target=tgt,
            input_summary=inp, output_summary=out,
        ))

    max_steps = 80
    print(f"Task: {task_prompt}")
    print(f"Model: {llm.model}")
    print(f"Max steps: {max_steps}")
    print(f"Trace file: {trace_path} (real-time JSONL)\n")
    print(f"{'Step':<6} {'Action':<25} {'Input':<50} {'Reasoning'}")
    print("-" * 130)

    for step in range(1, max_steps + 1):
        user_message = task._build_user_message(
            "researcher", task_desc, events, step, max_steps=max_steps
        )

        try:
            raw_response = llm._generate(user_message)
        except Exception as e:
            print(f"  step {step}: [API ERROR] {e}")
            break

        parsed = llm.parse_action(raw_response)

        if parsed is None:
            print(f"  step {step}: [NO PARSE] {raw_response[:100]!r}")
            _emit(TraceEvent(
                trace_id=trace_id, execution_id="test",
                timestamp=_utc_now(), event_type=TraceEventType.REASONING,
                source="agent_001", target="internal",
                input_summary=_summarize(user_message, 120),
                output_summary=_summarize(raw_response[:300], 200),
                agent_id="agent_001", agent_name="researcher",
                agent_role="Senior Research Analyst",
            ))
            continue

        action = parsed["action"]
        action_input = parsed.get("action_input", "")
        reasoning = parsed.get("reasoning", "")[:80]

        print(f"  {step:<5} {action:<25} {_summarize(str(action_input), 48):<50} {reasoning}")

        _emit(TraceEvent(
            trace_id=trace_id, execution_id="test",
            timestamp=_utc_now(), event_type=TraceEventType.REASONING,
            source="agent_001", target="internal",
            input_summary=_summarize(user_message, 120),
            output_summary=_summarize(parsed.get("reasoning", ""), 200),
            agent_id="agent_001", agent_name="researcher",
            agent_role="Senior Research Analyst",
        ))

        if action == "final":
            _emit(TraceEvent(
                trace_id=trace_id, execution_id="test",
                timestamp=_utc_now(), event_type=TraceEventType.FINAL_RESPONSE,
                source="agent_001", target="user",
                input_summary=_summarize(task_desc, 100),
                output_summary=_summarize(parsed.get("final_response", "Done"), 200),
                agent_id="agent_001", agent_name="researcher",
            ))
            completed = True
            break

        if action in task.REQUIRED_TOOLS:
            tool_args = task._parse_tool_input(action, action_input)
            tool_output = task._execute_tool(action, tool_args)
            tool_calls += 1

            _emit(TraceEvent(
                trace_id=trace_id, execution_id="test",
                timestamp=_utc_now(), event_type=TraceEventType.TOOL_CALL,
                source="agent_001", target=f"mcp_{action}",
                input_summary=_summarize(str(action_input), 200), output_summary="",
                agent_id="agent_001", agent_name="researcher",
                tool_id=f"mcp_{action}", tool_name=action,
            ))

            _emit(TraceEvent(
                trace_id=trace_id, execution_id="test",
                timestamp=_utc_now(), event_type=TraceEventType.TOOL_RESULT,
                source=f"mcp_{action}", target="agent_001",
                input_summary=f"{action}({_summarize(str(action_input), 80)})",
                output_summary=tool_output[:2000],
                agent_id="agent_001", agent_name="researcher",
                tool_id=f"mcp_{action}", tool_name=action,
            ))

    # Summary
    total_events = len(events)
    action_counts = {}
    for e in events:
        if e.event_type == TraceEventType.TOOL_CALL:
            action_counts[e.tool_name] = action_counts.get(e.tool_name, 0) + 1

    print(f"\n{'=' * 60}")
    print(f"Completed: {'YES' if completed else 'NO'} at step {step}")
    print(f"Total events: {total_events}")
    print(f"Tool calls: {tool_calls}")
    print(f"Action distribution: {dict(sorted(action_counts.items(), key=lambda x: -x[1]))}")
    print(f"{'=' * 60}")

    trace_file.close()
    print(f"Trace saved (real-time JSONL): {trace_path}")

    return total_events, completed


if __name__ == "__main__":
    n, done = run_test()
    if not done:
        print(f"\nTrace did NOT complete — hit max_steps at {n} events")
    elif n < 90:
        print(f"\nWARNING: trace is short ({n} events) — may not be enough for training")
    else:
        print(f"\nGood: trace completed with {n} events (target: 90-120)")
