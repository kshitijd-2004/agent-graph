"""Benchmark runner — scales the pilot into a full experimental benchmark.

Differences from pilot:
- Uses a configurable benchmark manifest (not hardcoded pilot lists)
- Supports ALL topologies, ALL LEPs, ALL propagation modes
- Supports multiple repetitions for statistical significance
- Emits per-repetition trace files plus an aggregated results CSV
- Does not require real API keys — dry-run backends work offline
"""
from __future__ import annotations

import csv
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from schemas import (
    LEPConfig, ScenarioSpec, Trace, WorkflowConfig,
)
from schemas.scenario import CONDITIONS, TOPOLOGIES

logger = logging.getLogger("benchmark")


# ── Execution record ───────────────────────────────────────────────────────

@dataclass
class BenchmarkRecord:
    """One execution result for the benchmark."""
    run_id: str
    scenario_id: str
    task_family: str
    condition: str          # benign | single_lep | counterfactual
    lep_code: str
    topology: str
    propagation_mode: str
    repetition_index: int
    trace_id: str = ""
    success: bool = False
    error: Optional[str] = None
    runtime_seconds: float = 0.0
    num_events: int = 0
    num_tool_calls: int = 0
    num_handoffs: int = 0
    num_stages: int = 0
    num_turns: int = 0
    # LEP labels
    injection_fired: bool = False
    injection_event_ids: list[str] = field(default_factory=list)
    consumption_event_ids: list[str] = field(default_factory=list)
    propagation_event_ids: list[str] = field(default_factory=list)
    recovery_event_ids: list[str] = field(default_factory=list)
    # Outcomes
    downstream_failure: bool = False
    failure_type: str = ""
    task_success: bool = False
    evaluator_passed: bool = False
    evaluator_errors: list[str] = field(default_factory=list)
    # Propagation tracking
    perturbation_reached_target: bool = False
    perturbation_propagated_to_consumer: bool = False
    perturbation_propagated_to_producer: bool = False
    recovery_detected: bool = False
    # Final output snippet
    final_output: str = ""
    timestamp: str = ""
    trace_path: str = ""
    # Pairing
    pair_tag: str = ""
    is_baseline: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "scenario_id": self.scenario_id,
            "task_family": self.task_family,
            "condition": self.condition,
            "lep_code": self.lep_code,
            "topology": self.topology,
            "propagation_mode": self.propagation_mode,
            "repetition_index": self.repetition_index,
            "trace_id": self.trace_id,
            "success": self.success,
            "error": self.error,
            "runtime_seconds": self.runtime_seconds,
            "num_events": self.num_events,
            "num_tool_calls": self.num_tool_calls,
            "num_handoffs": self.num_handoffs,
            "num_stages": self.num_stages,
            "num_turns": self.num_turns,
            "injection_fired": self.injection_fired,
            "injection_event_ids": self.injection_event_ids,
            "consumption_event_ids": self.consumption_event_ids,
            "propagation_event_ids": self.propagation_event_ids,
            "recovery_event_ids": self.recovery_event_ids,
            "downstream_failure": self.downstream_failure,
            "failure_type": self.failure_type,
            "task_success": self.task_success,
            "evaluator_passed": self.evaluator_passed,
            "evaluator_errors": self.evaluator_errors,
            "perturbation_reached_target": self.perturbation_reached_target,
            "perturbation_propagated_to_consumer": self.perturbation_propagated_to_consumer,
            "perturbation_propagated_to_producer": self.perturbation_propagated_to_producer,
            "recovery_detected": self.recovery_detected,
            "final_output": self.final_output,
            "timestamp": self.timestamp,
            "trace_path": self.trace_path,
            "pair_tag": self.pair_tag,
            "is_baseline": self.is_baseline,
        }


# ── Manifest / config ──────────────────────────────────────────────────────

@dataclass
class BenchmarkManifest:
    """Configuration for a benchmark run.

    Fields:
        topologies:       List of topology IDs to benchmark.
        task_families:    List of task family names.
        lep_configs:      List of LEPConfig objects.
        num_repetitions:  How many times to repeat each (topology × task × LEP) cell.
        max_events:       Upper bound on trace events per scenario.
        max_agent_turns:  Upper bound on turns per agent per stage.
        model_name:       Model to use (default: claude-sonnet-5).
        dry_run:          If True, use DryRunBackend.
    """
    topologies: list[str] = field(default_factory=list)
    task_families: list[str] = field(default_factory=list)
    lep_configs: list[LEPConfig] = field(default_factory=list)
    num_repetitions: int = 3
    max_events: int = 50
    max_agent_turns: int = 20
    model_name: str = "claude-sonnet-5"
    dry_run: bool = True
    output_dir: Optional[Path] = None
    fixture_root: Optional[Path] = None
    seed: int = 42
    propagation_modes: list[str] = field(default_factory=lambda: [
        "single_origin",
        "one_to_many",
        "many_to_one",
    ])

    def build_plan(self) -> list[dict[str, Any]]:
        """Build the full cross-product execution plan."""
        plan: list[dict[str, Any]] = []
        idx = 0

        for topology in self.topologies:
            for task_family in self.task_families:
                for lep_config in self.lep_configs:
                    for prop_mode in self.propagation_modes:
                        for rep in range(self.num_repetitions):
                            pair_id = (
                                f"b_{topology}_{task_family}_"
                                f"{lep_config.code}_{prop_mode}_{rep:02d}"
                            )
                            plan.append({
                                "run_id": f"run-{idx:04d}",
                                "scenario_id": f"{pair_id}_benign",
                                "task_family": task_family,
                                "condition": "benign",
                                "lep_codes": [],
                                "topology": topology,
                                "propagation_mode": prop_mode,
                                "lep_code": lep_config.code,
                                "repetition_index": rep,
                                "pair_tag": pair_id,
                                "is_baseline": True,
                            })
                            plan.append({
                                "run_id": f"run-{idx + 1:04d}",
                                "scenario_id": f"{pair_id}_lep",
                                "task_family": task_family,
                                "condition": "single_lep",
                                "lep_codes": [lep_config.code],
                                "topology": topology,
                                "propagation_mode": prop_mode,
                                "lep_code": lep_config.code,
                                "repetition_index": rep,
                                "pair_tag": pair_id,
                                "is_baseline": False,
                            })
                            idx += 2

                        # Counterfactuals (no LEP, same config)
                        for rep in range(self.num_repetitions):
                            plan.append({
                                "run_id": f"run-{idx:04d}",
                                "scenario_id": (
                                    f"b_{topology}_{task_family}_"
                                    f"cf_{rep:02d}"
                                ),
                                "task_family": task_family,
                                "condition": "counterfactual",
                                "lep_codes": [],
                                "topology": topology,
                                "propagation_mode": prop_mode,
                                "lep_code": "",
                                "repetition_index": rep,
                                "pair_tag": "",
                                "is_baseline": False,
                            })
                            idx += 1

        return plan


# ── Benchmark runner ────────────────────────────────────────────────────────

class BenchmarkRunner:
    """Executes the full benchmark and writes results.

    Usage:
        manifest = BenchmarkManifest(
            topologies=["linear_2", "star", "mesh"],
            task_families=["code_review"],
            lep_configs=[...],
            num_repetitions=5,
        )
        runner = BenchmarkRunner(manifest)
        runner.run()
    """

    def __init__(
        self,
        manifest: BenchmarkManifest,
        llm_backend: Any = None,
    ):
        self.manifest = manifest
        self.llm_backend = llm_backend
        self.results: list[BenchmarkRecord] = []
        self._run_idx = 0

    def run(self) -> dict[str, Any]:
        """Execute the full benchmark plan."""
        plan = self.manifest.build_plan()
        logger.info(
            "Benchmark plan: %d scenarios across %d topologies × %d tasks × "
            "%d LEPs × %d rep × %d prop_modes",
            len(plan),
            len(self.manifest.topologies),
            len(self.manifest.task_families),
            len(self.manifest.lep_configs),
            self.manifest.num_repetitions,
            len(self.manifest.propagation_modes),
        )

        for entry in plan:
            record = self._execute(entry)
            self.results.append(record)
            self._persist_record(record)
            self._run_idx += 1

        summary = self._summarize()
        self._write_summary(summary)
        logger.info(
            "Benchmark complete: %d runs, %d failed",
            len(self.results),
            sum(1 for r in self.results if r.error),
        )
        return summary

    def _execute(self, entry: dict[str, Any]) -> BenchmarkRecord:
        """Execute one benchmark scenario."""
        t0 = time.time()
        run_id = entry.get("run_id", f"run-{self._run_idx:04d}")

        record = BenchmarkRecord(
            run_id=run_id,
            scenario_id=entry["scenario_id"],
            task_family=entry["task_family"],
            condition=entry["condition"],
            lep_code=entry.get("lep_code", ""),
            topology=entry.get("topology", ""),
            propagation_mode=entry.get("propagation_mode", "single_origin"),
            repetition_index=entry.get("repetition_index", 0),
            pair_tag=entry.get("pair_tag", ""),
            is_baseline=entry.get("is_baseline", False),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        try:
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

            from generation.runner import ScenarioRunner

            backend = self.llm_backend or self._default_backend()
            runner = ScenarioRunner(llm_backend=backend, dry_run=self.manifest.dry_run)

            result = runner.run(spec, self.manifest.fixture_root)
            trace = result.trace

            # Write trace
            trace_dir = (
                self.manifest.output_dir or Path("benchmark_output")
            ) / "traces"
            trace_dir.mkdir(parents=True, exist_ok=True)
            trace_path = trace_dir / f"{entry['scenario_id']}_trace.json"
            with open(trace_path, "w") as f:
                json.dump(trace.to_dict(), f, indent=2, default=str)
            record.trace_path = str(trace_path)

            # Populate record
            record.trace_id = trace.trace_id
            record.success = result.runner_success
            record.error = result.error
            record.num_events = len(trace.events)

            # Event statistics
            record.num_tool_calls = sum(
                1 for e in trace.events if e.event_type.value == "tool_call"
            )
            record.num_handoffs = sum(
                1 for e in trace.events if e.event_type.value == "agent_handoff"
            )
            record.num_turns = sum(
                getattr(e, "turn_number", 0)
                for e in trace.events
                if e.event_type.value == "agent_message"
            )
            record.num_stages = len(set(
                e.agent_role for e in trace.events
                if e.agent_role
            ))

            # LEP labels
            record.injection_event_ids = [
                e.event_id for e in trace.events
                if getattr(e, "event_labels", None) and e.event_labels.is_injection_origin
            ]
            record.consumption_event_ids = [
                e.event_id for e in trace.events
                if getattr(e, "event_labels", None) and e.event_labels.consumes_perturbed_info
            ]
            record.propagation_event_ids = [
                e.event_id for e in trace.events
                if getattr(e, "event_labels", None) and e.event_labels.forwards_perturbed_info
            ]
            record.recovery_event_ids = [
                e.event_id for e in trace.events
                if getattr(e, "event_labels", None) and e.event_labels.recovers_from_perturbation
            ]
            record.injection_fired = len(record.injection_event_ids) > 0
            record.recovery_detected = len(record.recovery_event_ids) > 0

            # Downstream failure
            record.downstream_failure = any(
                getattr(e, "event_labels", None) and e.event_labels.introduces_downstream_failure
                for e in trace.events
            )
            for e in trace.events:
                if (getattr(e, "event_labels", None)
                        and e.event_labels.introduces_downstream_failure):
                    record.failure_type = getattr(e.event_labels, "failure_type", "")
                    break

            # Final output
            for e in reversed(trace.events):
                if e.event_type.value == "final_response":
                    record.final_output = (e.output_text or e.input_text or "")[:500]
                    break

            # Evaluator
            eval_result = self._evaluate(trace, spec)
            record.task_success = eval_result.get("task_success", False)
            record.evaluator_passed = eval_result.get("passed", True)
            record.evaluator_errors = eval_result.get("errors", [])

            # Propagation tracking
            record.perturbation_reached_target = (
                record.injection_fired
            )
            record.perturbation_propagated_to_consumer = (
                len(record.consumption_event_ids) > 0
            )
            record.perturbation_propagated_to_producer = (
                len(record.propagation_event_ids) > 0
            )

        except Exception as e:
            logger.error("Benchmark run %s failed: %s", run_id, e, exc_info=True)
            record.error = str(e)
            record.success = False

        record.runtime_seconds = round(time.time() - t0, 2)
        return record

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _default_backend(self):
        from generation.runner import DryRunBackend
        return DryRunBackend()

    def _build_workflow_config(self, entry: dict[str, Any]) -> WorkflowConfig:
        return WorkflowConfig(
            topology=entry.get("topology", "linear_2"),
            sharing_policy="handoff_summary_only",
            memory_mode="ephemeral_shared",
            verification_mode="self_check",
            max_events=self.manifest.max_events,
            max_agent_turns=self.manifest.max_agent_turns,
            timeout_seconds=300,
            model_name=self.manifest.model_name,
            temperature=0.1,
            seed=self.manifest.seed,
            allow_parallel_agents=False,
            allow_retries=True,
            propagation_mode=entry.get("propagation_mode", "single_origin"),
        )

    def _fixture_id(self, entry: dict[str, Any]) -> str:
        family = entry["task_family"]
        FIXTURE_MAP = {
            "code_review": "code_review_easy",
            "financial_analysis": "financial_clean",
            "research_synthesis": "research_conflicting",
            "competitive_intelligence": "competitive_pricing",
        }
        return FIXTURE_MAP.get(family, f"{family}_default")

    def _resolve_lep(self, code: str) -> LEPConfig:
        # Build lookup from tasks.registry at call time (cheap, cached)
        from tasks.registry import get_default_leps, get_task_registry
        for tf in get_task_registry():
            for lep in get_default_leps(tf):
                if lep.code == code:
                    return lep
        return LEPConfig(
            code=code, name=code, category="unknown",
            target_agent="", description=f"Auto-resolved: {code}",
        )

    def _evaluate(self, trace: Trace, spec: ScenarioSpec) -> dict[str, Any]:
        """Run task evaluator on the trace."""
        from evaluators.base_evaluator import get_evaluator
        from environment.workspace import Workspace

        evaluator = get_evaluator(spec.task_family)
        if evaluator is None:
            return {"passed": True, "errors": [], "task_success": True}

        ws = Workspace(
            (self.manifest.output_dir or Path("benchmark_output"))
            / f"ws_{spec.scenario_id}"
        )
        result = evaluator.evaluate(trace, ws, spec)

        d: dict[str, Any]
        if hasattr(result, 'to_dict'):
            d = result.to_dict()
        elif hasattr(result, '__dataclass_fields__'):
            d = {f.name: getattr(result, f.name) for f in result.__dataclass_fields__}
        else:
            d = dict(result)

        d.setdefault("task_success", d.get("task_success", False))
        d.setdefault("passed", d.get("task_success", True))
        d.setdefault("errors", d.get("failure_types", d.get("evaluator_notes", [])))
        return d

    # ── Persistence ───────────────────────────────────────────────────────────

    def _persist_record(self, record: BenchmarkRecord) -> None:
        """Append record to JSONL."""
        out_dir = self.manifest.output_dir or Path("benchmark_output")
        out_dir.mkdir(parents=True, exist_ok=True)
        records_path = out_dir / "benchmark_records.jsonl"
        with open(records_path, "a") as f:
            f.write(json.dumps(record.to_dict()) + "\n")

    def _summarize(self) -> dict[str, Any]:
        """Aggregate statistics across all runs."""
        total = len(self.results)
        failed = sum(1 for r in self.results if r.error)
        injection_hits = sum(1 for r in self.results if r.injection_fired)
        consumption_hits = sum(1 for r in self.results if len(r.consumption_event_ids) > 0)
        propagation_hits = sum(1 for r in self.results if len(r.propagation_event_ids) > 0)
        recovery_hits = sum(1 for r in self.results if r.recovery_detected)
        failures = sum(1 for r in self.results if r.downstream_failure)
        task_success = sum(1 for r in self.results if r.task_success)

        # Per-condition breakdown
        conditions: dict[str, int] = {}
        for r in self.results:
            conditions[r.condition] = conditions.get(r.condition, 0) + 1

        # Per-topology breakdown
        topologies: dict[str, dict[str, int]] = {}
        for r in self.results:
            t = topologies.setdefault(r.topology, {"total": 0, "injection_fired": 0, "downstream_failure": 0})
            t["total"] += 1
            if r.injection_fired:
                t["injection_fired"] += 1
            if r.downstream_failure:
                t["downstream_failure"] += 1

        # Per-propagation-mode breakdown
        prop_modes: dict[str, dict[str, int]] = {}
        for r in self.results:
            m = prop_modes.setdefault(r.propagation_mode, {"total": 0, "injection_fired": 0, "downstream_failure": 0})
            m["total"] += 1
            if r.injection_fired:
                m["injection_fired"] += 1
            if r.downstream_failure:
                m["downstream_failure"] += 1

        return {
            "total_runs": total,
            "successful_runs": total - failed,
            "failed_runs": failed,
            "injection_fired": injection_hits,
            "injection_fired_rate": round(injection_hits / max(total, 1), 3),
            "downstream_failures": failures,
            "downstream_failure_rate": round(failures / max(total, 1), 3),
            "recovery_rate": round(recovery_hits / max(total, 1), 3),
            "consumption_hits": consumption_hits,
            "propagation_hits": propagation_hits,
            "recovery_hits": recovery_hits,
            "task_success": task_success,
            "conditions": conditions,
            "per_topology": topologies,
            "per_propagation_mode": prop_modes,
            "avg_runtime_seconds": round(
                sum(r.runtime_seconds for r in self.results) / max(total, 1), 2
            ),
        }

    def _write_summary(self, summary: dict[str, Any]) -> None:
        """Write summary JSON and CSV."""
        out_dir = self.manifest.output_dir or Path("benchmark_output")
        out_dir.mkdir(parents=True, exist_ok=True)

        summary_path = out_dir / "benchmark_summary.json"
        with open(summary_path, "w") as f:
            json.dump(summary, f, indent=2)

        # CSV of all records
        csv_path = out_dir / "benchmark_records.csv"
        if self.results:
            fieldnames = list(self.results[0].to_dict().keys())
            with open(csv_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for r in self.results:
                    writer.writerow(r.to_dict())

        logger.info("Summary written: %s, %s", summary_path, csv_path)


# ── CLI entry point ─────────────────────────────────────────────────────────

def run_benchmark(
    topologies: list[str] | None = None,
    task_families: list[str] | None = None,
    lep_codes: list[str] | None = None,
    num_repetitions: int = 3,
    max_events: int = 50,
    output_dir: str | None = None,
    dry_run: bool = True,
    fixture_root: str | None = None,
    propagation_modes: list[str] | None = None,
) -> dict[str, Any]:
    """Convenience entry point for benchmark execution.

    Args:
        topologies:      Topology IDs to benchmark (None → all).
        task_families:   Task family names (None → all).
        lep_codes:       LEP codes to test (None → all).
        num_repetitions: Repetitions per cell.
        max_events:      Max trace events per scenario.
        output_dir:      Where to write results.
        dry_run:         Use DryRunBackend.
        fixture_root:    Root directory for workspace fixtures.
        propagation_modes: Propagation modes to test (None → defaults).

    Returns:
        Summary dict.
    """
    from tasks.registry import get_default_leps, get_task_registry

    task_registry = get_task_registry()
    available_leps = []
    seen = set()
    for tf in (task_families or list(task_registry.keys())):
        for lep in get_default_leps(tf):
            if lep.code not in seen:
                available_leps.append(lep)
                seen.add(lep.code)

    _default_prop = ["single_origin", "one_to_many", "many_to_one"]
    manifest = BenchmarkManifest(
        topologies=topologies or TOPOLOGIES,
        task_families=task_families or list(task_registry.keys()),
        lep_configs=available_leps if not lep_codes else [
            lep for lep in available_leps if lep.code in lep_codes
        ],
        num_repetitions=num_repetitions,
        max_events=max_events,
        dry_run=dry_run,
        output_dir=Path(output_dir) if output_dir else None,
        fixture_root=Path(fixture_root) if fixture_root else None,
        propagation_modes=propagation_modes or _default_prop,
    )
    runner = BenchmarkRunner(manifest)
    return runner.run()
