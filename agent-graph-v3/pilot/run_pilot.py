"""Main pilot runner — orchestrates the full pilot execution pipeline.

Usage (dry-run, no API key needed):
    python -m pilot.run_pilot --dry-run

Usage (real model):
    LLM_API_KEY=sk-... python -m pilot.run_pilot --real-model

Flags:
    --dry-run           Use DryRunBackend (default)
    --output-dir DIR    Where to write pilot outputs
    --fixture-dir DIR   Root of workspace_fixtures/
    --max-executions N  Limit number of executions (for quick smoke tests)
    --task FAMILY      Run only this task family
    --lep CODE         Run only this LEP
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
) -> dict[str, Any]:
    """Run the full pilot and return summary statistics."""
    from pilot.config import PilotConfig, PILOT_LEP_CONFIGS, PILOT_TASK_FAMILIES
    from pilot.executor import PilotExecutor
    from pilot.audit_report import AuditReport

    # Resolve paths
    v3_root = Path(__file__).resolve().parent.parent
    output_dir = output_dir or v3_root / "pilot_output"
    output_dir.mkdir(parents=True, exist_ok=True)
    fixture_root = fixture_root or v3_root / "workspace_fixtures"

    # Build config
    config = PilotConfig()

    # Apply filters
    task_families = [task_filter] if task_filter else config.task_families
    lep_configs = [lep for lep in config.lep_configs
                   if not lep_filter or lep.code == lep_filter]

    if task_filter and task_filter not in PILOT_TASK_FAMILIES:
        logger.error("Unknown task family: %s (choose from %s)", task_filter, PILOT_TASK_FAMILIES)
        sys.exit(1)

    if lep_filter:
        if not any(lep.code == lep_filter for lep in PILOT_LEP_CONFIGS):
            logger.error("Unknown LEP code: %s", lep_filter)
            sys.exit(1)

    logger.info("=" * 60)
    logger.info("AGENT-GRAPH V3 PILOT")
    logger.info("Schema: v%s | Mode: %s", SCHEMA_VERSION, "dry-run" if dry_run else "real-model")
    logger.info("Task families: %s", ", ".join(task_families))
    logger.info("LEPs: %s", ", ".join(l.code for l in lep_configs) or "none (benign/cf)")
    logger.info("Output: %s", output_dir)
    logger.info("=" * 60)

    # Build execution plan
    plan = []
    for task_family in task_families:
        # Each perturbed (LEP) run is paired with a matching benign trace
        # so downstream analysis can compare the two directly.
        for lep in lep_configs:
            for rep in range(max(1, config.num_perturbed // (len(task_families) * len(lep_configs)))):
                pair_id = f"{config.pilot_id}_{lep.code}_{task_family}_{rep:02d}"
                plan.append({
                    "scenario_id": f"{pair_id}_benign",
                    "task_family": task_family,
                    "condition": "benign",
                    "lep_codes": [],
                    "topology": "linear_2",
                    "repetition_index": rep,
                    "pair_tag": pair_id,
                })
                plan.append({
                    "scenario_id": f"{pair_id}_lep",
                    "task_family": task_family,
                    "condition": "single_lep",
                    "lep_codes": [lep.code],
                    "topology": "linear_2",
                    "repetition_index": rep,
                    "pair_tag": pair_id,
                })

        # Counterfactuals
        for rep in range(config.num_counterfactuals // len(task_families)):
            plan.append({
                "scenario_id": f"{config.pilot_id}_cf_{task_family}_{rep:02d}",
                "task_family": task_family,
                "condition": "counterfactual",
                "lep_codes": [],
                "topology": "linear_2",
                "repetition_index": rep,
            })

    if max_executions:
        # Round up to nearest even so we never split a benign+LEP pair in half.
        plan = plan[:max_executions + (max_executions % 2)]

    logger.info("Execution plan: %d scenarios", len(plan))

    # Execute
    executor = PilotExecutor(
        output_dir=output_dir,
        fixture_root=fixture_root,
        dry_run=dry_run,
    )
    records = executor.run_all(plan)

    # Write raw records
    records_path = output_dir / "pilot_records.jsonl"
    with open(records_path, "w") as f:
        for rec in records:
            f.write(json.dumps(rec.to_dict()) + "\n")

    # Generate audit report
    report_gen = AuditReport(records=[r.to_dict() for r in records], output_dir=output_dir)
    report_path = report_gen.write_report()

    # Summary
    passed = sum(1 for r in records if r.success and not r.error)
    failed = sum(1 for r in records if r.error)
    all_pass = len(report_gen.issues) == 0

    # Pair summary: for each pair_tag, count benign + malignant traces
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


def main():
    parser = argparse.ArgumentParser(
        description="AgentGraph V3 Pilot — real-model validation"
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
    args = parser.parse_args()

    dry_run = not args.real_model
    result = run_pilot(
        dry_run=dry_run,
        output_dir=args.output_dir,
        fixture_root=args.fixture_dir,
        max_executions=args.max_executions,
        task_filter=args.task,
        lep_filter=args.lep,
    )

    if not result["all_checks_pass"]:
        logger.warning("Pilot has %d issues — see report for details", len(result["issues"]))
        sys.exit(1)
    else:
        logger.info("All pilot checks passed!")
        sys.exit(0)


if __name__ == "__main__":
    main()
