"""Bounded agent-stage execution engine with stage-local message history.

Each stage runs one agent for a bounded number of turns. The stage can
terminate via: handoff, final, loop detection, or max-turn exhaustion.

Key design:
- Backend.reset() is called ONCE at stage start, not per turn.
- A stage-local message list (stage_history) is maintained for the active
  agent, containing: system prompt, task, prior reasoning/action, tool call,
  and full tool results.
- Each new turn appends to stage_history, and the next API request is built
  from the same persistent history.
- On handoff, the receiving agent gets a FRESH history seeded with the
  handoff payload — the source agent's raw history is never reused.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from schemas import (
    LEPConfig, Trace, TraceEvent, TraceEventType, TraceVariant,
    WorkflowConfig,
)
from schemas.scenario import ScenarioSpec
from generation.handoff import HandoffPayload
from generation.topology import TopologyConfig, Stage, HandoffRule
from backend.api_backend import ToolCall, ModelTurn

logger = logging.getLogger(__name__)


@dataclass
class StageResult:
    """Result of executing one agent stage."""
    stage_id: str
    events: List[TraceEvent]
    termination_reason: str  # "handoff" | "final" | "max_turns" | "loop" | "timeout"
    handoff_payload: Optional[HandoffPayload] = None
    final_agent_role: str = ""
    raw_output: str = ""
    step_count: int = 0
    raw_prompts: List[str] = field(default_factory=list)  # debug-only, not in canonical trace
    protocol_violation_data: Optional[Dict[str, Any]] = None  # raw model text + context


class StageRunner:
    """Executes a single bounded agent stage with persistent message history.

    Responsibilities:
    - Run the assigned agent for up to stage.max_turns steps
    - Maintain a stage-local message history (not reset between turns)
    - Call backend.reset() exactly once at stage start
    - Emit REASONING, TOOL_CALL, TOOL_RESULT, AGENT_HANDOFF, FINAL_RESPONSE events
    - On handoff: build structured HandoffPayload and return it
    - On final (only if stage.can_finalize): emit FINAL_RESPONSE
    - Detect loops and terminate
    """

    # Actions that count as "meaningful" for the post-handoff requirement
    MEANINGFUL_ACTIONS = frozenset({
        "read_text_file", "write_file", "search_files",
        "list_directory", "create_directory",
    })

    # Actions that advance the task
    TASK_ACTIONS = frozenset({
        "read_text_file", "write_file", "search_files",
        "list_directory", "create_directory",
    })

    # Native tool actions — acceptable for tool_choice="any"
    # handoff and submit_final are also valid native tools but are
    # phase-terminating and handled separately.
    NATIVE_TOOL_ACTIONS = frozenset({
        "list_directory", "read_text_file", "write_file",
        "search_files", "create_directory",
        "handoff", "submit_final",
    })

    # Retry nudge sent when the model returns text instead of a tool call
    TOOL_CALL_NUDGE = (
        "Call exactly one provided tool. "
        "Do not describe or print the call."
    )

    def __init__(self, llm_backend, evaluator=None):
        self.llm = llm_backend
        self.evaluator = evaluator

    def run_stage(
        self,
        stage: Stage,
        topology: TopologyConfig,
        handoff_rule: Optional[HandoffRule],
        scenario: ScenarioSpec,
        ws_path,
        task_prompt: str,
        prior_events: List[TraceEvent],
        handoff_from_payload: Optional[HandoffPayload] = None,
        lep_orchestrator=None,
        lep_corrupted_values: Optional[Dict[str, Any]] = None,
        global_event_counter: Optional[List[int]] = None,
    ) -> StageResult:
        """Execute one agent stage.

        Args:
            stage: The stage definition (agent role, max turns, etc.)
            topology: Full topology config (for context)
            handoff_rule: The rule governing handoff INTO this stage (if any)
            scenario: The full scenario spec
            ws_path: Workspace directory path
            task_prompt: Original task description
            prior_events: Events emitted by prior stages (for provenance)
            handoff_from_payload: Structured payload from the prior agent (if receiving)
            lep_orchestrator: Active LEP orchestrator (or None for benign)
            lep_corrupted_values: Accumulated corrupted values
            global_event_counter: Shared counter [n] for globally unique event IDs/indexes

        Returns:
            StageResult with events and termination info
        """
        events: List[TraceEvent] = []
        # stage_event_counter is per-stage local (resets each stage).
        # Only used for stage_event_index — the canonical event_index comes
        # from the shared global counter.
        stage_event_counter = [0]
        max_turns = stage.max_turns
        current_role = stage.agent_role
        agent_id = stage.agent_id

        # Use shared global counter for globally unique event IDs/indexes
        # (passed from runner.py). If absent (standalone test), create a local one.
        global_counter = global_event_counter if global_event_counter is not None else [0]

        # Build the agent map for make_evt
        agent_map = {s.agent_role: s.agent_id for s in topology.stages}
        # Ensure all stages are in the map even if roles overlap
        for s in topology.stages:
            agent_map.setdefault(s.agent_role, s.agent_id)

        def make_evt(event_type: TraceEventType, source: str, target: str,
                     role: str = "", tool_name: Optional[str] = None,
                     input_text: Optional[str] = None, output_text: Optional[str] = None,
                     tool_arguments: Optional[Dict[str, Any]] = None,
                     tool_result: Optional[str] = None,
                     **kw) -> TraceEvent:
            global_counter[0] += 1
            stage_event_counter[0] += 1
            idx = global_counter[0] - 1
            now = datetime.now(timezone.utc).isoformat()
            return TraceEvent(
                trace_id="",  # set by caller
                event_id=str(idx),                      # compatibility alias (matches runner.py)
                event_index=idx,                         # canonical identifier
                stage_event_index=stage_event_counter[0] - 1,
                timestamp=now,
                event_type=event_type,
                source_entity_id=source,
                target_entity_id=target,
                agent_id=agent_map.get(role or current_role, agent_id),
                agent_role=role or current_role,
                tool_name=tool_name,
                tool_arguments=tool_arguments,
                tool_result=tool_result,
                input_text=input_text,
                output_text=output_text,
                **kw,
            )

        def label_injection(evt: TraceEvent, lep_code: str):
            evt.event_labels.is_injection_origin = True
            evt.event_labels.controlled_injection = True
            evt.hidden["lep_type"] = lep_code
            evt.hidden["injected"] = True

        def label_consumption(evt: TraceEvent, lep_code: str):
            evt.event_labels.consumes_perturbed_info = True
            evt.hidden["lep_type"] = lep_code
            evt.hidden["consumed"] = True

        def label_propagation(evt: TraceEvent, lep_code: str):
            evt.event_labels.forwards_perturbed_info = True
            evt.hidden["lep_type"] = lep_code

        def label_failure(evt: TraceEvent, failure_type: str = "factual_error"):
            evt.event_labels.introduces_downstream_failure = True
            evt.event_labels.failure_type = failure_type

        # ── Stage initialization ─────────────────────────────────────────────

        # Build role-specific system prompt BEFORE resetting the backend
        # so the backend's native _messages uses the correct prompt.
        system_prompt = self._build_system_prompt(stage, handoff_from_payload)

        # Build tool list: include handoff/submit_final only if the stage
        # is allowed to use them (prevents premature use).
        available_tools = [
            "list_directory", "read_text_file", "write_file",
            "search_files", "create_directory",
        ]
        if stage.can_handoff:
            available_tools.append("handoff")
        if stage.can_finalize:
            available_tools.append("submit_final")

        # Reset backend ONCE per stage (not per turn)
        self.llm.reset(
            task=task_prompt,
            agent_name=current_role,
            mcp_tools=available_tools,
            system_prompt=system_prompt,
        )

        # Determine LEP code
        lep_code = "benign"
        if scenario is not None and getattr(scenario, 'lep_configs', None):
            lep_code = scenario.lep_configs[0].code

        # If dry-run backend, set LEP context
        if hasattr(self.llm, 'set_context') and lep_orchestrator:
            self.llm.set_context(
                lep_code=lep_code,
                task_family=scenario.task_family,
                lep_corrupted_values=lep_corrupted_values or {},
            )

        # ── Stage-local message history ──────────────────────────────────────
        # This list is the persistent conversation for this agent stage.
        # It is NOT rebuilt from global events each turn.
        # On handoff, the receiving agent gets a FRESH history.
        stage_history: List[Dict[str, Any]] = []

        # Seed history with system + task
        system_prompt = self._build_system_prompt(stage, handoff_from_payload)
        stage_history.append({
            "role": "system",
            "content": system_prompt,
        })
        stage_history.append({
            "role": "user",
            "content": f"Task: {task_prompt}\nYou are {current_role}. Complete the assigned task.",
        })

        # If receiving a handoff, add handoff context to history
        handoff_received = False
        if handoff_from_payload:
            handoff_received = True
            handoff_content = (
                f"[Handoff received from {handoff_from_payload.from_agent}]\n"
                f"Summary: {handoff_from_payload.summary}\n"
            )
            if handoff_from_payload.findings:
                handoff_content += f"Key findings: {'; '.join(handoff_from_payload.findings[:5])}\n"
            if handoff_from_payload.source_paths:
                handoff_content += f"Source paths: {', '.join(handoff_from_payload.source_paths[:5])}\n"
            if handoff_from_payload.report_path:
                handoff_content += f"Report path: {handoff_from_payload.report_path}\n"
            stage_history.append({
                "role": "user",
                "content": handoff_content,
            })

        logger.info(
            "StageRunner.run_stage START: stage=%s role=%s agent_id=%s max_turns=%d "
            "history_len=%d backend_reset_count=1 handoff=%s",
            stage.stage_id, current_role, agent_id, max_turns,
            len(stage_history), handoff_received,
        )

        # ── Stage execution loop ─────────────────────────────────────────────
        # Native tool mode: the model receives tool definitions via the
        # Anthropic tool_use mechanism and responds with tool_use blocks.
        # We force tool selection with tool_choice="any" and retry once
        # if the model returns text-only output.

        action_history: List[str] = []
        last_corrupted_result = ""
        corrupted_tool_call_evt = None
        termination_reason = "max_turns"
        backend_reset_count = 1  # already reset above

        for turn in range(1, max_turns + 1):
            # Detect backend mode:
            # - Native (APIBackend): has native _messages property.
            #   The backend owns its own conversation history — no serialized
            #   prompt needed, no history serialization.
            # - Legacy (DryRunBackend etc.): no _messages.
            #   Must build a text prompt from stage_history.
            is_native = hasattr(self.llm, '_messages')
            if is_native:
                prompt = ""
            else:
                prompt = self._build_turn_prompt_from_history(
                    stage=stage,
                    task_prompt=task_prompt,
                    stage_history=stage_history,
                    turn=turn,
                    max_turns=max_turns,
                )

            # Force tool selection — works for both native and text-mode backends.
            tool_choice = "any"

            # ── First API call ───────────────────────────────────────────────
            model_turn = self.llm.generate(prompt, tool_choice=tool_choice)

            # ── Retry on text-only or max_tokens ─────────────────────────────
            if not model_turn.tool_call or model_turn.stop_reason == "max_tokens":
                retry_reason = "text_only" if not model_turn.tool_call else "max_tokens"
                logger.info(
                    "StageRunner retry: stage=%s turn=%d reason=%s",
                    stage.stage_id, turn, retry_reason,
                )
                # Retry once with explicit nudge
                model_turn = self.llm.generate(
                    self.TOOL_CALL_NUDGE,
                    tool_choice=tool_choice,
                )

                if not model_turn.tool_call:
                    # Retry also failed — protocol violation
                    protocol_evt = make_evt(
                        TraceEventType.FINAL_RESPONSE, agent_id, "user",
                        role=current_role,
                        output_text="Terminated: protocol violation — "
                                    "model returned non-tool text after retry.",
                        observable={
                            "protocol_violation": True,
                            "raw_model_text": model_turn.text,
                            "raw_model_stop_reason": model_turn.stop_reason,
                            "retry_attempted": True,
                            "retry_result": None,
                            "retry_reason": retry_reason,
                        },
                    )
                    events.append(protocol_evt)
                    return StageResult(
                        stage_id=stage.stage_id,
                        events=events,
                        termination_reason="protocol_violation",
                        final_agent_role=current_role,
                        step_count=turn,
                        protocol_violation_data={
                            "raw_text": model_turn.text,
                            "stop_reason": model_turn.stop_reason,
                            "turn": turn,
                            "retry_reason": retry_reason,
                        },
                    )

            # ── Extract action from tool_use block ───────────────────────────
            tc = model_turn.tool_call
            action = tc.name
            action_input = tc.input if isinstance(tc.input, dict) else {}
            raw_output = f"[tool_use:{tc.name}]"

            logger.info(
                "StageRunner turn: stage=%s role=%s turn=%d/%d "
                "tool=%s action_input_keys=%s stop_reason=%s",
                stage.stage_id, current_role, turn, max_turns,
                action, list(action_input.keys()),
                model_turn.stop_reason,
            )

            # ── Loop detection ───────────────────────────────────────────────
            if action not in ("handoff", "submit_final"):
                action_history.append(action)
                recent = action_history[-10:]
                if recent.count(action) >= 8:
                    loop_evt = make_evt(
                        TraceEventType.FINAL_RESPONSE, agent_id, "user",
                        role=current_role,
                        output_text=f"Terminated: execution loop detected "
                                    f"(repeated '{action}' {recent.count(action)} times).",
                    )
                    events.append(loop_evt)
                    return StageResult(
                        stage_id=stage.stage_id,
                        events=events,
                        termination_reason="loop",
                        final_agent_role=current_role,
                        step_count=turn,
                    )

            # ── REASONING event ──────────────────────────────────────────────
            # Native mode: serialize native messages for the event record.
            # Dry-run mode: use the text prompt.
            if is_native:
                raw_msgs = getattr(self.llm, '_messages', None)
                # Handle both property (returns list) and method (needs call)
                if callable(raw_msgs):
                    native_msgs = raw_msgs()
                else:
                    native_msgs = raw_msgs
                reasoning_input = json.dumps(native_msgs) if native_msgs else prompt
            else:
                reasoning_input = prompt
            reasoning_evt = make_evt(
                TraceEventType.REASONING, agent_id, "internal",
                role=current_role,
                input_text=reasoning_input,
                output_text=raw_output,
            )
            events.append(reasoning_evt)
            if lep_orchestrator:
                lep_orchestrator.evaluate_triggers(reasoning_evt)

            # ── TOOL EXECUTION ───────────────────────────────────────────────
            if action not in ("handoff", "submit_final"):
                tc = model_turn.tool_call
                tc_evt = make_evt(
                    TraceEventType.TOOL_CALL, agent_id,
                    f"tool_{tc.name}",
                    role=current_role,
                    tool_name=tc.name,
                    tool_call_id=tc.id,
                    tool_arguments=action_input,
                )
                events.append(tc_evt)

                # Execute the tool
                result = self._execute_tool(
                    tc.name, action_input, ws_path
                )

                # Apply LEP corruption if active
                if lep_orchestrator:
                    result = self._apply_lep_corruption(
                        orchestrator=lep_orchestrator,
                        trigger_event=tc_evt,
                        lep_code=lep_code,
                        original_result=result,
                    )

                # Track corrupted results
                if hasattr(result, 'perturbed_result') and result.perturbed_result != result.original_result:
                    last_corrupted_result = result.perturbed_result
                    corrupted_tool_call_evt = tc_evt

                result_text = result.perturbed_result if hasattr(result, 'perturbed_result') else result

                # Record tool result
                tr_evt = make_evt(
                    TraceEventType.TOOL_RESULT, f"tool_{tc.name}", agent_id,
                    role=current_role,
                    tool_name=tc.name,
                    tool_call_id=tc.id,
                    output_text=result_text,
                )
                events.append(tr_evt)
                stage_history.append({
                    "role": "tool",
                    "tool_name": tc.name,
                    "content": result_text,
                })

                # ── Native tool_result in backend conversation ──────────────
                self.llm._append_tool_result(tc, result_text)

                # LEP triggers on tool result consumption
                if lep_orchestrator:
                    results = lep_orchestrator.evaluate_triggers(tr_evt)
                    for lep_code, decision in results.items():
                        if decision.fired:
                            label_injection(tr_evt, lep_code)
                            label_propagation(tr_evt, lep_code)

                # Label consumption if this tool result used corrupted data
                if corrupted_tool_call_evt is tc_evt:
                    label_consumption(tr_evt, lep_code)
                    label_propagation(tr_evt, lep_code)
                    corrupted_tool_call_evt = None

            # ── HANDOFF ──────────────────────────────────────────────────────
            if action == "handoff":
                if not stage.can_handoff:
                    # This stage is not permitted to emit handoffs.
                    # Treat as premature_final: the model is trying to
                    # terminate the workflow through the wrong channel.
                    # This also prevents linear topologies from restarting
                    # after the final stage fails to call submit_final.
                    termination_reason = "premature_final"
                    break

                payload = self._build_handoff_payload(
                    stage=stage,
                    events=events,
                    topology=topology,
                    current_role=current_role,
                    contains_corrupted=last_corrupted_result != "",
                    corrupted_tc_evt=corrupted_tool_call_evt,
                )
                if corrupted_tool_call_evt:
                    payload.contains_corrupted_data = True
                    payload.corrupted_tool_call_event_id = corrupted_tool_call_evt.event_id

                hoff_evt = make_evt(
                    TraceEventType.AGENT_HANDOFF, agent_id,
                    topology.get_stage(payload.to_agent).agent_id
                    if topology.get_stage(payload.to_agent) else agent_id,
                    role=current_role,
                    output_text=payload.summary,
                )
                hoff_evt.observable = {
                    "handoff_from": current_role,
                    "handoff_to": payload.to_agent,
                }
                events.append(hoff_evt)

                if lep_orchestrator:
                    results = lep_orchestrator.evaluate_triggers(hoff_evt)
                    for lep_code, decision in results.items():
                        if decision.fired:
                            label_injection(hoff_evt, lep_code)
                            hoff_evt.observable["corrupted"] = True
                            label_propagation(hoff_evt, lep_code)

                return StageResult(
                    stage_id=stage.stage_id,
                    events=events,
                    termination_reason="handoff",
                    handoff_payload=payload,
                    final_agent_role=current_role,
                    step_count=turn,
                )

            # ── SUBMIT FINAL ─────────────────────────────────────────────────
            if action == "submit_final":
                if not stage.can_finalize:
                    termination_reason = "premature_final"
                    break

                summary = action_input.get("summary", "") or "Task complete"
                final_evt = make_evt(
                    TraceEventType.FINAL_RESPONSE, agent_id, "user",
                    role=current_role,
                    output_text=summary,
                )

                # Mark as downstream failure if this stage consumed corrupted data
                if last_corrupted_result != "":
                    label_failure(final_evt, "factual_error")

                events.append(final_evt)

                if lep_orchestrator and lep_orchestrator._active_leps:
                    lep_orchestrator.evaluate_triggers(final_evt)

                return StageResult(
                    stage_id=stage.stage_id,
                    events=events,
                    termination_reason="final",
                    final_agent_role=current_role,
                    step_count=turn,
                    raw_output=summary,
                )

        # Exhausted max turns
        return StageResult(
            stage_id=stage.stage_id,
            events=events,
            termination_reason=termination_reason,
            final_agent_role=current_role,
            step_count=max_turns,
        )

    # ── Helpers ──────────────────────────────────────────────────────────

    def _parse_tool_input(self, action: str, raw: str) -> Dict[str, Any]:
        """Parse tool input from JSON or raw string."""
        if not raw or raw.strip() == "":
            return {}
        raw = raw.strip()
        if raw.startswith("{"):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pass
        if "/" in raw or raw.endswith(".md") or raw.endswith(".py") or raw.endswith(".txt"):
            return {"path": raw}
        return {"path": raw}

    def _validate_tool_args(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Filter tool arguments to only allowed keys."""
        allowed = {
            "list_directory": {"path"},
            "read_text_file": {"path"},
            "write_file": {"path", "content"},
            "search_files": {"path", "pattern"},
            "create_directory": {"path"},
            "handoff": {"target_agent", "summary", "report_path", "source_paths", "verification_requests"},
            "submit_final": {"summary", "report_path"},
        }
        return {k: v for k, v in args.items() if k in allowed.get(tool_name, set())}

    def _execute_tool(self, tool_name: str, args: Dict[str, Any],
                      ws_path) -> str:
        """Execute a tool against the workspace."""
        try:
            from environment.workspace import Workspace
            ws = Workspace(ws_path)
            return ws.execute(tool_name, args)
        except ImportError:
            return self._simulate_tool(tool_name, args, ws_path)

    def _simulate_tool(self, tool_name: str, args: Dict[str, Any],
                       ws_path) -> str:
        """Simulate tool execution when environment module unavailable."""
        if tool_name == "list_directory":
            path = args.get("path", str(ws_path))
            return f"Directory listing for {path}:\ndocuments/\noutput/\nsrc/\ntests/"

        elif tool_name == "read_text_file":
            file_path = args.get("path", "")
            full_path = ws_path / file_path
            try:
                with open(full_path) as f:
                    return f.read()
            except (OSError, FileNotFoundError):
                return f"[SIMULATED] Contents of {file_path}\n(This is dry-run output.)"

        elif tool_name == "write_file":
            file_path = args.get("path", "")
            content = args.get("content", "")
            full_path = ws_path / file_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            with open(full_path, "w") as f:
                f.write(content)
            return f"File written: {file_path}"

        elif tool_name == "search_files":
            pattern = args.get("pattern", "*.md")
            return f"Found 2 files matching {pattern}:\ndocuments/primary_source.md\ndocuments/supporting_data.md"

        elif tool_name == "create_directory":
            dir_path = args.get("path", "")
            (ws_path / dir_path).mkdir(parents=True, exist_ok=True)
            return f"Directory created: {dir_path}"

        return f"Unknown tool: {tool_name}"

    def _apply_lep_corruption(self, orchestrator, trigger_event: TraceEvent,
                               lep_code: str, original_result: str):
        """Apply LEP corruption to a tool result."""
        for code, lep in orchestrator._active_leps.items():
            if code != lep_code or not hasattr(lep, "corrupt"):
                continue
            try:
                return lep.corrupt(trigger_event, original_result)
            except Exception as e:
                logger.debug("LEP %s corruption error: %s", code, e)

        # Fallback: return original unchanged
        from leps.tool_result_corruption import ToolResultCorruptionResult
        return ToolResultCorruptionResult(
            lep_instance_id=f"{lep_code}_fallback",
            fired=False,
            original_result=original_result,
            perturbed_result=original_result,
            altered_fields=[],
        )

    @staticmethod
    def _repair_action(raw: str) -> Optional[Dict[str, Any]]:
        """Attempt one repair pass when parse_action() returns None.

        Tries prose-pattern detection first, then JSON field extraction.
        Returns None if no action is detectable.
        """
        if not raw:
            return None

        # Strip code fences if present
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        # ── Pass 1: prose-pattern detection ─────────────────────────────────
        prose_result = StageRunner._repair_from_prose(text)
        if prose_result:
            return prose_result

        # ── Pass 2: JSON field extraction ───────────────────────────────────
        m = re.search(r'"action"\s*:\s*"([^"]+)"', text)
        if not m:
            return None

        action = m.group(1)

        # Validate it's a known action
        known_actions = {
            "list_directory", "read_text_file", "write_file",
            "search_files", "create_directory",
            "handoff_to_analyst", "final",
        }
        if action not in known_actions:
            return None

        # Extract action_input if present
        action_input = {}
        m_path = re.search(r'"path"\s*:\s*"([^"]*)"', text)
        if m_path:
            action_input["path"] = m_path.group(1)
        m_content = re.search(r'"content"\s*:\s*"(.*?)"\s*[,\}]', text, re.DOTALL)
        if m_content:
            action_input["content"] = m_content.group(1)
        m_pattern = re.search(r'"pattern"\s*:\s*"([^"]*)"', text)
        if m_pattern:
            action_input["pattern"] = m_pattern.group(1)

        return {
            "reasoning": f"[repaired] Extracted action '{action}' from non-JSON response",
            "action": action,
            "action_input": action_input,
            "final_response": "",
        }

    @staticmethod
    def _repair_from_prose(text: str) -> Optional[Dict[str, Any]]:
        """Detect intended action from natural-language prose.

        Priority order: write_file > read_text_file > list_directory >
        search_files > handoff_to_analyst > final
        """
        lower = text.lower()

        # write_file — strongest signal: explicit "writing to <path>" or
        # "write <path>" or "save to <path>"
        write_patterns = [
            r'writing to\s+(\S+)',
            r'write\s+(?:to\s+)?(\S+\.(?:md|txt|json|py|log|csv))',
            r'saving to\s+(\S+)',
            r'saved to\s+(\S+)',
            r'output.*?(\S+\.(?:md|txt|json))',
        ]
        for pat in write_patterns:
            m = re.search(pat, lower)
            if m:
                path = m.group(1).strip('"').strip("'")
                return {
                    "reasoning": f"[repaired] Detected write_file intent from prose: '{text[:100]}'",
                    "action": "write_file",
                    "action_input": {"path": path},
                    "final_response": "",
                }

        # read_text_file — "reading <path>" or "opening <path>"
        read_patterns = [
            r'reading\s+(\S+\.(?:md|txt|py|json|log|csv))',
            r'opening\s+(\S+\.(?:md|txt|py|json|log|csv))',
            r'checking\s+(\S+\.(?:md|txt|py|json|log|csv))',
        ]
        for pat in read_patterns:
            m = re.search(pat, lower)
            if m:
                path = m.group(1).strip('"').strip("'")
                return {
                    "reasoning": f"[repaired] Detected read_text_file intent from prose",
                    "action": "read_text_file",
                    "action_input": {"path": path},
                    "final_response": "",
                }

        # list_directory — "listing" or "browse" or "explore the workspace"
        if re.search(r'\b(listing|browse|explore|looking at)\b.*?\b(directory|workspace|files|folder)\b', lower):
            return {
                "reasoning": "[repaired] Detected list_directory intent from prose",
                "action": "list_directory",
                "action_input": {"path": "."},
                "final_response": "",
            }

        # search_files — "searching" or "looking for"
        if re.search(r'\b(searching|looking for|find)\b', lower):
            return {
                "reasoning": "[repaired] Detected search_files intent from prose",
                "action": "search_files",
                "action_input": {"pattern": "*.md", "path": "."},
                "final_response": "",
            }

        # handoff_to_analyst — "handing off" or "passing to analyst"
        if re.search(r'\b(handing off|hand.?off|passing to|transferring to)\b', lower):
            return {
                "reasoning": "[repaired] Detected handoff intent from prose",
                "action": "handoff_to_analyst",
                "action_input": {},
                "final_response": "",
            }

        # final — explicit completion signals
        if re.search(r'\b(task complete|done|finished|all done|complete)\b', lower):
            return {
                "reasoning": "[repaired] Detected final intent from prose",
                "action": "final",
                "action_input": {},
                "final_response": text[:200],
            }

        return None

    def _find_next_agent_role(self, current_role: str,
                              topology: TopologyConfig) -> str:
        """Find the next agent role in the topology after current_role."""
        stages = topology.stages
        for i, stage in enumerate(stages):
            if stage.agent_role == current_role and i + 1 < len(stages):
                return stages[i + 1].agent_role
        return topology.exit_stage

    def _build_system_prompt(self, stage: Stage,
                             handoff_from_payload: Optional[HandoffPayload]) -> str:
        """Build the system prompt for this stage.

        Role-specific completion instructions: each agent is told exactly
        one legal completion action based on its stage configuration.
        """
        parts = [f"You are {stage.agent_role}."]
        if handoff_from_payload:
            parts.append(
                f"You are continuing work from {handoff_from_payload.from_agent}. "
                f"Review their findings and build upon them."
            )

        if stage.can_finalize and not stage.can_handoff:
            # Pure final agent: must call submit_final, must NOT call handoff
            parts.append(
                "You are the final agent in this workflow. "
                "When your work is complete, call submit_final with a summary. "
                "Do NOT call handoff."
            )
        elif stage.can_handoff and not stage.can_finalize:
            # Pure intermediate agent: must call handoff, must NOT call submit_final or prose
            parts.append(
                "You are an intermediate agent. After completing your analysis "
                "and writing your output artifact, call handoff to transfer work "
                "to the next agent. "
                "Do NOT call submit_final. Do NOT finish with plain prose."
            )
        elif stage.can_handoff and stage.can_finalize:
            # Review-loop style: may either hand back for revision OR finalize.
            parts.append(
                "You may either (a) call submit_final to terminate the workflow "
                "with a summary, or (b) call handoff to send work back for "
                "revision. Do NOT finish with plain prose."
            )
        else:
            # Neither handoff nor finalize: a self-contained stage.
            parts.append(
                "Complete the assigned task using the available tools."
            )

        return " ".join(parts)

    def _build_turn_prompt_from_history(
        self,
        stage: Stage,
        task_prompt: str,
        stage_history: List[Dict[str, Any]],
        turn: int,
        max_turns: int,
    ) -> str:
        """Build the prompt for this turn from the stage-local message history.

        This is the key fix: we build from stage_history (persistent per agent)
        rather than from global events (which gets rebuilt each turn).
        """
        # Build a text representation of the conversation so far
        lines = []
        for msg in stage_history:
            role = msg.get("role", "")
            if role == "system":
                lines.append(f"[System] {msg['content']}")
            elif role == "user":
                lines.append(f"[User] {msg['content']}")
            elif role == "assistant":
                # Include the full assistant response (not truncated)
                lines.append(f"[Assistant] {msg['content']}")
            elif role == "tool":
                # Include full tool result (not truncated)
                tool_name = msg.get("tool_name", "unknown")
                content = msg.get("content", "")
                lines.append(f"[Tool: {tool_name}] {content}")

        remaining = max_turns - turn
        deadline = ""
        if remaining <= 3:
            deadline = f"\nCRITICAL: {remaining} steps left. Call 'final' or 'handoff_to_analyst' if done."
        elif remaining <= 8:
            deadline = f"\nWarning: {remaining} steps remaining."

        return (
            f"Task: {task_prompt}\n"
            f"You are {stage.agent_role}. Step {turn}/{max_turns}\n\n"
            f"Conversation so far:\n" + "\n".join(lines) +
            f"\n\nRespond with JSON only.{deadline}"
        )

    def _build_handoff_payload(
        self,
        stage: Stage,
        events: List[TraceEvent],
        topology: TopologyConfig,
        current_role: str,
        contains_corrupted: bool = False,
        corrupted_tc_evt: Optional[TraceEvent] = None,
    ) -> HandoffPayload:
        """Build a structured handoff payload from the agent's outputs."""
        findings: List[str] = []
        source_paths: List[str] = []
        report_path = ""

        # Extract findings from write_file outputs
        write_events = [e for e in events if e.event_type == TraceEventType.TOOL_CALL
                        and e.tool_name == "write_file"]
        for we in write_events:
            if we.tool_arguments and isinstance(we.tool_arguments, dict):
                path = we.tool_arguments.get("path", "")
                if path:
                    source_paths.append(path)
                    if "report" in path.lower() or "output" in path.lower():
                        report_path = path

        # Extract findings from tool results
        tool_results = [e for e in events if e.event_type == TraceEventType.TOOL_RESULT]
        for tr in tool_results:
            if tr.output_text and len(tr.output_text) > 10:
                # Include first 200 chars as a finding
                findings.append(tr.output_text[:200].replace("\n", " "))

        # Build summary from recent events
        summary_parts = [f"Handoff from {current_role}"]
        if findings:
            summary_parts.append(f"Findings: {'; '.join(findings[:3])}")
        if source_paths:
            summary_parts.append(f"Files: {', '.join(source_paths[:3])}")
        summary = ". ".join(summary_parts)

        # Detect if this handoff contains corrupted data
        contains_corrupted_data = contains_corrupted or bool(
            e for e in events
            if getattr(e, "event_labels", None)
            and e.event_labels.consumes_perturbed_info
        )

        return HandoffPayload(
            from_agent=current_role,
            to_agent=self._find_next_agent_role(current_role, topology),
            findings=findings[:10],
            source_paths=source_paths[:10],
            report_path=report_path,
            uncertainty=[],
            verification_requests=[],
            provenance_event_ids=[e.event_id for e in events[-10:]],
            summary=summary,
            raw_output=summary,
            contains_corrupted_data=contains_corrupted_data,
            corrupted_tool_call_event_id=corrupted_tc_evt.event_id if corrupted_tc_evt else "",
        )
