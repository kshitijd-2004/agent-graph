"""Evaluation framework for detector comparison.

Provides:
- DetectionEvaluator: Per-detector evaluation with standard metrics.
- BaselineComparator: Cross-detector comparison table.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from training.metrics import (
    aggregate_metrics,
    compute_aupr,
    compute_auroc,
    compute_classification_metrics,
    compute_lead_time,
    compute_lead_time_at_multiple_fpr,
)

logger = logging.getLogger(__name__)


class DetectionEvaluator:
    """Evaluate detector performance with standard ML and early-warning metrics.

    Supports both learned detectors (StaticGNN, TemporalGNN, HybridDetector)
    and heuristic baselines (HeuristicDetector).

    Usage::

        evaluator = DetectionEvaluator()
        results = evaluator.evaluate_detector(
            y_true=labels,
            y_scores=predictions,
            detector_name="StaticGNN",
        )
        summary = evaluator.evaluate_detector_with_lead_time(
            y_true=labels,
            y_scores=predictions,
            event_indices=event_indices,
            failure_indices=failure_indices,
            benign_scores=benign_predictions,
            detector_name="TemporalGNN",
        )
    """

    def __init__(self, fpr_values: Optional[List[float]] = None) -> None:
        """Initialize evaluator.

        Args:
            fpr_values: FPR thresholds for lead time evaluation.
                        Default [0.05, 0.1, 0.2].
        """
        self.fpr_values = fpr_values or [0.05, 0.1, 0.2]
        self.results: Dict[str, Dict[str, Any]] = {}

    def evaluate_detector(
        self,
        y_true: List[float],
        y_scores: List[float],
        detector_name: str,
        threshold: float = 0.5,
    ) -> Dict[str, Any]:
        """Evaluate a detector with standard classification metrics.

        Args:
            y_true:       Ground-truth labels [N].
            y_scores:     Probability scores [N].
            detector_name: Name for this detector (used in results dict).
            threshold:    Classification threshold.

        Returns:
            Dict of metrics for this detector.
        """
        y_true_np = np.array(y_true, dtype=float)
        y_scores_np = np.array(y_scores, dtype=float)

        metrics = compute_classification_metrics(y_true_np, y_scores_np, threshold=threshold)

        result = {
            "detector": detector_name,
            "num_samples": len(y_true),
            "threshold": threshold,
            **metrics,
        }

        self.results[detector_name] = result
        return result

    def evaluate_detector_with_lead_time(
        self,
        y_true: List[float],
        y_scores: List[float],
        event_indices: List[int],
        failure_indices: Optional[List[Optional[int]]],
        benign_scores: Optional[List[float]] = None,
        detector_name: str = "unknown",
        threshold: float = 0.5,
    ) -> Dict[str, Any]:
        """Evaluate with early-warning lead time at multiple FPR levels.

        Args:
            y_true:           Ground-truth labels [N].
            y_scores:         Probability scores [N].
            event_indices:    Event index for each score [N].
            failure_indices:  First failure event index per trace [N], or
                              None for traces without failure.
            benign_scores:    Scores from benign traces for FPR calibration.
            detector_name:    Detector name.
            threshold:        Classification threshold.

        Returns:
            Dict of metrics including lead time values.
        """
        base_metrics = self.evaluate_detector(
            y_true, y_scores, detector_name=detector_name, threshold=threshold
        )

        y_scores_np = np.array(y_scores, dtype=float)
        event_indices_np = np.array(event_indices, dtype=int)
        benign_scores_np = (
            np.array(benign_scores, dtype=float) if benign_scores else None
        )

        # Compute lead time for each trace that has a failure
        lead_times = []
        lead_times_calibrated = []

        for i, failure_idx in enumerate(failure_indices):
            if failure_idx is None:
                continue

            # Per-trace: find the range of events belonging to this trace
            # This is simplified — in practice, event_indices should be
            # grouped by trace_id
            trace_mask = (event_indices_np >= failure_idx - 100) & (
                event_indices_np <= failure_idx + 10
            )
            trace_scores = y_scores_np[trace_mask]
            trace_event_idx = event_indices_np[trace_mask]

            if len(trace_scores) == 0:
                continue

            lt = compute_lead_time(
                trace_scores, trace_event_idx, failure_idx,
                use_benign_calibration=False,
            )
            lead_times.append(lt)

            lt_cal = compute_lead_time(
                trace_scores, trace_event_idx, failure_idx,
                use_benign_calibration=True,
                benign_scores=benign_scores_np,
            )
            lead_times_calibrated.append(lt_cal)

        def _stats(vals):
            valid = [v for v in vals if v is not None]
            if not valid:
                return {"mean": None, "median": None, "max": None, "count": 0}
            return {
                "mean": float(np.mean(valid)),
                "median": float(np.median(valid)),
                "max": float(np.max(valid)),
                "count": len(valid),
            }

        lead_time_stats = _stats(lead_times)
        lead_time_cal_stats = _stats(lead_times_calibrated)

        base_metrics.update({
            "lead_time_uncalibrated": lead_time_stats,
            "lead_time_calibrated": lead_time_cal_stats,
        })

        self.results[detector_name] = base_metrics
        return base_metrics

    def compare(self) -> pd.DataFrame:
        """Return a comparison table of all evaluated detectors.

        Returns:
            Pandas DataFrame with one row per detector and columns for
            all computed metrics.
        """
        rows = []
        for name, result in self.results.items():
            row = {"detector": name}
            # Flatten nested dicts
            for key, value in result.items():
                if isinstance(value, dict):
                    for sub_key, sub_val in value.items():
                        row[f"{key}.{sub_key}"] = sub_val
                else:
                    row[key] = value
            rows.append(row)

        return pd.DataFrame(rows)

    def summary(self) -> str:
        """Print a human-readable comparison summary."""
        df = self.compare()
        if df.empty:
            return "No results to compare."

        # Select key columns
        key_cols = ["detector", "auroc", "auprc", "f1", "precision", "recall",
                    "accuracy"]
        available = [c for c in key_cols if c in df.columns]
        if not available:
            return str(df)

        return df[available].to_string(index=False)


class BaselineComparator:
    """Compare all detector types on the same train/val/test split.

    Runs evaluation for each detector and produces a comparison table.
    """

    def __init__(self, fpr_values: Optional[List[float]] = None) -> None:
        self.evaluator = DetectionEvaluator(fpr_values=fpr_values)

    def add_results(
        self,
        detector_name: str,
        y_true: List[float],
        y_scores: List[float],
        event_indices: Optional[List[int]] = None,
        failure_indices: Optional[List[Optional[int]]] = None,
        benign_scores: Optional[List[float]] = None,
    ) -> Dict[str, Any]:
        """Add evaluation results for a detector.

        If lead time data is provided, computes lead time metrics too.
        """
        if event_indices is not None and failure_indices is not None:
            return self.evaluator.evaluate_detector_with_lead_time(
                y_true=y_true,
                y_scores=y_scores,
                event_indices=event_indices,
                failure_indices=failure_indices,
                benign_scores=benign_scores,
                detector_name=detector_name,
            )
        else:
            return self.evaluator.evaluate_detector(
                y_true=y_true,
                y_scores=y_scores,
                detector_name=detector_name,
            )

    def compare(self) -> pd.DataFrame:
        """Return comparison DataFrame across all added detectors."""
        return self.evaluator.compare()

    def summary(self) -> str:
        """Print comparison summary."""
        return self.evaluator.summary()
