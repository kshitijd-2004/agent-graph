"""Evaluation metrics for LEP detection.

Provides standard binary classification metrics (AUROC, AUPR, accuracy,
precision, recall, F1) and early-warning evaluation (lead time at fixed
FPR). All metrics operate on raw probability scores and boolean labels.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score

logger = logging.getLogger(__name__)


# ── Binary classification metrics ─────────────────────────────────────────────


def compute_auroc(y_true: np.ndarray, y_scores: np.ndarray) -> float:
    """Compute Area Under the Receiver Operating Characteristic curve.

    Uses sklearn.metrics.roc_auc_score for correctness.

    Args:
        y_true:  Boolean or float array of ground-truth labels [N].
        y_scores: Float array of probability scores [N].

    Returns:
        AUROC value in [0, 1]. Returns NaN if all labels are the same or
        if the input is empty.
    """
    y_true = np.asarray(y_true, dtype=float)
    y_scores = np.asarray(y_scores, dtype=float)

    if len(y_true) == 0 or y_true.min() == y_true.max():
        return float("nan")

    return float(roc_auc_score(y_true, y_scores))


def compute_aupr(y_true: np.ndarray, y_scores: np.ndarray) -> float:
    """Compute Area Under the Precision-Recall curve (Average Precision).

    Uses sklearn.metrics.average_precision_score for correctness.

    Args:
        y_true:  Boolean or float array of ground-truth labels [N].
        y_scores: Float array of probability scores [N].

    Returns:
        AUPR value in [0, 1]. Returns the positive-class proportion if all
        labels are the same (matches sklearn behavior for single class).
    """
    y_true = np.asarray(y_true, dtype=float)
    y_scores = np.asarray(y_scores, dtype=float)

    if len(y_true) == 0:
        return 0.0

    return float(average_precision_score(y_true, y_scores))


def compute_classification_metrics(
    y_true: np.ndarray,
    y_scores: np.ndarray,
    threshold: float = 0.5,
) -> dict:
    """Compute standard binary classification metrics at a given threshold.

    Args:
        y_true:    Ground-truth labels [N].
        y_scores:  Probability scores [N].
        threshold: Classification threshold (default 0.5).

    Returns:
        Dict with accuracy, precision, recall, f1, auprc, and confusion
        matrix counts.
    """
    y_true = np.asarray(y_true, dtype=float)
    y_scores = np.asarray(y_scores, dtype=float)
    y_pred = (y_scores >= threshold).astype(float)

    tp = float(((y_pred == 1) & (y_true == 1)).sum())
    fp = float(((y_pred == 1) & (y_true == 0)).sum())
    tn = float(((y_pred == 0) & (y_true == 0)).sum())
    fn = float(((y_pred == 0) & (y_true == 1)).sum())

    accuracy = (tp + tn) / (tp + fp + tn + fn + 1e-10)
    precision = tp / (tp + fp + 1e-10)
    recall = tp / (tp + fn + 1e-10)
    f1 = 2 * precision * recall / (precision + recall + 1e-10)
    auprc = compute_aupr(y_true, y_scores)
    auproc = compute_auroc(y_true, y_scores)

    return {
        "accuracy": float(accuracy),
        "precision": float(precision),
        "recall": float(recall),
        "f1": float(f1),
        "auprc": float(auprc),
        "auroc": float(auproc),
        "tp": int(tp),
        "fp": int(fp),
        "tn": int(tn),
        "fn": int(fn),
    }


def compute_lead_time(
    risk_scores: np.ndarray,
    event_indices: np.ndarray,
    failure_event_idx: int,
    fpr_target: float = 0.1,
    use_benign_calibration: bool = False,
    benign_scores: Optional[np.ndarray] = None,
) -> Optional[float]:
    """Compute early-warning lead time at a target false positive rate.

    The detector raises an alert when the risk score exceeds a threshold
    calibrated to achieve ``fpr_target``. Lead time is the number of events
    between the first alert and the actual failure event.

    Args:
        risk_scores:         Per-event risk scores [num_events].
        event_indices:       Event index for each risk score [num_events].
        failure_event_idx:   Index of the first failure event (absolute index
                              in the full trace, not within this prefix).
        fpr_target:          Target false positive rate for threshold
                              calibration (default 0.1 = 10%).
        use_benign_calibration: If True, calibrate threshold from benign scores
                                rather than percentile on this trace.
        benign_scores:       Risk scores from benign traces for calibration
                             [num_benign_events]. Required if
                             use_benign_calibration is True.

    Returns:
        Lead time in number of events. Returns None if no alert is raised
        before failure, or if failure_event_idx is None.
    """
    if failure_event_idx is None:
        return None

    risk_scores = np.asarray(risk_scores, dtype=float)
    event_indices = np.asarray(event_indices, dtype=int)

    # Calibrate threshold
    if use_benign_calibration and benign_scores is not None and len(benign_scores) > 0:
        benign_scores = np.asarray(benign_scores, dtype=float)
        threshold = float(np.percentile(benign_scores, 100 * (1 - fpr_target)))
    else:
        # Calibrate from this trace's scores: threshold at (1-fpr_target) percentile
        threshold = float(np.percentile(risk_scores, 100 * (1 - fpr_target)))

    # Find first alert before failure
    pre_failure_mask = event_indices < failure_event_idx
    alert_mask = risk_scores >= threshold

    alert_before_failure = pre_failure_mask & alert_mask
    alert_indices = event_indices[alert_before_failure]

    if len(alert_indices) == 0:
        return None

    first_alert = int(alert_indices.min())
    lead_time = failure_event_idx - first_alert
    return float(lead_time)


def compute_lead_time_at_multiple_fpr(
    risk_scores: np.ndarray,
    event_indices: np.ndarray,
    failure_event_idx: Optional[int],
    fpr_values: list[float] = None,
) -> dict:
    """Compute lead time at multiple FPR thresholds.

    Args:
        risk_scores:         Per-event risk scores [num_events].
        event_indices:       Event index for each risk score [num_events].
        failure_event_idx:   Index of the first failure event.
        fpr_values:          List of FPR values to evaluate (default [0.05, 0.1, 0.2]).

    Returns:
        Dict mapping FPR to lead time (float or None).
    """
    if fpr_values is None:
        fpr_values = [0.05, 0.1, 0.2]

    results = {}
    for fpr in fpr_values:
        lt = compute_lead_time(risk_scores, event_indices, failure_event_idx,
                               fpr_target=fpr)
        results[f"lead_time_fpr_{fpr}"] = lt

    return results


# ── Aggregation helpers ────────────────────────────────────────────────────────


def aggregate_metrics(metric_dicts: list[dict]) -> dict:
    """Aggregate per-graph metrics across a dataset.

    Computes mean and std for all numeric fields.

    Args:
        metric_dicts: List of metric dicts (from compute_classification_metrics).

    Returns:
        Dict with ``{field}_mean`` and ``{field}_std`` for each numeric field.
    """
    if not metric_dicts:
        return {}

    keys = [k for k in metric_dicts[0].keys() if isinstance(metric_dicts[0][k], (int, float))]
    result = {}

    for key in keys:
        values = np.array([m[key] for m in metric_dicts], dtype=float)
        result[f"{key}_mean"] = float(values.mean())
        result[f"{key}_std"] = float(values.std())
        result[f"{key}_min"] = float(values.min())
        result[f"{key}_max"] = float(values.max())

    return result


def find_best_f1_threshold(y_true: np.ndarray, y_scores: np.ndarray) -> float:
    """Maximize F1 over observed scores; ties prefer the highest threshold."""
    from sklearn.metrics import precision_recall_curve
    y_true, y_scores = np.asarray(y_true), np.asarray(y_scores, dtype=float)
    if y_true.shape != y_scores.shape or y_true.size == 0 or not np.isfinite(y_scores).all():
        raise ValueError("Threshold calibration needs aligned, finite, nonempty OOF predictions")
    precision, recall, thresholds = precision_recall_curve(y_true, y_scores)
    denominator = precision[:-1] + recall[:-1]
    f1 = np.divide(2 * precision[:-1] * recall[:-1], denominator,
                   out=np.zeros_like(denominator), where=denominator > 0)
    return float(thresholds[np.flatnonzero(f1 == f1.max())[-1]])
