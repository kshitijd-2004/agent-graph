"""Benchmark CLI — run the full experimental matrix.

Usage:
    python -m benchmark --topologies linear_2,branch_and_verify \\
        --task-families code_review \\
        --lep-codes LEP_TOOL_RESULT_CORRUPTION \\
        --repetitions 3 \\
        --propagation-modes single_origin,one_to_many \\
        --output-dir benchmark_output
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Optional

# Ensure parent directory is in path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from schemas.scenario import TOPOLOGIES

from benchmark.benchmark_runner import BenchmarkManifest, BenchmarkRunner

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("benchmark")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AgentGraph V3 Benchmark Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Small benchmark: one topology, one task, one LEP
  python -m benchmark --topologies linear_2 --task-families code_review \\
      --lep-codes LEP_TOOL_RESULT_CORRUPTION --repetitions 3

  # Full matrix across all topologies and propagation modes
  python -m benchmark --task-families code_review,financial_analysis \\
      --repetitions 5 --propagation-modes single_origin,one_to_many,many_to_one

  # Real LLM mode (requires API key)
  python -m benchmark --task-families code_review --real-model
""",
    )
    parser.add_argument(
        "--topologies",
        type=str,
        default=",".join(TOPOLOGIES),
        help=f"Comma-separated topology IDs (default: all — {','.join(TOPOLOGIES)})",
    )
    parser.add_argument(
        "--task-families",
        type=str,
        default="code_review,financial_analysis,research_synthesis,competitive_intelligence",
        help="Comma-separated task family names",
    )
    parser.add_argument(
        "--lep-codes",
        type=str,
        default=None,
        help="Comma-separated LEP codes (default: all registered LEPs)",
    )
    parser.add_argument(
        "--propagation-modes",
        type=str,
        default="single_origin,one_to_many,many_to_one",
        help="Comma-separated propagation modes",
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        default=3,
        help="Repetitions per (topology × task × LEP × propagation_mode) cell (default: 3)",
    )
    parser.add_argument(
        "--max-events",
        type=int,
        default=50,
        help="Max events per scenario trace (default: 50)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("benchmark_output"),
        help="Output directory for traces and results (default: benchmark_output)",
    )
    parser.add_argument(
        "--fixture-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "workspace_fixtures",
        help="Root directory for workspace fixtures",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Use mock LLM backend (default)",
    )
    parser.add_argument(
        "--real-model",
        action="store_true",
        help="Use real LLM (requires LLM_API_KEY)",
    )

    args = parser.parse_args()

    dry_run = not args.real_model

    topologies = [t.strip() for t in args.topologies.split(",") if t.strip()]
    task_families = [t.strip() for t in args.task_families.split(",") if t.strip()]
    lep_codes = [c.strip() for c in args.lep_codes.split(",")] if args.lep_codes else None
    propagation_modes = [m.strip() for m in args.propagation_modes.split(",") if m.strip()]

    # Resolve LEPConfigs from task registry
    from tasks.registry import get_default_leps, get_task_registry
    task_registry = get_task_registry()

    available_leps = []
    seen = set()
    for tf in task_families:
        if tf not in task_registry:
            logger.error("Unknown task family: %s. Available: %s", tf, sorted(task_registry.keys()))
            return 1
        for lep in get_default_leps(tf):
            if lep.code not in seen:
                available_leps.append(lep)
                seen.add(lep.code)

    if lep_codes:
        available_leps = [lep for lep in available_leps if lep.code in lep_codes]

    if not available_leps:
        logger.error("No LEPs match the requested codes. Available: %s", sorted(seen))
        return 1

    # Build manifest — one cell per (topology × propagation_mode)
    # The runner iterates topologies × task_families × lep_configs × propagation_modes
    manifest = BenchmarkManifest(
        topologies=topologies,
        task_families=task_families,
        lep_configs=available_leps,
        num_repetitions=args.repetitions,
        max_events=args.max_events,
        model_name="claude-sonnet-5",
        dry_run=dry_run,
        output_dir=args.output_dir,
        fixture_root=args.fixture_root,
        seed=42,
        propagation_modes=propagation_modes,
    )

    logger.info(
        "Benchmark plan: %d topologies × %d tasks × %d LEPs × %d prop_modes × %d reps = %d scenarios",
        len(topologies),
        len(task_families),
        len(available_leps),
        len(propagation_modes),
        args.repetitions,
        len(topologies) * len(task_families) * len(available_leps) * len(propagation_modes) * args.repetitions,
    )

    runner = BenchmarkRunner(manifest)
    summary = runner.run()

    # Print summary
    print("\n" + "=" * 60)
    print("BENCHMARK SUMMARY")
    print("=" * 60)
    for key in [
        "total_runs", "successful_runs", "failed_runs",
        "injection_fired_rate", "downstream_failure_rate",
        "recovery_rate",
    ]:
        print(f"  {key}: {summary.get(key, 'N/A')}")

    print("\nPer-topology:")
    for topo, stats in summary.get("per_topology", {}).items():
        print(f"  {topo}: {stats}")

    print("\nPer-propagation-mode:")
    for mode, stats in summary.get("per_propagation_mode", {}).items():
        print(f"  {mode}: {stats}")

    results_path = args.output_dir / "benchmark_summary.json"
    with open(results_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"\nFull summary: {results_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
