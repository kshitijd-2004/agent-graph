"""AgentGraphs training / evaluation harness.

    python train.py --data out --traces traces --model logreg --diagnose

Harness only. GAT / JODIE / TGN are declared against one interface and raise
NotImplementedError until wired to PyG / DyGLib.
"""

import argparse
import glob
import json
import os
from collections import defaultdict

import numpy as np


# ---------------------------------------------------------------- data
def load_exported(data_dir, view):
    out = {}
    for p in sorted(glob.glob(os.path.join(data_dir, view, "*.npz"))):
        d = np.load(p, allow_pickle=True)
        out[os.path.basename(p)[:-4]] = {k: d[k] for k in d.files}
    return out


def load_traces(trace_dir):
    runs = {}
    for p in sorted(glob.glob(os.path.join(trace_dir, "*.jsonl"))):
        evs = [json.loads(l) for l in open(p) if l.strip()]
        if evs:
            runs[evs[0]["trace_id"]] = evs
    return runs


def execution_groups(runs):
    return {tid: evs[0].get("execution_id", tid) for tid, evs in runs.items()}


def grouped_folds(trace_ids, groups):
    # group by execution: a benign run and its perturbed twin must not split
    by_group = defaultdict(list)
    for tid in trace_ids:
        by_group[groups.get(tid, tid)].append(tid)
    for g in sorted(by_group):
        train = [t for k, ts in by_group.items() if k != g for t in ts]
        yield g, train, by_group[g]


# ------------------------------------------------------------- metrics
def auc_pr(y_true, y_score):
    from sklearn.metrics import average_precision_score
    if len(np.unique(y_true)) < 2:
        return float("nan")
    return float(average_precision_score(y_true, y_score))


def precision_at_k(y_true, y_score, k=None):
    if y_true.sum() == 0:
        return float("nan")
    k = k or int(y_true.sum())
    return float(y_true[np.argsort(-y_score)[:k]].mean())


def lead_time(y_score, failure_idx, threshold=0.5):
    # events between first flag and the failure; None if never flagged before it
    flagged = np.where(y_score[:failure_idx] >= threshold)[0]
    return None if len(flagged) == 0 else int(failure_idx - flagged[0])


# -------------------------------------------------------------- models
class Model:
    name, view = "base", "static"

    def fit(self, train_graphs):
        raise NotImplementedError

    def predict_edges(self, graph):
        raise NotImplementedError


class LogRegBaseline(Model):
    """Edge features only, no structure, no time. The floor of the ladder."""
    name, view = "logreg", "static"

    def fit(self, train_graphs):
        from sklearn.linear_model import LogisticRegression
        X = np.vstack([g["edge_attr"] for g in train_graphs])
        y = np.concatenate([g["y_edge"] for g in train_graphs])
        self.clf = None
        if len(np.unique(y)) > 1:
            self.clf = LogisticRegression(
                max_iter=1000, class_weight="balanced").fit(X, y)
        return self

    def predict_edges(self, graph):
        if self.clf is None:
            return np.zeros(len(graph["y_edge"]))
        return self.clf.predict_proba(graph["edge_attr"])[:, 1]


class GATModel(Model):
    """Static baseline: [h_src || h_dst || edge_attr] -> MLP. No time."""
    name, view = "gat", "static"

    def fit(self, train_graphs):
        raise NotImplementedError(
            "Wire to torch_geometric.nn.GATConv. Inputs already in PyG shape: "
            "x [N, 6], edge_index [2, E], edge_attr [E, 73].")

    def predict_edges(self, graph):
        raise NotImplementedError


class JODIEModel(Model):
    """Temporal baseline: evolving embeddings, weaker memory than TGN."""
    name, view = "jodie", "temporal"

    def fit(self, train_graphs):
        raise NotImplementedError(
            "Wire to DyGLib. Inputs already in stream shape: "
            "src [E], dst [E], t [E], msg [E, 73].")

    def predict_edges(self, graph):
        raise NotImplementedError


class TGNModel(Model):
    """Primary model. use_memory=False is the key ablation."""
    name, view = "tgn", "temporal"

    def __init__(self, use_memory=True):
        self.use_memory = use_memory

    def fit(self, train_graphs):
        raise NotImplementedError(
            "Wire to DyGLib TGN. Set memory_dim=0 for the ablation arm.")

    def predict_edges(self, graph):
        raise NotImplementedError


MODELS = {m.name: m for m in [LogRegBaseline, GATModel, JODIEModel, TGNModel]}


# --------------------------------------------------------- diagnostics
def diagnose(runs):
    """Report data problems. Does not correct for them."""
    from sklearn.metrics import average_precision_score
    print("\n--- DATA DIAGNOSTICS ---")

    sigs = defaultdict(list)
    for tid, evs in runs.items():
        sig = (tuple(e["event_id"] for e in evs if e.get("lep_injected")),
               evs[0].get("failure_type"))
        sigs[str(sig)].append(tid)
    print(f"  unique injection patterns: {len(sigs)} across {len(runs)} runs")
    for tids in sigs.values():
        if len(tids) > 1:
            print(f"    [WARN] identical pattern: {tids}")

    print("  shortcut check (event_type alone -> lep_injected):")
    for tid, evs in sorted(runs.items()):
        y = np.array([1 if e.get("lep_injected") else 0 for e in evs])
        if y.sum() == 0:
            continue
        best, best_ap = None, 0.0
        for et in {e["event_type"] for e in evs}:
            rule = np.array([1.0 if e["event_type"] == et else 0.0
                             for e in evs])
            ap = average_precision_score(y, rule)
            if ap > best_ap:
                best, best_ap = et, ap
        flag = "   <== label recoverable from event_type alone" \
            if best_ap > 0.6 else ""
        print(f"    {tid:<16} best='{best}' AUC-PR={best_ap:.3f}{flag}")
    print("--- end diagnostics ---\n")


# ---------------------------------------------------------------- eval
def evaluate(model_cls, data_dir, trace_dir, **kw):
    graphs = load_exported(data_dir, model_cls.view)
    if not graphs:
        raise SystemExit(f"no {model_cls.view} exports in {data_dir}/ "
                         f"-- run agentgraph.py first")
    groups = execution_groups(load_traces(trace_dir))

    print(f"  model={model_cls.name}  view={model_cls.view}")
    print(f"  {len(graphs)} runs, {len(set(groups.values()))} executions "
          f"(leave-one-execution-out)")

    rows = []
    for fold, train_ids, test_ids in grouped_folds(graphs, groups):
        model = model_cls(**kw).fit([graphs[t] for t in train_ids])
        for tid in test_ids:
            g = graphs[tid]
            s, y = model.predict_edges(g), g["y_edge"]
            rows.append({"fold": fold, "trace": tid, "auc_pr": auc_pr(y, s),
                         "p_at_k": precision_at_k(y, s),
                         "pos": int(y.sum()), "n": len(y)})

    print(f"\n  {'execution':<14} {'trace':<16} {'AUC-PR':>8} {'P@k':>8} "
          f"{'pos/n':>9}")
    for r in rows:
        print(f"  {r['fold']:<14} {r['trace']:<16} {r['auc_pr']:>8.3f} "
              f"{r['p_at_k']:>8.3f} {str(r['pos'])+'/'+str(r['n']):>9}")

    valid = [r["auc_pr"] for r in rows if not np.isnan(r["auc_pr"])]
    if valid:
        print(f"\n  mean AUC-PR: {np.mean(valid):.3f}  (n={len(valid)})")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="out")
    ap.add_argument("--traces", default="traces")
    ap.add_argument("--model", default="logreg", choices=list(MODELS))
    ap.add_argument("--no-memory", action="store_true",
                    help="TGN ablation: disable the memory module")
    ap.add_argument("--diagnose", action="store_true",
                    help="report data problems before evaluating")
    args = ap.parse_args()

    if args.diagnose:
        diagnose(load_traces(args.traces))

    kw = {"use_memory": not args.no_memory} if args.model == "tgn" else {}
    print(">>> EVAL")
    evaluate(MODELS[args.model], args.data, args.traces, **kw)
    print()


if __name__ == "__main__":
    main()
