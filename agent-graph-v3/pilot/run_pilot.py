"""Main pilot runner — delegates execution to BenchmarkRunner with filtered scopes.

Usage (dry-run, no API key needed):
    python -m pilot.run_pilot --dry-run

Usage (real model):
    LLM_API_KEY=sk-... python -m pilot.run_pilot --real-model

Flags:
    --dry-run           Use DryRunBackend (default)
    --output-dir DIR    Where to write pilot outputs
    --fixture-dir DIR   Root of workspace_fixtures/
    --task FAMILY       Run only this task family
    --lep CODE          Run only this LEP
    --topology ID       Run only this topology (default: branch_and_verify)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Ensure v3 root is on path
_V3_ROOT = Path(__file__).resolve().parent.parent
if str(_V3_ROOT) not in sys.path:
    sys.path.insert(0, str(_V3_ROOT))

from schemas import SCHEMA_VERSION
from schemas.scenario import TOPOLOGY_PROPAGATION_MODES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("pilot")


def run_pilot(
    dry_run: bool = True,
    output_dir: Path | None = None,
    fixture_root: Path | None = None,
    max_executions: int | None = None,
    task_filter: str | None = None,
    lep_filter: str | None = None,
    topology_filter: str | None = None,
) -> dict[str, Any]:
    """Run the pilot via BenchmarkRunner with optional filters.

    Args:
        dry_run: Use DryRunBackend.
        output_dir: Where to write pilot outputs.
        fixture_root: Root of workspace_fixtures/.
        max_executions: Hard cap on total scenarios (for smoke tests).
        task_filter: Run only this task family.
        lep_filter: Run only this LEP code.
        topology_filter: Run only this topology ID.
    """
    from pilot.config import PilotConfig, PILOT_LEP_CONFIGS, PILOT_TASK_FAMILIES
    from pilot.audit_report import AuditReport
    from benchmark.benchmark_runner import (
        BenchmarkManifest,
        BenchmarkRunner,
        BenchmarkRecord,
    )

    # Resolve paths
    v3_root = Path(__file__).resolve().parent.parent
    output_dir = output_dir or v3_root / "pilot_output"
    output_dir.mkdir(parents=True, exist_ok=True)
    fixture_root = fixture_root or v3_root / "workspace_fixtures"

    # Load pilot config
    config = PilotConfig()

    # Apply filters
    task_families = [task_filter] if task_filter else list(PILOT_TASK_FAMILIES)
    lep_configs = [lep for lep in PILOT_LEP_CONFIGS
                   if not lep_filter or lep.code == lep_filter]
    topologies = [topology_filter] if topology_filter else [config.workflow_config.topology]

    # Validate filters
    if task_filter and task_filter not in PILOT_TASK_FAMILIES:
        logger.error("Unknown task family: %s (choose from %s)", task_filter, PILOT_TASK_FAMILIES)
        sys.exit(1)

    if lep_filter and not any(lep.code == lep_filter for lep in PILOT_LEP_CONFIGS):
        logger.error("Unknown LEP code: %s", lep_filter)
        sys.exit(1)

    if topology_filter and topology_filter not in TOPOLOGY_PROPAGATION_MODES:
        logger.warning(
            "Topology '%s' not in known set %s — will use single_origin propagation.",
            topology_filter, list(TOPOLOGY_PROPAGATION_MODES.keys()),
        )

    logger.info("=" * 60)
    logger.info("AGENT-GRAPH V3 PILOT (via BenchmarkRunner)")
    logger.info("Schema: v%s | Mode: %s", SCHEMA_VERSION, "dry-run" if dry_run else "real-model")
    logger.info("Topologies: %s", ", ".join(topologies))
    logger.info("Task families: %s", ", ".join(task_families))
    logger.info("LEPs: %s", ", ".join(l.code for l in lep_configs) or "none (benign/cf)")
    logger.info("Output: %s", output_dir)
    logger.info("=" * 60)

    # Build propagation modes per topology
    all_prop_modes = set()
    for t in topologies:
        all_prop_modes.update(TOPOLOGY_PROPAGATION_MODES.get(t, ["single_origin"]))
    prop_modes = sorted(all_prop_modes)

    # Build BenchmarkManifest — delegates plan construction to the benchmark
    # runner, which handles the cross-product correctly.
    manifest = BenchmarkManifest(
        topologies=topologies,
        task_families=task_families,
        lep_configs=lep_configs,
        num_repetitions=1,
        max_events=config.workflow_config.max_events,
        max_agent_turns=config.workflow_config.max_agent_turns,
        model_name=config.workflow_config.model_name,
        temperature=config.workflow_config.temperature,
        dry_run=dry_run,
        output_dir=output_dir,
        fixture_root=fixture_root,
        seed=config.workflow_config.seed,
        backend_name="api",
        propagation_modes=prop_modes,
    )

    logger.info(
        "Benchmark plan: %d topologies × %d tasks × %d LEPs × %d prop_modes × 1 rep",
        len(topologies), len(task_families), len(lep_configs), len(prop_modes),
    )

    # Build backend
    if dry_run:
        from generation.runner import DryRunBackend
        llm_backend = DryRunBackend()
    else:
        from backend.api_backend import APIBackend
        llm_backend = APIBackend(
            model=config.workflow_config.model_name,
            temperature=config.workflow_config.temperature,
        )

    # Run via BenchmarkRunner
    runner = BenchmarkRunner(manifest, llm_backend=llm_backend)
    benchmark_summary = runner.run()

    # Apply max_executions cap — keep only the first N records and remove
    # the corresponding trace files so the pilot output stays consistent.
    if max_executions:
        excess = runner.results[max_executions:]
        runner.results = runner.results[:max_executions]
        for extra_brec in excess:
            extra_src = output_dir / "traces" / f"{extra_brec.scenario_id}_trace.json"
            if extra_src.exists():
                extra_src.unlink()
            extra_dst = output_dir / f"{extra_brec.scenario_id}_trace.json"
            if extra_dst.exists():
                extra_dst.unlink()

    # Convert BenchmarkRecords → ExecutionRecords (pilot format)
    records = []
    for brec in runner.results:
        rec = _benchmark_record_to_execution(brec)
        records.append(rec)

    # Write pilot_records.jsonl
    records_path = output_dir / "pilot_records.jsonl"
    with open(records_path, "w") as f:
        for rec in records:
            f.write(json.dumps(rec.to_dict()) + "\n")

    # Copy/rename trace files to pilot naming convention
    # Benchmark writes traces as {scenario_id}_trace.json into output_dir/traces/
    # Pilot expects them directly in output_dir with the same name.
    trace_src_dir = output_dir / "traces"
    for brec in runner.results:
        src = trace_src_dir / f"{brec.scenario_id}_trace.json"
        dst = output_dir / f"{brec.scenario_id}_trace.json"
        if src.exists():
            src.rename(dst)
        else:
            logger.debug("No trace file for %s", brec.scenario_id)

    # Generate audit report
    report_gen = AuditReport(
        records=[r.to_dict() for r in records],
        output_dir=output_dir,
    )
    report_path = report_gen.write_report()

    # Summary
    passed = sum(1 for r in records if r.success and not r.error)
    failed = sum(1 for r in records if r.error)
    all_pass = len(report_gen.issues) == 0

    pairs: dict[str, list] = {}
    for rec in records:
        tag = rec.pair_tag or rec.scenario_id
        pairs.setdefault(tag, []).append(rec)
    paired = sum(1 for v in pairs.values() if len(v) == 2 and
                 any(r.condition == "benign" for r in v) and
                 any(r.condition == "single_lep" for r in v))

    logger.info("=" * 60)
    logger.info("PILOT COMPLETE")
    logger.info("Executions: %d total, %d passed, %d failed", len(records), passed, failed)
    logger.info("LEP pairs (benign + malignant): %d / %d", paired, len(pairs))
    logger.info("Audit issues: %d", len(report_gen.issues))
    logger.info("Report: %s", report_path)
    logger.info("=" * 60)

    return {
        "total": len(records),
        "passed": passed,
        "failed": failed,
        "issues": report_gen.issues,
        "all_checks_pass": all_pass,
        "report_path": str(report_path),
        "records_path": str(records_path),
    }


def _benchmark_record_to_execution(brec: BenchmarkRecord) -> ExecutionRecord:
    """Convert a BenchmarkRecord into a pilot ExecutionRecord."""
    from pilot.executor import ExecutionRecord

    variant = "benign" if brec.condition == "benign" else (
        "counterfactual" if brec.condition == "counterfactual" else "malignant"
    )

    return ExecutionRecord(
        execution_id=brec.run_id,
        scenario_id=brec.scenario_id,
        task_family=brec.task_family,
        condition=brec.condition,
        lep_codes=[brec.lep_code] if brec.condition != "benign" and brec.lep_code else [],
        variant=variant,
        trace=None,
        trace_id=brec.trace_id,
        pair_tag=brec.pair_tag or _extract_pair_tag(brec.scenario_id),
        success=brec.success,
        error=brec.error,
        runtime_seconds=brec.runtime_seconds,
        num_events=brec.num_events,
        num_tool_calls=brec.num_tool_calls,
        num_handoffs=brec.num_handoffs,
        injection_fired=brec.injection_fired,
        injection_events=brec.injection_event_ids,
        consumption_events=brec.consumption_event_ids,
        propagation_events=brec.propagation_event_ids,
        downstream_failure=brec.downstream_failure,
        task_success=brec.task_success,
        evaluator_passed=brec.evaluator_passed,
        evaluator_errors=brec.evaluator_errors,
        final_output=brec.final_output,
        timestamp=brec.timestamp,
    )


def _extract_pair_tag(scenario_id: str) -> str:
    """Extract the pair_tag from a benchmark scenario_id.

    Benchmark scenario_ids look like:
        b_{topology}_{task}_{lep_code}_{prop_mode}_{rep:02d}_benign
        b_{topology}_{task}_{lep_code}_{prop_mode}_{rep:02d}_lep
    The pair_tag is everything before the _benign/_lep suffix.
    """
    for suffix in ("_benign", "_lep", "_cf"):
        if scenario_id.endswith(suffix):
            return scenario_id[: -len(suffix)]
    return scenario_id


def main():
    parser = argparse.ArgumentParser(
        description="AgentGraph V3 Pilot — real-model validation (via BenchmarkRunner)"
    )
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Use deterministic DryRunBackend (default)")
    parser.add_argument("--real-model", action="store_true",
                        help="Use real LLM API (requires LLM_API_KEY)")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="Output directory for pilot results")
    parser.add_argument("--fixture-dir", type=Path, default=None,
                        help="Root directory of workspace_fixtures/")
    parser.add_argument("--max-executions", type=int, default=None,
                        help="Limit number of executions")
    parser.add_argument("--task", type=str, default=None,
                        help="Run only this task family")
    parser.add_argument("--lep", type=str, default=None,
                        help="Run only this LEP code")
    parser.add_argument("--topology", type=str, default=None,
                        help="Run only this topology ID (default: branch_and_verify)")
    args = parser.parse_args()

    dry_run = not args.real_model
    result = run_pilot(
        dry_run=dry_run,
        output_dir=args.output_dir,
        fixture_root=args.fixture_dir,
        max_executions=args.max_executions,
        task_filter=args.task,
        lep_filter=args.lep,
        topology_filter=args.topology,
    )

    if not result["all_checks_pass"]:
        logger.warning("Pilot has %d issues — see report for details", len(result["issues"]))
        sys.exit(1)
    else:
        logger.info("All pilot checks passed!")
        sys.exit(0)


if __name__ == "__main__":
    main()
