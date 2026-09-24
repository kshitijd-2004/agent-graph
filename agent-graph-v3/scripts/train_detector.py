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

  # Nested cross-validation
  python scripts/train_detector.py --output-dir benchmark_output --cross-validation --epochs 50
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from training.pipeline import DetectorPipeline, PipelineResult
from training.cross_validation import NestedGroupCV

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
        default=1e-4,
        help="Learning rate (default: 1e-4)",
    )
    parser.add_argument(
        "--weight-decay",
        type=float,
        default=1e-4,
        help="Weight decay for AdamW (default: 1e-4)",
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
        "--cross-validation",
        action="store_true",
        help="Use nested stratified group-aware cross-validation instead of a single train/val/test split",
    )
    parser.add_argument(
        "--outer-folds",
        type=int,
        default=5,
        help="Number of outer CV folds (default: 5)",
    )
    parser.add_argument(
        "--inner-folds",
        type=int,
        default=4,
        help="Number of inner CV folds (default: 4; automatically reduced if needed)",
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


def _log_split_counts(name: str, split_data: Dict[str, Any]) -> None:
    """Log train/val/test execution counts and class balance."""
    for split in ("train", "val", "test"):
        eids = split_data.get(f"{split}_execution_ids", [])
        labels = split_data.get(f"{split}_labels", [])
        pos = sum(1 for l in labels if l == 1)
        neg = len(labels) - pos
        logger.info(
            "%s %s: %d executions (pos=%d, neg=%d)",
            name, split, len(eids), pos, neg,
        )


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
    logger.info("LR           : %.2e", args.lr)
    logger.info("Weight decay : %.2e", args.weight_decay)

    if args.cross_validation:
        logger.info(
            "CV mode      : %d outer folds, %d inner folds",
            args.outer_folds, args.inner_folds,
        )
    else:
        logger.info(
            "Split        : train=%.0f%% val=%.0f%% test=%.0f%%",
            args.train_frac * 100, args.val_frac * 100,
            (1 - args.train_frac - args.val_frac) * 100,
        )

    # ── Build pipeline ────────────────────────────────────────────────────────
    pipeline = DetectorPipeline(
        output_dir=output_dir,
        seed=args.seed,
        device=args.device or "auto",
        snapshot_interval=args.snapshot_interval,
    )

    # ── Run ───────────────────────────────────────────────────────────────────
    if args.cross_validation:
        from training.cross_validation import NestedGroupCV
        cv = NestedGroupCV(
            outer_folds=args.outer_folds,
            inner_folds=args.inner_folds,
            seed=args.seed,
        )
        results = pipeline.run_cross_validation(
            detector_types=args.detectors,
            snapshot_interval=args.snapshot_interval,
            num_epochs=args.epochs,
            batch_size=args.batch_size,
            patience=args.patience,
            lr=args.lr,
            weight_decay=args.weight_decay,
            cv=cv,
            use_heuristic_baselines=not args.no_heuristic_baselines,
        )
    else:
        results = pipeline.run(
            detector_types=args.detectors,
            snapshot_interval=args.snapshot_interval,
            num_epochs=args.epochs,
            batch_size=args.batch_size,
            patience=args.patience,
            lr=args.lr,
            weight_decay=args.weight_decay,
            train_frac=args.train_frac,
            val_frac=args.val_frac,
            use_heuristic_baselines=not args.no_heuristic_baselines,
        )

    if args.cross_validation:
        from dataclasses import asdict
        for name, result in results.items():
            print(f"{name}: pooled OOF {result.pooled_metrics}")
            for key, mean in result.mean_metrics.items():
                print(f"  {key}: {mean:.4f} ± {result.std_metrics[key]:.4f}")
        if args.results_json:
            path = Path(args.results_json)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({k: asdict(v) for k, v in results.items()}, indent=2))
        return 0

    # The pipeline also returns metadata such as the comparison DataFrame.
    detector_results = {
        name: result for name, result in results.items()
        if isinstance(result, PipelineResult)
    }

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    for name, result in detector_results.items():
        test_metrics = result.test_metrics.get("metrics", {})
        tm_at_thresh = result.test_metrics.get("metrics_at_threshold", {})
        thresh_str = f"threshold={result.best_threshold:.4f}" if getattr(result, "best_threshold", None) else ""
        print(
            f"  {name:20s}  pos_weight={getattr(result, 'pos_weight', 1.0):.4f}  "
            f"val AUPRC={getattr(result, 'best_val_auprc', result.best_val_auroc):.4f}  "
            f"test AUROC={test_metrics.get('auroc', float('nan')):.4f}  "
            f"test AUPRC={test_metrics.get('auprc', float('nan')):.4f}  "
            f"{thresh_str}  "
            f"test F1={tm_at_thresh.get('f1', float('nan')):.4f}  "
            f"(epoch {result.best_epoch})"
        )
    print("=" * 60)

    # ── Optional JSON output ──────────────────────────────────────────────────
    if args.results_json:
        results_path = Path(args.results_json)
        results_path.parent.mkdir(parents=True, exist_ok=True)
        serializable = {}
        for name, result in detector_results.items():
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
