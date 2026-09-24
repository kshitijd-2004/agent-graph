"""Nested CV with isolated task groups and selection from complete inner OOF scores."""
from dataclasses import dataclass, asdict
import logging
from pathlib import Path
import json

import numpy as np
from training.metrics import compute_classification_metrics, find_best_f1_threshold

logger = logging.getLogger(__name__)


def grouped_folds(labels, groups, requested, seed, reduce=False):
    """Stratify whole groups, guaranteeing both classes in every partition.

    Seed mixed groups first, then pair positive-only and negative-only groups.
    The remaining groups minimize normalized class-count imbalance. This
    construction finds a class-preserving assignment whenever one is feasible.
    """
    labels, groups = np.asarray(labels), np.asarray(groups)
    unique = list(dict.fromkeys(groups.tolist()))
    counts = {g: np.array([sum(labels[groups == g] == c) for c in (0, 1)])
              for g in unique}
    capacity = min(sum(v[c] > 0 for v in counts.values()) for c in (0, 1))
    k = min(requested, capacity) if reduce else requested
    if k < 2 or capacity < k:
        raise ValueError(f"Cannot create {requested} grouped folds with both classes; capacity={capacity}")
    if k != requested:
        logger.warning("Reducing inner folds from %d to %d due to group/class constraints", requested, k)
    rng = np.random.default_rng(seed)
    rng.shuffle(unique)
    mixed = [g for g in unique if np.all(counts[g] > 0)]
    only = [[g for g in unique if counts[g][c] > 0 and counts[g][1-c] == 0]
            for c in (0, 1)]
    folds = [[] for _ in range(k)]
    for i in range(k):
        if mixed:
            folds[i].append(mixed.pop())
        else:
            folds[i].extend([only[0].pop(), only[1].pop()])
    totals = np.array([sum((counts[g] for g in f), np.zeros(2)) for f in folds])
    target = np.maximum(np.sum(list(counts.values()), axis=0) / k, 1)
    remaining = mixed + only[0] + only[1]
    remaining.sort(key=lambda g: max(counts[g] / target), reverse=True)
    for g in remaining:
        def cost(i):
            proposed = totals.copy()
            proposed[i] += counts[g]
            return np.var(proposed / target, axis=0).sum()
        i = min(range(k), key=cost)
        folds[i].append(g)
        totals[i] += counts[g]
    splits = []
    for f in folds:
        val = np.flatnonzero(np.isin(groups, f))
        train = np.flatnonzero(~np.isin(groups, f))
        if len(set(labels[val])) != 2 or len(set(labels[train])) != 2:
            raise ValueError("Grouped partition lacks both classes")
        splits.append((train, val))
    return splits


@dataclass
class CrossValidationResults:
    outer_folds: int
    fold_results: list
    per_fold_metrics: list
    mean_metrics: dict
    std_metrics: dict
    pooled_metrics: dict
    outer_test_predictions: list

    def save(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2))


class NestedGroupCV:
    """Select a configuration by pooled inner OOF AUPRC; calibrate its F1 threshold.

    fit_fn(train_execution_ids, train_labels, pos_weight, config) returns a
    fresh fitted model. predict_fn(model, execution_ids) returns one score per
    execution. Neither callback receives validation/test labels. Configuration
    budgets are fixed before fitting; no validation-driven early stopping occurs.
    """
    def __init__(self, outer_folds=5, inner_folds=4, seed=42, auto_reduce_inner=True):
        if outer_folds != 5:
            raise ValueError("Final evaluation requires exactly 5 outer folds")
        if inner_folds < 2:
            raise ValueError("At least two inner folds are required")
        self.outer_folds, self.inner_folds = outer_folds, inner_folds
        self.seed, self.auto_reduce_inner = seed, auto_reduce_inner

    def run(self, execution_ids, labels, group_ids, fit_fn, predict_fn, configurations):
        if not (len(execution_ids) == len(labels) == len(group_ids)):
            raise ValueError("Execution IDs, labels and groups must align")
        if len(set(execution_ids)) != len(execution_ids):
            raise ValueError("Expected exactly one entry per execution")
        if set(labels) != {0, 1} or not configurations:
            raise ValueError("Both binary classes and at least one configuration are required")
        eids, y, groups = np.asarray(execution_ids), np.asarray(labels), np.asarray(group_ids)
        outer = grouped_folds(y, groups, 5, self.seed)
        # Preflight every inner partition before spending time fitting models.
        inner = [grouped_folds(y[pool], groups[pool], self.inner_folds,
                              self.seed + i + 1, self.auto_reduce_inner)
                 for i, (pool, _) in enumerate(outer)]
        records, folds = [], []

        def fit(indices, config):
            weight = float(sum(y[indices] == 0) / sum(y[indices] == 1))
            return fit_fn(eids[indices].tolist(), y[indices].tolist(), weight, config)

        def predict(model, indices):
            scores = np.asarray(predict_fn(model, eids[indices].tolist()), dtype=float)
            if scores.shape != (len(indices),) or not np.isfinite(scores).all():
                raise ValueError("Predictions must contain one finite score per execution")
            return scores

        for i, (pool, test) in enumerate(outer):
            candidates = []
            for config in configurations:
                oof = np.full(len(pool), np.nan)
                for train, val in inner[i]:
                    model = fit(pool[train], config)
                    oof[val] = predict(model, pool[val])
                    del model
                if not np.isfinite(oof).all():
                    raise RuntimeError("Incomplete inner OOF coverage")
                metrics = compute_classification_metrics(y[pool], oof)
                candidates.append((metrics['auprc'], dict(config), oof))
            _, selected, oof = max(candidates, key=lambda candidate: candidate[0])
            threshold = float(find_best_f1_threshold(y[pool], oof))
            model = fit(pool, selected)
            scores = predict(model, test)  # Exactly one untouched outer-test prediction pass.
            del model
            metrics = compute_classification_metrics(y[test], scores, threshold=threshold)
            folds.append(dict(fold=i, train_eids=eids[pool].tolist(), test_eids=eids[test].tolist(),
                              inner_folds_used=len(inner[i]), selected_configuration=selected,
                              candidate_auprc=[dict(configuration=c, auprc=a) for a,c,_ in candidates],
                              threshold=threshold, pos_weight=float(sum(y[pool]==0)/sum(y[pool]==1)),
                              inner_oof_predictions=[dict(execution_id=str(e), label=float(l), score=float(s))
                                                     for e,l,s in zip(eids[pool],y[pool],oof)],
                              metrics=metrics))
            for idx, score in zip(test, scores):
                records.append(dict(fold=i, execution_id=str(eids[idx]), group_id=str(groups[idx]),
                                    label=float(y[idx]), score=float(score), threshold=threshold,
                                    prediction=int(score >= threshold)))
            logger.info("Outer fold %d: config=%s threshold=%.6f metrics=%s", i+1, selected, threshold, metrics)
        keys = ('auroc', 'auprc', 'precision', 'recall', 'f1')
        per_fold = [f['metrics'] for f in folds]
        mean = {k: float(np.mean([m[k] for m in per_fold])) for k in keys}
        std = {k: float(np.std([m[k] for m in per_fold], ddof=1)) for k in keys}
        truth = np.array([r['label'] for r in records])
        pooled = compute_classification_metrics(truth, np.array([r['score'] for r in records]))
        decisions = compute_classification_metrics(truth, np.array([r['prediction'] for r in records]))
        for k in ('precision', 'recall', 'f1', 'accuracy', 'tp', 'fp', 'tn', 'fn'):
            pooled[k] = decisions[k]
        for k in keys:
            logger.info("%s: %.4f ± %.4f; pooled OOF %.4f", k, mean[k], std[k], pooled[k])
        return CrossValidationResults(5, folds, per_fold, mean, std, pooled, records)
