"""Benchmark runner — scales the pilot into a full experimental benchmark.

Differences from pilot:
- Uses a configurable benchmark manifest (not hardcoded pilot lists)
- Supports ALL topologies, ALL LEPs, ALL propagation modes
- Supports multiple repetitions for statistical significance
- Emits per-repetition trace files plus an aggregated results CSV
- Does not require real API keys — dry-run backends work offline
- Fixture-aware: each plan entry carries an explicit fixture_id and task_variant
  sourced from the fixture's own manifest.json
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
from schemas.scenario import CONDITIONS, TOPOLOGIES, TOPOLOGY_PROPAGATION_MODES

logger = logging.getLogger("benchmark")


# ── Fixture discovery ───────────────────────────────────────────────────────────


def _load_fixture_manifest(fixture_dir: Path) -> dict[str, Any]:
    """Read a fixture's manifest.json and return the parsed dict.

    Returns an empty dict when the manifest is missing or unreadable so
    callers can degrade gracefully.
    """
    manifest_path = fixture_dir / "manifest.json"
    if not manifest_path.is_file():
        return {}
    try:
        with open(manifest_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def discover_fixtures(
    fixture_root: Path | None = None,
    *,
    task_families: list[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Discover all fixtures under ``fixture_root`` grouped by task_family.

    Each returned dict carries the raw manifest plus the derived
    ``fixture_dir`` path so callers do not need to re-resolve it.

    The default ``fixture_root`` is ``<repo>/workspace_fixtures``.  Pass
    ``None`` to skip discovery and return an empty mapping.

    ``task_families`` limits discovery to the listed families; ``None``
    means "all families present on disk".
    """
    if fixture_root is None:
        # Default: workspace_fixtures next to this file's package root.
        fixture_root = Path(__file__).resolve().parent.parent / "workspace_fixtures"

    result: dict[str, list[dict[str, Any]]] = {}
    if not fixture_root.is_dir():
        return result

    allowed_families = set(task_families) if task_families is not None else None
    for fixture_dir in sorted(fixture_root.iterdir()):
        if not fixture_dir.is_dir():
            continue
        manifest = _load_fixture_manifest(fixture_dir)
        family = manifest.get("task_family")
        if not family:
            continue
        if allowed_families is not None and family not in allowed_families:
            continue
        result.setdefault(family, []).append({
            "fixture_id": manifest.get("fixture_id", fixture_dir.name),
            "task_family": family,
            "task_variant": manifest.get("task_variant", "default"),
            "fixture_dir": fixture_dir,
            "manifest": manifest,
        })
    return result


def fixture_id_for_task_family(task_family: str) -> str:
    """Resolve the default fixture consistently for planning and execution.

    Kept for backward compatibility with existing tests and the
    ``_execute`` fallback path.  New code should prefer explicit fixture
    selection via ``BenchmarkManifest.fixture_ids`` or discovery.
    """
    fixture_ids = {
        "code_review": "code_review_easy",
        "financial_analysis": "financial_clean",
        "research_synthesis": "research_conflicting",
    }
    return fixture_ids.get(task_family, f"{task_family}_default")


# ── Execution record ───────────────────────────────────────────────────────

@dataclass
class BenchmarkRecord:
    """One execution result for the benchmark."""
    run_id: str
    scenario_id: str
    task_family: str
    condition: str          # benign | single_lep
    lep_code: str
    topology: str
    propagation_mode: str
    repetition_index: int
    trace_id: str = ""
    success: bool = False
    dataset_eligible: bool = False
    termination_reason: str = "unknown"
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
    # Execution variant
    execution_variant: str = "standard"
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
            "dataset_eligible": self.dataset_eligible,
            "termination_reason": self.termination_reason,
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
            "execution_variant": self.execution_variant,
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
        fixture_root:     Root directory containing workspace_fixtures/.
        fixture_ids:      Optional explicit fixture selection per task family.
                          Maps task_family -> list[fixture_id].  When None the
                          planner discovers all applicable fixtures from disk.
                          Use a single-element list to restrict to one fixture.
        smoke_fixtures:   If True, restrict to the legacy single-default fixture
                          per task family (backward-compatible smoke mode).
    """
    topologies: list[str] = field(default_factory=list)
    task_families: list[str] = field(default_factory=list)
    lep_configs: list[LEPConfig] = field(default_factory=list)
    num_repetitions: int = 1
    num_benign_repetitions: int = 5
    max_events: int = 300
    max_agent_turns: int = 80
    model_name: str = "claude-sonnet-5"
    temperature: float = 0.4
    dry_run: bool = True
    output_dir: Optional[Path] = None
    fixture_root: Optional[Path] = None
    seed: int = 42
    backend_name: str = "api"
    vllm_url: str = "http://localhost:8000/v1"
    propagation_modes: list[str] = field(default_factory=lambda: [
        "single_origin",
        "one_to_many",
        "many_to_one",
    ])
    node_id: int = 0
    num_nodes: int = 1
    fixture_ids: Optional[dict[str, list[str]]] = None
    smoke_fixtures: bool = False

    # ── Fixture resolution helpers ──────────────────────────────────────────────

    def _resolve_fixtures_for_family(
        self, task_family: str,
    ) -> list[dict[str, Any]]:
        """Return the fixtures to plan for a task family.
    
        Resolution order:
        1. Explicit fixture_ids selection
        2. smoke_fixtures default fixture
        3. all discovered fixtures
        4. legacy fallback only when discovery is unavailable
        """
        discovered = discover_fixtures(
            self.fixture_root,
            task_families=[task_family],
        ).get(task_family, [])
    
        by_id = {
            fx["fixture_id"]: fx
            for fx in discovered
        }
    
        # 1. Explicit fixture selection must be exact.
        if self.fixture_ids and task_family in self.fixture_ids:
            requested = self.fixture_ids[task_family]
    
            missing = [
                fixture_id
                for fixture_id in requested
                if fixture_id not in by_id
            ]
    
            if missing:
                raise ValueError(
                    f"Unknown fixture(s) for task family '{task_family}': "
                    f"{sorted(missing)}. "
                    f"Available fixtures: {sorted(by_id)}"
                )
    
            return [by_id[fixture_id] for fixture_id in requested]
    
        # 2. Smoke mode uses the real default fixture manifest.
        if self.smoke_fixtures:
            default_id = fixture_id_for_task_family(task_family)
    
            if default_id not in by_id:
                raise ValueError(
                    f"Default smoke fixture '{default_id}' for task family "
                    f"'{task_family}' was not found. "
                    f"Available fixtures: {sorted(by_id)}"
                )
    
            return [by_id[default_id]]
    
        # 3. Normal full benchmark: use all discovered fixtures.
        if discovered:
            return discovered
    
        # 4. Legacy fallback only when fixture discovery is unavailable.
        default_id = fixture_id_for_task_family(task_family)
        return [{
            "fixture_id": default_id,
            "task_family": task_family,
            "task_variant": "default",
            "fixture_dir": Path("."),
            "manifest": {},
        }]

    def _compatible_topologies(
        self, fixture: dict[str, Any], requested: list[str],
    ) -> list[str]:
        """Return the intersection of requested topologies and those
        supported by the fixture manifest.

        Falls back to all requested topologies when the fixture manifest
        does not declare ``supported_topologies`` (legacy/default behavior).
        """
        supported = set(fixture.get("manifest", {}).get("supported_topologies", []))
        if not supported:
            return list(requested)
        return [t for t in requested if t in supported]

    def _compatible_leps(
        self, fixture: dict[str, Any], family_leps: list[LEPConfig],
    ) -> list[LEPConfig]:
        """Return family-level LEP configs filtered to those the fixture supports.

        Falls back to all family LEPs when the fixture manifest does not
        declare ``supported_leps`` (legacy/default behavior).
        """
        supported = set(fixture.get("manifest", {}).get("supported_leps", []))
        if not supported:
            return list(family_leps)
        return [lep for lep in family_leps if lep.code in supported]

    def build_plan(self) -> list[dict[str, Any]]:
        """Build the full cross-product execution plan.

        Produces two types of entries:
        - Benign: one per (fixture_id, topology, execution_variant, rep).
          "standard" is always emitted; "memory_enabled" is emitted only
          if at least one LEP for this task family requires memory.
        - LEP: one per (LEP, propagation_mode, rep), paired to the
          correct execution_variant via pair_tag.

        Clean-reference identity for anomaly detection:
            (fixture_id, topology, execution_variant)

        pair_tag links one LEP execution to its matched benign repetition
        (bookkeeping). Behavioral reference pools ALL benign runs with
        the same clean-reference identity.
        """
        plan: list[dict[str, Any]] = []
        idx = 0

        for task_family in self.task_families:
            fixtures = self._resolve_fixtures_for_family(task_family)
            # Pre-compute which LEPs need memory for this task family
            family_leps = [
                lep for lep in self.lep_configs
                if lep.task_family == task_family or not lep.task_family
            ]
            has_memory_lep = any(lep.requires_memory for lep in family_leps)
            variants = ["standard"] + (["memory_enabled"] if has_memory_lep else [])

            for fixture in fixtures:
                fixture_id = fixture["fixture_id"]
                task_variant = fixture.get("task_variant", "default")
                fixture_topologies = self._compatible_topologies(fixture, self.topologies)
                fixture_leps = self._compatible_leps(fixture, family_leps)

                for topology in fixture_topologies:
                    allowed_modes = [
                        mode for mode in TOPOLOGY_PROPAGATION_MODES.get(topology, [])
                        if mode in self.propagation_modes
                    ]
                    if not allowed_modes:
                        continue

                    # ── Benign entries: one per variant × benign_rep ───────────
                    for variant in variants:
                        for rep in range(self.num_benign_repetitions):
                            pair_tag = (
                                f"b_{topology}_{fixture_id}_{variant}_{rep:02d}"
                            )
                            plan.append({
                                "run_id": f"run-{idx:04d}",
                                "scenario_id": (
                                    f"{topology}_{fixture_id}_{variant}_{rep:02d}_benign"
                                ),
                                "task_family": task_family,
                                "fixture_id": fixture_id,
                                "task_variant": task_variant,
                                "condition": "benign",
                                "execution_variant": variant,
                                "lep_codes": [],
                                "topology": topology,
                                "propagation_mode": allowed_modes[0],
                                "lep_code": "",
                                "repetition_index": rep,
                                "pair_tag": pair_tag,
                                "is_baseline": True,
                            })
                            idx += 1

                    # ── LEP entries: one per LEP × mode × rep ────────────────
                    lep_mode_combos = [
                        (lep, mode)
                        for lep in fixture_leps
                        for mode in allowed_modes
                    ]
                    for lep_config, prop_mode in lep_mode_combos:
                        exec_variant = (
                            "memory_enabled" if lep_config.requires_memory else "standard"
                        )
                        for rep in range(self.num_repetitions):
                            pair_tag = (
                                f"b_{topology}_{fixture_id}_{exec_variant}_{rep:02d}"
                            )
                            plan.append({
                                "run_id": f"run-{idx:04d}",
                                "scenario_id": (
                                    f"{topology}_{fixture_id}_"
                                    f"{lep_config.code}_{prop_mode}_{rep:02d}_lep"
                                ),
                                "task_family": task_family,
                                "fixture_id": fixture_id,
                                "task_variant": task_variant,
                                "condition": "single_lep",
                                "execution_variant": exec_variant,
                                "lep_codes": [lep_config.code],
                                "topology": topology,
                                "propagation_mode": prop_mode,
                                "lep_code": lep_config.code,
                                "repetition_index": rep,
                                "pair_tag": pair_tag,
                                "is_baseline": False,
                            })
                            idx += 1

        return plan


# ── Benchmark runner ────────────────────────────────────────────────────────

class BenchmarkRunner:
    """Executes the full benchmark and writes results.

    Usage:
        manifest = BenchmarkManifest(
            topologies=["review_loop", "star", "mesh"],
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

        # Apply node partition if multi-node
        if self.manifest.num_nodes > 1:
            from benchmark.split_merge import split_plan
            plan = split_plan(plan, self.manifest.node_id, self.manifest.num_nodes)
            logger.info(
                "Node %d/%d: executing %d of %d plan entries",
                self.manifest.node_id, self.manifest.num_nodes,
                len(plan), len(self.manifest.build_plan()),
            )

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

        audit_path = self.audit_injection_counts()
        summary["injection_count_audit"] = audit_path
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
            execution_variant=entry.get("execution_variant", "standard"),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        try:
            wcfg = self._build_workflow_config(entry)
            lep_configs = [self._resolve_lep(c, entry["task_family"]) for c in entry.get("lep_codes", [])]

            spec = ScenarioSpec(
                scenario_id=entry["scenario_id"],
                task_family=entry["task_family"],
                task_variant=entry.get("task_variant", "default"),
                fixture_id=entry.get("fixture_id") or fixture_id_for_task_family(entry["task_family"]),
                workflow_config=wcfg,
                lep_configs=lep_configs,
                condition=entry["condition"],
                repetition_index=entry.get("repetition_index", 0),
            )

            from generation.runner import ScenarioRunner

            backend = self.llm_backend or self._default_backend()
            runner = ScenarioRunner(llm_backend=backend, dry_run=self.manifest.dry_run,
                                    max_events=self.manifest.max_events)

            # Use a short, descriptive execution_id so trace_id encodes
            # task family, topology, LEP, mode, repetition, and variant.
            short_id = self._short_trace_id(entry)
            result = runner.run(spec, self.manifest.fixture_root, execution_id=short_id)
            trace = result.trace

            # Stamp execution_variant into trace metadata so downstream
            # analysis can group by (fixture_id, topology, execution_variant)
            # without reconstructing it from other fields.
            trace.metadata["execution_variant"] = entry.get(
                "execution_variant", "standard"
            )

            # Keep incomplete executions for auditing, outside the dataset directory
            # scanned by clean-reference construction and detector training.
            trace_dir = (
                self.manifest.output_dir or Path("benchmark_output")
            ) / ("traces" if result.dataset_eligible else "rejected_traces")
            trace_dir.mkdir(parents=True, exist_ok=True)
            trace_path = trace_dir / f"{entry['scenario_id']}_trace.json"
            with open(trace_path, "w") as f:
                json.dump(trace.to_dict(), f, indent=2, default=str)
            record.trace_path = str(trace_path)

            # Populate record
            record.trace_id = trace.trace_id
            record.success = result.runner_success
            record.dataset_eligible = result.dataset_eligible
            record.termination_reason = result.termination_reason
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

            # Downstream failure — from trace.labels (authoritative, set by
            # the ScenarioRunner task evaluator). Do NOT reconstruct from
            # individual event labels; do NOT re-run the task evaluator.
            record.downstream_failure = trace.labels.downstream_failure
            record.task_success = trace.labels.task_success
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

            # Evaluator — use the authoritative result from ScenarioRunner.
            # The runner already evaluated the trace; do NOT re-run here.
            eval_result = result.evaluation or {}
            record.evaluator_passed = bool(eval_result.get("overall_passed", True))
            task_eval = eval_result.get("task", {})
            record.evaluator_errors = task_eval.get("errors", [])

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
        if self.manifest.dry_run:
            from generation.runner import DryRunBackend
            return DryRunBackend()
        if self.manifest.backend_name == "vllm":
            from backend.hf_backend import HFBackend
            return HFBackend(
                model=self.manifest.model_name,
                base_url=self.manifest.vllm_url,
            )
        from backend.api_backend import APIBackend
        return APIBackend(
            model=self.manifest.model_name,
            temperature=self.manifest.temperature,
        )

    def _build_workflow_config(self, entry: dict[str, Any]) -> WorkflowConfig:
        # Memory mode depends on execution_variant:
        #   standard        → ephemeral_private (no shared memory)
        #   memory_enabled  → ephemeral_shared  (shared memory; benign runs
        #                     use clean content, memory LEPs poison it)
        memory_mode = (
            "ephemeral_shared"
            if entry.get("execution_variant") == "memory_enabled"
            else "ephemeral_private"
        )
        return WorkflowConfig(
            topology=entry.get("topology", "review_loop"),
            sharing_policy="handoff_summary_only",
            memory_mode=memory_mode,
            verification_mode="self_check",
            max_events=self.manifest.max_events,
            max_agent_turns=self.manifest.max_agent_turns,
            timeout_seconds=300,
            model_name=self.manifest.model_name,
            temperature=self.manifest.temperature,
            seed=self.manifest.seed,
            allow_parallel_agents=False,
            allow_retries=True,
            propagation_mode=entry.get("propagation_mode", "single_origin"),
        )

    @staticmethod
    def _short_trace_id(entry: dict[str, Any]) -> str:
        """Build a compact, collision-safe execution_id.

        Format for LEP runs:
            {fixture}.{topo}.{lep}.{mode}.{rep:02d}.{variant_short}
        Format for benign runs:
            {fixture}.{topo}.{rep:02d}.{variant_short}.benign

        Examples:
            financial_clean.coord.MEMORY_POISONING.single_origin.00.mem
            financial_clean.coord.INPUT_DISREGARD.single_origin.00.std
            financial_clean.coord.00.std.benign
            financial_clean.coord.00.mem.benign

        The runner appends _a (benign) or _b (lep) to produce the full trace_id.
        """
        fixture = entry.get("fixture_id", "unknown")
        TOP = {
            "review_loop": "rl",
            "branch_and_verify": "bv",
            "coordinator_workers": "coord",
        }
        VARIANT_SHORT = {
            "standard": "std",
            "memory_enabled": "mem",
        }
        topo = TOP.get(entry.get("topology", ""), entry.get("topology", ""))
        mode = entry.get("propagation_mode", "single_origin")
        rep = f"{entry.get('repetition_index', 0):02d}"
        variant_short = VARIANT_SHORT.get(
            entry.get("execution_variant", "standard"), "std"
        )
        lep_code = entry.get("lep_code", "")

        if lep_code:
            return f"{fixture}.{topo}.{lep_code}.{mode}.{rep}.{variant_short}"
        return f"{fixture}.{topo}.{rep}.{variant_short}.benign"

    def _resolve_lep(self, code: str, task_family: str = "") -> LEPConfig:
        # Build lookup from tasks.registry at call time (cheap, cached)
        from dataclasses import replace
        from tasks.registry import get_default_leps, get_task_registry
        # This run's own family first: the same LEP code exists in several
        # families with different target agents and triggers, so the old
        # first-match loop could hand a financial run code_review's config.
        families = ([task_family] if task_family else []) + [
            tf for tf in get_task_registry() if tf != task_family]
        for tf in families:
            for lep in get_default_leps(tf):
                if lep.code == code:
                    # copy: get_default_leps returns shared module-level
                    # objects, so assigning to them leaks across runs
                    return replace(lep, task_family=task_family or tf)
        return LEPConfig(
            code=code, name=code, category="unknown",
            target_agent="", description=f"Auto-resolved: {code}",
            task_family=task_family,
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

    def audit_injection_counts(self) -> str:
        """Verify that each trace's injection_origin_count matches its expected count.

        Writes mismatched trace filenames to ``injection_count_mismatches.txt``
        in the output directory. Returns the path to that file.
        """
        out_dir = Path(self.manifest.output_dir or "benchmark_output")
        trace_dir = out_dir / "traces"
        out_dir.mkdir(parents=True, exist_ok=True)
        mismatches_path = out_dir / "injection_count_mismatches.txt"
        mismatches: list[str] = []

        from generation.injection_origins import expected_injection_origins

        trace_paths = sorted(trace_dir.glob("*_trace.json")) + sorted(
            (out_dir / "rejected_traces").glob("*_trace.json")
        )
        for trace_path in trace_paths:
            try:
                with open(trace_path) as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue

            meta = data.get("metadata", {})
            condition = meta.get("condition", "")
            prop_mode = meta.get("propagation_mode", "single_origin")
            lep_codes = meta.get("lep_codes", [])
            actual = data.get("injection_origin_count", -1)

            expected = expected_injection_origins(
                condition=condition, lep_codes=lep_codes, propagation_mode=prop_mode,
                topology=meta.get("topology", "review_loop"),
            )

            if actual != expected:
                mismatches.append(
                    f"{trace_path.name}: expected={expected} actual={actual} "
                    f"condition={condition} prop_mode={prop_mode} leps={lep_codes}"
                )

        with open(mismatches_path, "w") as f:
            for line in mismatches:
                f.write(line + "\n")

        logger.info(
            "Injection count audit: %d traces checked, %d mismatches → %s",
            len(trace_paths),
            len(mismatches),
            mismatches_path,
        )
        return str(mismatches_path)


# ── CLI entry point ─────────────────────────────────────────────────────────

def run_benchmark(
    topologies: list[str] | None = None,
    task_families: list[str] | None = None,
    lep_codes: list[str] | None = None,
    num_repetitions: int = 1,
    num_benign_repetitions: int = 5,
    max_events: int = 300,
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
        num_benign_repetitions=num_benign_repetitions,
        max_events=max_events,
        dry_run=dry_run,
        output_dir=Path(output_dir) if output_dir else None,
        fixture_root=Path(fixture_root) if fixture_root else None,
        propagation_modes=propagation_modes or _default_prop,
    )
    runner = BenchmarkRunner(manifest)
    return runner.run()
