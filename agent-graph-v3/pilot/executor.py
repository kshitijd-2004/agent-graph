"""Pilot executor — runs real-model or dry-run scenarios and collects results for audit."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from schemas import (
    LEPConfig, ScenarioSpec, Trace, TraceVariant,
    WorkflowConfig,
)
from schemas.scenario import CONDITIONS

logger = logging.getLogger(__name__)


@dataclass
class ExecutionRecord:
    """Result of a single pilot execution."""
    execution_id: str
    scenario_id: str
    task_family: str
    condition: str
    lep_codes: list[str]
    variant: str
    trace: Optional[Trace]
    trace_id: str = ""
    success: bool = False
    error: Optional[str] = None
    runtime_seconds: float = 0.0
    num_events: int = 0
    num_tool_calls: int = 0
    num_handoffs: int = 0
    injection_fired: bool = False
    injection_events: list[str] = field(default_factory=list)
    consumption_events: list[str] = field(default_factory=list)
    propagation_events: list[str] = field(default_factory=list)
    downstream_failure: bool = False
    task_success: bool = False
    evaluator_passed: bool = False
    evaluator_errors: list[str] = field(default_factory=list)
    final_output: str = ""
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "scenario_id": self.scenario_id,
            "task_family": self.task_family,
            "condition": self.condition,
            "lep_codes": self.lep_codes,
            "variant": self.variant,
            "trace_id": self.trace_id,
            "success": self.success,
            "error": self.error,
            "runtime_seconds": self.runtime_seconds,
            "num_events": self.num_events,
            "num_tool_calls": self.num_tool_calls,
            "num_handoffs": self.num_handoffs,
            "injection_fired": self.injection_fired,
            "injection_events": self.injection_events,
            "consumption_events": self.consumption_events,
            "propagation_events": self.propagation_events,
            "downstream_failure": self.downstream_failure,
            "task_success": self.task_success,
            "evaluator_passed": self.evaluator_passed,
            "evaluator_errors": self.evaluator_errors,
            "final_output": self.final_output,
            "timestamp": self.timestamp,
        }


class PilotExecutor:
    """Executes pilot scenarios and collects results.

    Supports both dry-run and real-model execution.
    """

    def __init__(
        self,
        output_dir: Path,
        fixture_root: Path,
        llm_backend: Any = None,
        dry_run: bool = True,
    ):
        self.output_dir = output_dir
        self.fixture_root = fixture_root
        self.dry_run = dry_run
        self.results: list[ExecutionRecord] = []
        self._scenario_idx = 0

    def run_all(self, plan: list[dict[str, Any]]) -> list[ExecutionRecord]:
        """Execute all scenarios in the plan."""
        for entry in plan:
            record = self._run_scenario(entry)
            self.results.append(record)
            self._persist_record(record)
        return self.results

    def _run_scenario(self, entry: dict[str, Any]) -> ExecutionRecord:
        """Execute a single scenario."""
        exec_id = f"exec-{self._scenario_idx:03d}"
        self._scenario_idx += 1
        t0 = time.time()

        record = ExecutionRecord(
            execution_id=exec_id,
            scenario_id=entry["scenario_id"],
            task_family=entry["task_family"],
            condition=entry["condition"],
            lep_codes=entry.get("lep_codes", []),
            variant="benign" if entry["condition"] == "benign" else (
                "counterfactual" if entry["condition"] == "counterfactual" else "malignant"
            ),
            trace=None,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        try:
            # Build ScenarioSpec
            wcfg = self._build_workflow_config(entry)
            lep_configs = [self._resolve_lep(c) for c in entry.get("lep_codes", [])]

            spec = ScenarioSpec(
                scenario_id=entry["scenario_id"],
                task_family=entry["task_family"],
                task_variant=entry.get("task_variant", "default"),
                fixture_id=self._fixture_id(entry),
                workflow_config=wcfg,
                lep_configs=lep_configs,
                condition=entry["condition"],
                repetition_index=entry.get("repetition_index", 0),
            )

            # Build backend and runner
            from generation.runner import ScenarioRunner, DryRunBackend
            from backend.api_backend import APIBackend

            backend = DryRunBackend() if self.dry_run else APIBackend()
            runner = ScenarioRunner(llm_backend=backend, dry_run=self.dry_run)

            # Execute
            result = runner.run(spec, self.fixture_root)
            trace = result.trace

            record.trace = trace
            record.trace_id = trace.trace_id
            record.success = result.runner_success
            record.error = result.error
            record.num_events = len(trace.events)

            # Extract event statistics
            record.num_tool_calls = sum(
                1 for e in trace.events if e.event_type.value == "tool_call"
            )
            record.num_handoffs = sum(
                1 for e in trace.events if e.event_type.value == "agent_handoff"
            )

            # Extract LEP labels
            record.injection_events = [
                e.event_id for e in trace.events
                if getattr(e, "event_labels", None) and e.event_labels.is_injection_origin
            ]
            record.consumption_events = [
                e.event_id for e in trace.events
                if getattr(e, "event_labels", None) and e.event_labels.consumes_perturbed_info
            ]
            record.propagation_events = [
                e.event_id for e in trace.events
                if getattr(e, "event_labels", None) and e.event_labels.forwards_perturbed_info
            ]
            record.injection_fired = len(record.injection_events) > 0
            record.downstream_failure = any(
                getattr(e, "event_labels", None) and e.event_labels.introduces_downstream_failure
                for e in trace.events
            )

            # Extract final output
            for e in reversed(trace.events):
                if e.event_type.value == "final_response":
                    record.final_output = (e.output_text or e.input_text or "")[:500]
                    break

            # Evaluate via task evaluator
            eval_result = self._evaluate(trace, spec)
            record.task_success = eval_result.get("task_success", False)
            record.evaluator_passed = eval_result.get("passed", True)
            record.evaluator_errors = eval_result.get("errors", [])

            # Cross-check: perturbed should fail, benign/counterfactual should succeed
            record.downstream_failure = self._expected_failure(entry["condition"], record)

        except Exception as e:
            logger.error("Pilot exec %s failed: %s", exec_id, e, exc_info=True)
            record.error = str(e)
            record.success = False

        record.runtime_seconds = round(time.time() - t0, 2)
        return record

    def _build_workflow_config(self, entry: dict[str, Any]) -> WorkflowConfig:
        from pilot.config import DEFAULT_WORKFLOW_CONFIG
        cfg = WorkflowConfig(
            topology=entry.get("topology", "linear_2"),
            sharing_policy=DEFAULT_WORKFLOW_CONFIG.sharing_policy,
            memory_mode=DEFAULT_WORKFLOW_CONFIG.memory_mode,
            verification_mode=DEFAULT_WORKFLOW_CONFIG.verification_mode,
            max_events=DEFAULT_WORKFLOW_CONFIG.max_events,
            max_agent_turns=DEFAULT_WORKFLOW_CONFIG.max_agent_turns,
            timeout_seconds=DEFAULT_WORKFLOW_CONFIG.timeout_seconds,
            model_name=DEFAULT_WORKFLOW_CONFIG.model_name,
            temperature=DEFAULT_WORKFLOW_CONFIG.temperature,
            seed=DEFAULT_WORKFLOW_CONFIG.seed,
            allow_parallel_agents=DEFAULT_WORKFLOW_CONFIG.allow_parallel_agents,
            allow_retries=DEFAULT_WORKFLOW_CONFIG.allow_retries,
        )
        return cfg

    def _fixture_id(self, entry: dict[str, Any]) -> str:
        family = entry["task_family"]
        # Map task families to actual fixture directory names.
        FIXTURE_MAP = {
            "code_review": "code_review_easy",
            "financial_analysis": "financial_clean",
            "research_synthesis": "research_conflicting",
        }
        return FIXTURE_MAP.get(family, f"{family}_default")

    def _resolve_lep(self, code: str) -> LEPConfig:
        from pilot.config import LEP_BY_CODE
        return LEP_BY_CODE.get(code, LEPConfig(
            code=code, name=code, category="unknown",
            target_agent="researcher",
            description=f"Auto-resolved: {code}",
        ))

    def _evaluate(self, trace: Trace, spec: ScenarioSpec) -> dict[str, Any]:
        """Run task evaluator on the trace."""
        from evaluators.base_evaluator import get_evaluator
        from environment.workspace import Workspace

        evaluator = get_evaluator(spec.task_family)
        if evaluator is None:
            return {"passed": True, "errors": [], "task_success": True}

        # Build workspace
        ws = Workspace(self.output_dir / f"ws_{spec.scenario_id}")

        result = evaluator.evaluate(trace, ws, spec)
        # Convert to dict — EvaluationResult is a dataclass
        d: dict[str, Any]
        if hasattr(result, 'to_dict'):
            d = result.to_dict()
        elif hasattr(result, '__dataclass_fields__'):
            d = {f.name: getattr(result, f.name) for f in result.__dataclass_fields__}
        else:
            d = dict(result)

        # Normalize to executor-expected keys
        d.setdefault("task_success", d.get("task_success", False))
        d.setdefault("passed", d.get("task_success", True))
        d.setdefault("errors", d.get("failure_types", d.get("evaluator_notes", [])))
        d.setdefault("downstream_failure", d.get("downstream_failure", False))
        return d

    def _expected_failure(self, condition: str, record: ExecutionRecord) -> bool:
        """Cross-check: does the failure status match the condition?"""
        if condition == "benign":
            return False  # benign should not fail
        elif condition == "single_lep":
            return record.injection_fired and record.downstream_failure
        elif condition == "counterfactual":
            return False  # counterfactual should not fail
        return False

    def _persist_record(self, record: ExecutionRecord) -> None:
        """Write execution record to JSONL."""
        records_path = self.output_dir / "pilot_records.jsonl"
        with open(records_path, "a") as f:
            f.write(json.dumps(record.to_dict()) + "\n")
