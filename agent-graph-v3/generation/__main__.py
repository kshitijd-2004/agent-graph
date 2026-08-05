"""Main scenario runner CLI and orchestration.

Usage:
    python -m generation --build-pilot --run
    python generation/run.py --scenarios manifests/pilot.jsonl
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional

# Ensure parent directory is in path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from schemas import ScenarioSpec

from generation.scenario_builder import ScenarioBuilder, ScenarioBuildConfig, TASK_CONFIGS
from generation.runner import ScenarioRunner, RunResult

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def load_scenarios(path: Path) -> list[dict]:
    """Load scenario specs from a JSONL manifest file."""
    scenarios = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                scenarios.append(json.loads(line))
    return scenarios


def save_scenarios(scenarios: list[ScenarioSpec], path: Path) -> None:
    """Save scenario specs to a JSONL manifest file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for spec in scenarios:
            f.write(json.dumps(spec.to_dict() if hasattr(spec, "to_dict") else {
                "scenario_id": spec.scenario_id,
                "task_family": spec.task_family,
                "task_variant": spec.task_variant,
                "fixture_id": spec.fixture_id,
                "condition": spec.condition,
                "lep_codes": [c.code for c in spec.lep_configs],
            }) + "\n")


def run_scenarios(
    scenarios: list[ScenarioSpec],
    fixture_root: Path,
    output_dir: Path,
    dry_run: bool = True,
    max_events: int = 80,
) -> tuple[list[RunResult], ObservableExporter]:
    """Execute a list of scenarios and return results and exporter for leakage audit."""
    if dry_run:
        llm = None  # ScenarioRunner will default to DryRunBackend
        logger.info("Running in DRY-RUN mode (mock LLM)")
    else:
        model_name = scenarios[0].workflow_config.model_name if scenarios else "claude-sonnet-5"
        logger.info("Running in REAL-LLM mode (model=%s)", model_name)
        from backend.api_backend import APIBackend
        llm = APIBackend(model=model_name)

    runner = ScenarioRunner(
        llm_backend=llm,
        dry_run=dry_run,
        max_events=max_events,
        output_dir=output_dir / "workspaces",
    )

    from exporters.observable_exporter import ObservableExporter
    from exporters.analysis_exporter import AnalysisExporter
    from exporters.prefix_exporter import PrefixExporter

    obs_exp = ObservableExporter(output_dir / "observable")
    ana_exp = AnalysisExporter(output_dir / "analysis")
    pre_exp = PrefixExporter(output_dir / "prefixes")

    results = []
    for i, scenario in enumerate(scenarios):
        logger.info("Running scenario %d/%d: %s", i + 1, len(scenarios), scenario.scenario_id)
        t0 = time.time()
        result = runner.run(scenario, fixture_root)
        result.runtime_seconds = time.time() - t0

        if result.runner_success and result.trace.num_events > 0:
            # Export in all formats
            obs_exp.export_trace(result.trace)
            ana_exp.export_trace(result.trace, evaluation=result.evaluation)
            pre_exp.export_prefixes(result.trace)

        results.append(result)
        logger.info("  → %d events, success=%s", result.trace.num_events, result.runner_success)

    runner.cleanup()
    return results, obs_exp


def summarize_results(results: list[RunResult]) -> Dict[str, Any]:
    """Generate a summary of run results."""
    total = len(results)
    successful = sum(1 for r in results if r.success)
    failed = total - successful
    benign = sum(1 for r in results if r.trace.is_benign)
    malignant = total - benign
    with_leps = sum(1 for r in results
                    if r.trace.metadata.get("lep_codes"))

    event_counts = [r.trace.num_events for r in results if r.success]
    avg_events = sum(event_counts) / len(event_counts) if event_counts else 0

    return {
        "total_scenarios": total,
        "successful": successful,
        "failed": failed,
        "benign_traces": benign,
        "malignant_traces": malignant,
        "with_leps": with_leps,
        "avg_events": round(avg_events, 1),
        "min_events": min(event_counts) if event_counts else 0,
        "max_events": max(event_counts) if event_counts else 0,
        "success_rate": round(successful / total, 3) if total else 0,
    }


def build_pilot_manifest(
    task_families: list[str],
    fixture_ids: Dict[str, list[str]],
    lep_configs: Dict[str, list],
    num_repetitions: int = 5,
    seed: int = 42,
    strict: bool = True,
    topology_override: Optional[str] = None,
    model_name: str = "claude-sonnet-5",
) -> list[ScenarioSpec]:
    """Build a pilot manifest of scenarios.

    For each task x fixture x LEP combination, generate:
    - 1 benign
    - 1 single-LEP
    - 1 counterfactual (no LEP, same config)
    Repeated num_repetitions times with different seeds.

    Args:
        strict: If True (default), fail if any LEP is unregistered.
                If False, skip unregistered LEPs and record in report.
        topology_override: If set, override the default topology for every task
                family. Each task family must list the topology in
                ``supported_topologies`` or a ValueError is raised.
    """
    builder = ScenarioBuilder(seed=seed)
    from validation.lep_validator import get_registered_codes
    registered = get_registered_codes()

    # Validate topology_override against supported_topologies before building
    if topology_override is not None:
        for tf in task_families:
            supported = TASK_CONFIGS.get(tf, {}).get("supported_topologies")
            if supported and topology_override not in supported:
                raise ValueError(
                    f"Topology '{topology_override}' is not supported for task "
                    f"family '{tf}'. Supported: {supported}"
                )

    all_scenarios = []
    skipped: Dict[str, list[str]] = {}

    for rep in range(num_repetitions):
        for task_family in task_families:
            fixtures = fixture_ids.get(task_family, [])
            leps = lep_configs.get(task_family, [])
            if topology_override is not None:
                topology = topology_override
            else:
                topology = TASK_CONFIGS.get(task_family, {}).get(
                    "default_topology", "linear_2"
                )

            # Validate LEPs for this task
            valid_leps = []
            for lep in leps:
                if lep.code in registered:
                    valid_leps.append(lep)
                elif strict:
                    raise ValueError(
                        f"Unregistered LEP '{lep.code}' for task '{task_family}'. "
                        f"Registered codes: {sorted(registered)}"
                    )
                else:
                    skipped.setdefault(task_family, []).append(lep.code)

            for fixture_id in fixtures:
                base_config = ScenarioBuildConfig(
                    task_family=task_family,
                    fixture_id=fixture_id,
                    task_variant="default",
                    topology=topology,
                    repetition_index=rep,
                    seed=seed + rep,
                )

                # Benign
                all_scenarios.append(builder.build_benign(base_config))

                # Single-LEP + counterfactual for each valid LEP
                for lep in valid_leps:
                    single = builder.build_single_lep(base_config, lep)
                    all_scenarios.append(single)

                    cf = builder.build_counterfactual(single)
                    all_scenarios.append(cf)

    if skipped:
        logger.warning("Skipped unregistered LEPs: %s", skipped)

    return all_scenarios


def _default_lep_configs() -> dict:
    """Fallback LEP configs when task registry is unavailable."""
    from schemas import LEPConfig
    from schemas.triggers import InjectionTrigger
    return {
        "code_review": [
            LEPConfig(code="LEP_TOOL_RESULT_CORRUPTION", name="Tool Corruption",
                      category="tool_corruption", description="Corrupt tool output",
                      target_agent="inspector",
                      trigger=InjectionTrigger(tool_name="read_text_file")),
            LEPConfig(code="LEP_INPUT_DISREGARD", name="Input Disregard",
                      category="input_disregard", description="Ignore upstream findings",
                      target_agent="reviewer",
                      trigger=InjectionTrigger(event_type="agent_handoff")),
        ],
        "financial_analysis": [
            LEPConfig(code="LEP_TOOL_RESULT_CORRUPTION", name="Numerical Corruption",
                      category="tool_corruption", description="Corrupt financial figures",
                      target_agent="extractor",
                      trigger=InjectionTrigger(tool_name="read_text_file")),
            LEPConfig(code="LEP_MEMORY_POISONING", name="Memory Poisoning",
                      category="memory_poisoning", description="Poison memory with stale value",
                      target_agent="analyst",
                      trigger=InjectionTrigger(event_type="memory_write")),
        ],
        "research_synthesis": [
            LEPConfig(code="LEP_INDIRECT_PROMPT_INJECTION", name="Indirect Injection",
                      category="prompt_injection", description="Inject into retrieved doc",
                      target_agent="researcher",
                      trigger=InjectionTrigger(tool_name="read_text_file")),
            LEPConfig(code="LEP_MEMORY_POISONING", name="Memory Poisoning",
                      category="memory_poisoning", description="Fabricate citation in memory",
                      target_agent="researcher",
                      trigger=InjectionTrigger(event_type="memory_write")),
        ],
        "competitive_intelligence": [
            LEPConfig(code="LEP_TOOL_RESULT_CORRUPTION", name="Stale Pricing",
                      category="tool_corruption", description="Corrupt pricing data",
                      target_agent="researcher",
                      trigger=InjectionTrigger(tool_name="read_text_file")),
            LEPConfig(code="LEP_HANDOFF_CORRUPTION", name="Handoff Corruption",
                      category="handoff_corruption", description="Omit key finding in handoff",
                      target_agent="researcher",
                      trigger=InjectionTrigger(event_type="agent_handoff")),
        ],
    }


def main():
    parser = argparse.ArgumentParser(
        description="AgentGraph V3 Scenario Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run with default pilot scenarios
  python -m generation.scenario_runner --dry-run --pilot

  # Run from manifest
  python -m generation.scenario_runner --scenarios manifests/pilot.jsonl

  # Build and run pilot
  python -m generation.scenario_runner --build-pilot --run --repetitions 3

  # Single task family with custom topology
  python -m generation --build-pilot --run \\
      --task-families code_review \\
      --topology review_loop \\
      --repetitions 3
""",
    )
    parser.add_argument("--scenarios", type=Path, help="Path to scenario manifest JSONL")
    parser.add_argument("--build-pilot", action="store_true", help="Build pilot manifest")
    parser.add_argument("--run", action="store_true", help="Run scenarios")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Use mock LLM (default)")
    parser.add_argument("--real-model", action="store_true",
                        help="Use real LLM (requires LLM_API_KEY)")
    parser.add_argument("--repetitions", type=int, default=5,
                        help="Number of repetitions per condition (default: 5)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--output-dir", type=Path, default=Path("output"),
                        help="Output directory")
    parser.add_argument("--fixture-root", type=Path,
                        default=Path(__file__).resolve().parent.parent / "workspace_fixtures",
                        help="Root directory for workspace fixtures")
    parser.add_argument("--manifest-out", type=Path, default=Path("manifests/pilot.jsonl"),
                        help="Output path for scenario manifest")
    parser.add_argument("--topology", type=str, default=None,
                        help="Override topology for all task families "
                             "(must be in each family's supported_topologies)")
    parser.add_argument("--task-families", type=str, default=None,
                        help="Comma-separated list of task families to include "
                             "(default: all four)")
    parser.add_argument("--model", type=str, default="claude-sonnet-5",
                        help="Model name to embed in scenarios (default: claude-sonnet-5)")

    args = parser.parse_args()

    dry_run = not args.real_model

    if args.build_pilot:
        logger.info("Building pilot manifest...")

        ALL_TASK_FAMILIES = ["code_review", "financial_analysis",
                             "research_synthesis", "competitive_intelligence"]

        if args.task_families:
            task_families = [f.strip() for f in args.task_families.split(",")]
            unknown = set(task_families) - set(ALL_TASK_FAMILIES)
            if unknown:
                parser.error(f"Unknown task family(ies): {sorted(unknown)}. "
                             f"Valid: {ALL_TASK_FAMILIES}")
        else:
            task_families = ALL_TASK_FAMILIES

        fixture_ids = {
            "code_review": ["code_review_easy", "code_review_conflicting"],
            "financial_analysis": ["financial_clean", "financial_version_conflict"],
            "research_synthesis": ["research_conflicting"],
            "competitive_intelligence": ["competitive_pricing"],
        }
        # Load LEP configs from task files
        lep_configs: Dict[str, list] = {}
        for tf in task_families:
            try:
                from tasks.registry import get_default_leps
                lep_configs[tf] = get_default_leps(tf)
            except (ImportError, AttributeError):
                lep_configs[tf] = []
            if not lep_configs[tf]:
                lep_configs[tf] = _default_lep_configs().get(tf, [])

        scenarios = build_pilot_manifest(
            task_families=task_families,
            fixture_ids=fixture_ids,
            lep_configs=lep_configs,
            num_repetitions=args.repetitions,
            seed=args.seed,
            topology_override=args.topology,
            model_name=args.model,
        )

        args.manifest_out.parent.mkdir(parents=True, exist_ok=True)
        save_scenarios(scenarios, args.manifest_out)
        logger.info("Built %d scenarios, saved to %s", len(scenarios), args.manifest_out)

        if not args.run:
            return 0

    if args.scenarios:
        raw = load_scenarios(args.scenarios)
        scenarios = [ScenarioSpec.from_dict(s) for s in raw]
    elif args.build_pilot:
        raw = load_scenarios(args.manifest_out)
        scenarios = [ScenarioSpec.from_dict(s) for s in raw]
    else:
        parser.error("Provide --scenarios or --build-pilot")
        return 1

    if not scenarios:
        logger.error("No scenarios to run")
        return 1

    logger.info("Running %d scenarios (dry_run=%s)...", len(scenarios), dry_run)
    results, obs_exp = run_scenarios(
        scenarios=scenarios,
        fixture_root=args.fixture_root,
        output_dir=args.output_dir,
        dry_run=dry_run,
    )

    # Generate pilot report
    report = generate_pilot_report(results, obs_exp)
    report_path = args.output_dir / "pilot_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    logger.info("Pilot report written to %s", report_path)

    # Print report summary
    _print_report(report)

    # Write run_summary (compatibility)
    summary = report["summary"]
    summary_path = args.output_dir / "run_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)

    # Save detailed results
    results_path = args.output_dir / "run_results.jsonl"
    with open(results_path, "w") as f:
        for r in results:
            f.write(json.dumps({
                "scenario_id": r.scenario_id,
                "success": r.success,
                "error": r.error,
                "num_events": r.trace.num_events,
                "variant": r.trace.variant.value,
                "runtime_seconds": round(r.runtime_seconds, 3),
                "evaluation_passed": r.evaluation.get("passed") if r.evaluation else None,
                "evaluation_errors": r.evaluation.get("errors", []) if r.evaluation else [],
            }) + "\n")

    return 0


def generate_pilot_report(results: list[RunResult],
                          obs_exporter: ObservableExporter) -> Dict[str, Any]:
    """Generate a compact pilot report from run results."""
    total = len(results)
    successful = [r for r in results if r.success]
    failed = [r for r in results if not r.success]

    # Pass/fail by condition
    eval_passed = [r for r in successful if r.evaluation and r.evaluation.get("passed")]
    eval_failed = [r for r in successful if r.evaluation and not r.evaluation.get("passed")]

    # By task family
    by_task: Dict[str, Dict[str, int]] = {}
    for r in results:
        tf = r.trace.metadata.get("task_family", "unknown")
        if tf not in by_task:
            by_task[tf] = {"total": 0, "passed": 0, "failed": 0}
        by_task[tf]["total"] += 1
        if r.evaluation and r.evaluation.get("passed"):
            by_task[tf]["passed"] += 1
        else:
            by_task[tf]["failed"] += 1

    # By LEP
    by_lep: Dict[str, Dict[str, int]] = {}
    for r in successful:
        leps = r.trace.metadata.get("lep_codes", [])
        for lep in leps:
            if lep not in by_lep:
                by_lep[lep] = {"total": 0, "injection": 0, "consumption": 0,
                               "propagation": 0, "failure": 0}
            stats = r.evaluation or {}
            by_lep[lep]["total"] += 1
            by_lep[lep]["injection"] += stats.get("injection_count", 0)
            by_lep[lep]["consumption"] += stats.get("consumption_count", 0)
            by_lep[lep]["propagation"] += stats.get("propagation_count", 0)
            by_lep[lep]["failure"] += stats.get("failure_count", 0)

    # Aggregate stats
    total_injection = sum(r.evaluation.get("injection_count", 0) for r in successful if r.evaluation)
    total_exposure = sum(r.evaluation.get("exposure_count", 0) for r in successful if r.evaluation)
    total_consumption = sum(r.evaluation.get("consumption_count", 0) for r in successful if r.evaluation)
    total_propagation = sum(r.evaluation.get("propagation_count", 0) for r in successful if r.evaluation)
    total_failures = sum(r.evaluation.get("failure_count", 0) for r in successful if r.evaluation)

    # Leakage findings
    leakage = obs_exporter.get_leakage_summary()

    # Errors
    all_errors = []
    for r in failed:
        all_errors.append({"scenario_id": r.scenario_id, "error": r.error})
    for r in eval_failed:
        all_errors.append({
            "scenario_id": r.scenario_id,
            "evaluation_errors": r.evaluation.get("errors", []),
        })

    return {
        "summary": {
            "total_scenarios": total,
            "successful_runs": len(successful),
            "failed_runs": len(failed),
            "eval_passed": len(eval_passed),
            "eval_failed": len(eval_failed),
            "pass_rate": round(len(eval_passed) / total, 3) if total else 0,
        },
        "counts": {
            "injection": total_injection,
            "exposure": total_exposure,
            "consumption": total_consumption,
            "propagation": total_propagation,
            "failure": total_failures,
        },
        "pass_rate_by_task": {
            tf: {
                "total": v["total"],
                "passed": v["passed"],
                "pass_rate": round(v["passed"] / v["total"], 3) if v["total"] else 0,
            }
            for tf, v in by_task.items()
        },
        "pass_rate_by_lep": by_lep,
        "traces_rejected_by_validator": len(failed),
        "observable_leakage_findings": leakage,
        "errors": all_errors[:50],
    }


def _print_report(report: Dict[str, Any]) -> None:
    """Print a human-readable summary of the pilot report."""
    print("\n" + "=" * 60)
    print("PILOT REPORT")
    print("=" * 60)

    s = report["summary"]
    print(f"\nOverall: {s['total_scenarios']} scenarios, "
          f"{s['successful_runs']} successful, {s['eval_passed']} passed eval "
          f"({s['pass_rate']*100:.1f}%)")

    c = report["counts"]
    print(f"\nCausal counts:")
    print(f"  Injection events:  {c['injection']}")
    print(f"  Exposure:          {c['exposure']}")
    print(f"  Consumption:       {c['consumption']}")
    print(f"  Propagation:       {c['propagation']}")
    print(f"  Failure events:    {c['failure']}")

    print(f"\nBy task family:")
    for tf, v in report["pass_rate_by_task"].items():
        print(f"  {tf}: {v['passed']}/{v['total']} ({v['pass_rate']*100:.0f}%)")

    if report["pass_rate_by_lep"]:
        print(f"\nBy LEP:")
        for lep, v in report["pass_rate_by_lep"].items():
            print(f"  {lep}: {v['total']} runs, "
                  f"injection={v['injection']}, "
                  f"consumption={v['consumption']}")

    lf = report["observable_leakage_findings"]
    print(f"\nLeakage: {lf['total_findings']} findings")
    if lf["by_type"]:
        for t, n in lf["by_type"].items():
            print(f"  {t}: {n}")

    rej = report["traces_rejected_by_validator"]
    if rej:
        print(f"\nRejected by validator: {rej}")

    if report["errors"]:
        print(f"\nErrors (showing first 5):")
        for e in report["errors"][:5]:
            print(f"  {e.get('scenario_id', '?')}: {str(e.get('error', e.get('evaluation_errors', '')))[:100]}")

    print("=" * 60)


if __name__ == "__main__":
    sys.exit(main())
