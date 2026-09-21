#!/usr/bin/env python3
"""Train downstream-failure detectors on benchmark trace data.

Usage examples
--------------
  # Quick smoke test — 10 epochs, static GNN only
  python scripts/train_detector.py --output-dir benchmark_output --epochs 10 --detectors static_gnn

  # Full experiment on all three detectors
  python scripts/train_detector.py --output-dir benchmark_output --epochs 100

  # Custom split ratios and snapshot cadence
  python scripts/train_detector.py --output-dir benchmark_output \\
      --train-frac 0.8 --val-frac 0.1 --snapshot-interval 3
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import List, Optional

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from training.pipeline import BenchmarkPipeline
from training.trainer import DetectorTrainer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("train_detector")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train downstream-failure detectors on benchmark traces",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="benchmark_output",
        help="Directory containing traces/ and benchmark_records.* (default: benchmark_output)",
    )
    parser.add_argument(
        "--traces-dir",
        type=str,
        default=None,
        help="Override traces directory (default: <output-dir>/traces)",
    )
    parser.add_argument(
        "--detectors",
        nargs="+",
        default=["static_gnn", "tgnn", "hybrid"],
        choices=["static_gnn", "tgnn", "hybrid"],
        help="Detectors to train (default: all three)",
    )
    parser.add_argument(
        "--epochs",
        type=int,
        default=100,
        help="Maximum training epochs per detector (default: 100)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=8,
        help="Training batch size (default: 8)",
    )
    parser.add_argument(
        "--patience",
        type=int,
        default=10,
        help="Early stopping patience in epochs (default: 10)",
    )
    parser.add_argument(
        "--lr",
        type=float,
        default=1e-3,
        help="Learning rate (default: 1e-3)",
    )
    parser.add_argument(
        "--train-frac",
        type=float,
        default=0.7,
        help="Training split fraction (default: 0.7)",
    )
    parser.add_argument(
        "--val-frac",
        type=float,
        default=0.15,
        help="Validation split fraction (default: 0.15)",
    )
    parser.add_argument(
        "--snapshot-interval",
        type=int,
        default=5,
        help="Snapshot every N events for static GNN (default: 5)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for splits (default: 42)",
    )
    parser.add_argument(
        "--device",
        type=str,
        default=None,
        help="Device string (e.g. cuda, mps); defaults to CUDA if available",
    )
    parser.add_argument(
        "--no-heuristic-baselines",
        action="store_true",
        help="Skip heuristic baseline evaluation",
    )
    parser.add_argument(
        "--results-json",
        type=str,
        default=None,
        help="Optional path to write results as JSON",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    output_dir = Path(args.output_dir).resolve()
    traces_dir = (
        Path(args.traces_dir).resolve() if args.traces_dir else output_dir / "traces"
    )

    if not traces_dir.exists():
        logger.error("Traces directory not found: %s", traces_dir)
        return 1

    logger.info("Output dir  : %s", output_dir)
    logger.info("Traces dir   : %s", traces_dir)
    logger.info("Detectors    : %s", args.detectors)
    logger.info("Epochs       : %d", args.epochs)
    logger.info("Batch size   : %d", args.batch_size)
    logger.info("Split        : train=%.0f%% val=%.0f%% test=%.0f%%",
                args.train_frac * 100, args.val_frac * 100,
                (1 - args.train_frac - args.val_frac) * 100)

    # ── Build pipeline ────────────────────────────────────────────────────────
    pipeline = BenchmarkPipeline(
        output_dir=str(output_dir),
        traces_dir=str(traces_dir),
        seed=args.seed,
        device=args.device,
    )

    # ── Run ───────────────────────────────────────────────────────────────────
    results = pipeline.run(
        detector_types=args.detectors,
        snapshot_interval=args.snapshot_interval,
        num_epochs=args.epochs,
        batch_size=args.batch_size,
        patience=args.patience,
        lr=args.lr,
        train_frac=args.train_frac,
        val_frac=args.val_frac,
        use_heuristic_baselines=not args.no_heuristic_baselines,
    )

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    for name, result in results.items():
        test_metrics = result.test_metrics.get("metrics", {})
        print(
            f"  {name:20s}  val AUROC={result.best_val_auroc:.4f}  "
            f"test AUROC={test_metrics.get('auroc', float('nan')):.4f}  "
            f"test F1={test_metrics.get('f1', float('nan')):.4f}  "
            f"(epoch {result.best_epoch})"
        )
    print("=" * 60)

    # ── Optional JSON output ──────────────────────────────────────────────────
    if args.results_json:
        results_path = Path(args.results_json)
        results_path.parent.mkdir(parents=True, exist_ok=True)
        serializable = {}
        for name, result in results.items():
            serializable[name] = {
                "best_val_auroc": result.best_val_auroc,
                "best_epoch": result.best_epoch,
                "test_metrics": result.test_metrics,
                "train_history": result.train_history,
            }
        with open(results_path, "w") as f:
            json.dump(serializable, f, indent=2, default=str)
        logger.info("Results written to %s", results_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
