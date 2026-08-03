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

        Returns:
            StageResult with events and termination info
        """
        events: List[TraceEvent] = []
        event_counter = [0]
        max_turns = stage.max_turns
        current_role = stage.agent_role
        agent_id = stage.agent_id

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
            event_counter[0] += 1
            now = datetime.now(timezone.utc).isoformat()
            return TraceEvent(
                trace_id="",  # set by caller
                event_id=str(event_counter[0]),
                event_index=event_counter[0] - 1,
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

        # Reset backend ONCE per stage (not per turn)
        self.llm.reset(
            task=task_prompt,
            agent_name=current_role,
            mcp_tools=["list_directory", "read_text_file", "write_file",
                       "search_files", "create_directory"],
            system_prompt=f"You are {current_role}. Complete the assigned task.",
        )

        # If dry-run backend, set LEP context
        if hasattr(self.llm, 'set_context') and lep_orchestrator:
            lep_code = "benign"
            if scenario.lep_configs:
                lep_code = scenario.lep_configs[0].code
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

        action_history: List[str] = []
        last_corrupted_result = ""
        corrupted_tool_call_evt = None
        termination_reason = "max_turns"
        backend_reset_count = 1  # already reset above

        for turn in range(1, max_turns + 1):
            # Build prompt from stage-local history
            # (not from global events — that was the bug)
            prompt = self._build_turn_prompt_from_history(
                stage=stage,
                task_prompt=task_prompt,
                stage_history=stage_history,
                turn=turn,
                max_turns=max_turns,
            )

            # Call backend — do NOT reset here (history accumulation fix)
            raw = self.llm.generate(prompt)
            parsed = self.llm.parse_action(raw)

            # ── Parse logging ────────────────────────────────────────────────
            logger.info(
                "StageRunner parse: stage=%s role=%s turn=%d raw_len=%d "
                "parsed=%s repair_count=0",
                stage.stage_id, current_role, turn, len(raw),
                "ok" if parsed else "None",
            )

            # ── Repair: if parse failed but raw contains an action field ──────
            repair_count = 0
            if parsed is None:
                repaired = self._repair_action(raw)
                if repaired is not None:
                    repair_count = 1
                    parsed = repaired
                    logger.info(
                        "StageRunner repair: stage=%s turn=%d repaired_action=%s",
                        stage.stage_id, turn, parsed.get("action", "?"),
                    )

            if parsed is None:
                # Final fallback: cannot recover — terminate with protocol_violation
                logger.warning(
                    "StageRunner protocol_violation: stage=%s turn=%d "
                    "raw=%s", stage.stage_id, turn, raw[:200],
                )
                final_evt = make_evt(
                    TraceEventType.FINAL_RESPONSE, agent_id, "user",
                    role=current_role,
                    output_text=f"Terminated: protocol_violation — model did not emit valid action JSON.",
                )
                events.append(final_evt)
                return StageResult(
                    stage_id=stage.stage_id,
                    events=events,
                    termination_reason="protocol_violation",
                    final_agent_role=current_role,
                    step_count=turn,
                )

            action = parsed.get("action", "")
            action_input = parsed.get("action_input", "")
            final_response = parsed.get("final_response", "")

            logger.info(
                "StageRunner turn: stage=%s role=%s turn=%d/%d "
                "history_msgs=%d backend_resets=%d action=%s repair_count=%d",
                stage.stage_id, current_role, turn, max_turns,
                len(stage_history), backend_reset_count, action, repair_count,
            )

            # ── Loop detection ───────────────────────────────────────────────
            if action not in ("handoff_to_analyst", "final"):
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
            reasoning_evt = make_evt(
                TraceEventType.REASONING, agent_id, "internal",
                role=current_role,
                input_text=prompt,
                output_text=raw,
            )
            events.append(reasoning_evt)
            if lep_orchestrator:
                lep_orchestrator.evaluate_triggers(reasoning_evt)

            # Append assistant turn to stage history
            stage_history.append({
                "role": "assistant",
                "content": raw,
            })

            # ── HANDOFF ──────────────────────────────────────────────────────
            if action == "handoff_to_analyst":
                if not stage.accepts_handoff:
                    # Can't handoff from here — treat as premature
                    termination_reason = "premature_final"
                    break

                # Build structured payload from researcher's outputs
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

                # LEP triggers on handoff
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

            # ── PREMATURE FINAL ──────────────────────────────────────────────
            if action == "final" or final_response:
                if not stage.can_finalize:
                    # This stage cannot finalize — enforce handoff instead
                    if stage.accepts_handoff:
                        # Emit a synthetic AGENT_HANDOFF event
                        next_role = self._find_next_agent_role(current_role, topology)
                        next_stage = topology.get_stage(next_role)
                        next_aid = next_stage.agent_id if next_stage else agent_id
                        hoff_evt = make_evt(
                            TraceEventType.AGENT_HANDOFF, agent_id, next_aid,
                            role=current_role,
                            output_text=f"Auto-enforced handoff from {current_role} to {next_role} "
                                        f"(backend skipped handoff)",
                        )
                        hoff_evt.observable = {"handoff_from": current_role, "handoff_to": next_role}
                        events.append(hoff_evt)

                        # Build a minimal handoff payload and return to let the
                        # topology route to the next stage
                        payload = HandoffPayload(
                            from_agent=current_role,
                            to_agent=next_role,
                            findings=[final_response or "Auto-enforced handoff"],
                            source_paths=[],
                            summary=final_response or "Auto-enforced handoff",
                            raw_output=final_response or "",
                            contains_corrupted_data=last_corrupted_result != "",
                        )
                        return StageResult(
                            stage_id=stage.stage_id,
                            events=events,
                            termination_reason="handoff",
                            handoff_payload=payload,
                            final_agent_role=current_role,
                            step_count=turn,
                        )
                    else:
                        # Can't handoff from here — treat as premature
                        logger.warning(
                            "Stage %s (role=%s) attempted premature final at turn %d. "
                            "Stage does not accept handoff.",
                            stage.stage_id, current_role, turn,
                        )
                        break

                # This stage may finalize — emit FINAL_RESPONSE
                final_evt = make_evt(
                    TraceEventType.FINAL_RESPONSE, agent_id, "user",
                    role=current_role,
                    output_text=final_response or "Task complete",
                )

                # Mark as downstream failure if this stage consumed corrupted data
                if handoff_from_payload and handoff_from_payload.contains_corrupted_data:
                    label_failure(final_evt, "factual_error")

                events.append(final_evt)

                if lep_orchestrator and lep_orchestrator._active_leps:
                    lep_orchestrator.evaluate_triggers(final_evt)

                return StageResult(
                    stage_id=stage.stage_id,
                    events=events,
                    termination_reason="final",
                    final_agent_role=current_role,
                    raw_output=final_response,
                    step_count=turn,
                )

            # ── TOOL CALLS ────────────────────────────────────────────────────
            if action in self.TASK_ACTIONS:
                raw_args = self._parse_tool_input(action, action_input)
                args = self._validate_tool_args(action, raw_args)
                original_result = self._execute_tool(action, args, ws_path)

                # TOOL_CALL event
                tc_evt = make_evt(
                    TraceEventType.TOOL_CALL, agent_id, f"tool_{action}",
                    role=current_role,
                    tool_name=action,
                    tool_arguments=args,
                    input_text=str(args),
                )
                events.append(tc_evt)

                # LEP corruption on tool call
                if lep_orchestrator:
                    results = lep_orchestrator.evaluate_triggers(tc_evt)
                    for lep_code, decision in results.items():
                        if decision.fired:
                            cr = self._apply_lep_corruption(
                                lep_orchestrator, tc_evt, lep_code, original_result
                            )
                            original_result = cr.perturbed_result
                            last_corrupted_result = cr.perturbed_result
                            corrupted_tool_call_evt = tc_evt
                            label_injection(tc_evt, lep_code)

                            # Feed corrupted value to backend so it propagates
                            if hasattr(self.llm, 'record_corrupted_value'):
                                field_name = cr.altered_fields[0] if cr.altered_fields else "value"
                                self.llm.record_corrupted_value(field_name, cr.perturbed_result)

                # TOOL_RESULT event
                tr_evt = make_evt(
                    TraceEventType.TOOL_RESULT, f"tool_{action}", agent_id,
                    role=current_role,
                    tool_name=action,
                    tool_result=original_result,
                    output_text=original_result,
                )
                events.append(tr_evt)

                if corrupted_tool_call_evt is tc_evt:
                    label_consumption(tr_evt, "LEP_TOOL_RESULT_CORRUPTION")
                    label_propagation(tr_evt, "LEP_TOOL_RESULT_CORRUPTION")
                    corrupted_tool_call_evt = None

                if lep_orchestrator:
                    lep_orchestrator.evaluate_triggers(tr_evt, tool_result=original_result)

                # Append tool result to stage-local history
                stage_history.append({
                    "role": "tool",
                    "tool_name": action,
                    "content": original_result,
                })

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
            "action_input": json.dumps(action_input),
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
                    "action_input": json.dumps({"path": path}),
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
                    "action_input": json.dumps({"path": path}),
                    "final_response": "",
                }

        # list_directory — "listing" or "browse" or "explore the workspace"
        if re.search(r'\b(listing|browse|explore|looking at)\b.*?\b(directory|workspace|files|folder)\b', lower):
            return {
                "reasoning": "[repaired] Detected list_directory intent from prose",
                "action": "list_directory",
                "action_input": json.dumps({"path": "."}),
                "final_response": "",
            }

        # search_files — "searching" or "looking for"
        if re.search(r'\b(searching|looking for|find)\b', lower):
            return {
                "reasoning": "[repaired] Detected search_files intent from prose",
                "action": "search_files",
                "action_input": json.dumps({"pattern": "*.md", "path": "."}),
                "final_response": "",
            }

        # handoff_to_analyst — "handing off" or "passing to analyst"
        if re.search(r'\b(handing off|hand.?off|passing to|transferring to)\b', lower):
            return {
                "reasoning": "[repaired] Detected handoff intent from prose",
                "action": "handoff_to_analyst",
                "action_input": json.dumps({}),
                "final_response": "",
            }

        # final — explicit completion signals
        if re.search(r'\b(task complete|done|finished|all done|complete)\b', lower):
            return {
                "reasoning": "[repaired] Detected final intent from prose",
                "action": "final",
                "action_input": json.dumps({}),
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
        """Build the system prompt for this stage."""
        parts = [f"You are {stage.agent_role}."]
        if handoff_from_payload:
            parts.append(
                f"You are continuing work from {handoff_from_payload.from_agent}. "
                f"Review their findings and build upon them."
            )
        parts.append("Use the available tools to complete your task.")
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
            f"\n\n"
            f"IMPORTANT: Respond with ONLY a single JSON object. No extra text.\n"
            f'Format: {{"reasoning": "...", "action": "tool_name", "action_input": {{"key": "value"}}, "final_response": "..."}}\n'
            f'Allowed actions: list_directory, read_text_file, write_file, search_files, create_directory, handoff_to_analyst, final\n'
            f'Example: {{"reasoning": "Writing report", "action": "write_file", "action_input": {{"path": "output/report.md", "content": "report text here"}}}}\n'
            f'To finish: {{"reasoning": "done", "action": "final", "action_input": {{}}, "final_response": "your findings"}}\n'
            f'CRITICAL: When action is "write_file", you MUST include "content" with the actual file text. '
            f'Never call write_file with only a path — include the full report content inline.'
            f"{deadline}"
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
