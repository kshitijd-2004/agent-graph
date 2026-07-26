"""
AgentGraphs -- trace -> graph -> model-ready tensors.   (Task II)

    python agentgraph.py --traces ../agent-graph/traces --out out --stage detect

PIPELINE
    traces/*.jsonl
      -> [convert]  entity-node graph (plain-python IR, inspectable)
      -> [export]   +-- STATIC   (PyG Data)      -> GAT / MPNN
                    +-- TEMPORAL (DyGLib stream) -> JODIE / TGN

DESIGN
  * ENTITIES are nodes (agents, tools, user, system, internal) -- persistent.
  * EVENTS are timestamped directed edges (source -> target).
  * One graph = one RUN = events sharing a trace_id, sorted by event_id.
  * Model features and ground-truth labels are split AT THE SOURCE.

  Why entity-as-node, not event-as-node:
    TGN's memory is PER-PERSISTENT-NODE. If each event were its own node, every
    node would be a one-shot with no history, so memory could not accumulate an
    early injection and carry it to a later failure -- collapsing TGN toward a
    static model. Persistent entities are what make the temporal hypothesis
    testable at all.

  Why features/labels split at the source:
    If the model is given the propagation fields and then asked to predict
    propagation, the answer is leaked.

  Why both views share one IR / one encoder / one label set:
    That is the experimental control. If the static baseline and TGN saw
    different features, any performance gap would be a pipeline artifact rather
    than evidence about propagation.
"""

import argparse
import glob
import json
import os
from collections import defaultdict
from datetime import datetime

import numpy as np


# =========================================================================
# SCHEMA -- THE SINGLE POINT OF UPDATE when the trace schema drifts.
#           Nothing below hard-codes a field name.
# =========================================================================
# ---- identity / structure ------------------------------------------------
F_TRACE     = "trace_id"       # unique per execution (one run = one graph)
F_EXEC      = "execution_id"   # shared across paired runs (benign/malignant)
F_EVENT     = "event_id"       # ordering within a run
F_TIME      = "timestamp"      # ISO-8601
F_TYPE      = "event_type"
F_SRC       = "source"
F_DST       = "target"

# ---- MODEL INPUTS (safe: knowable without seeing the outcome) -------------
# Structural/contextual features. Deliberately EXCLUDES anything the labeller
# added after the fact.
EDGE_FEATURE_FIELDS = [
    "event_type",
    "agent_id", "agent_name", "agent_role",
    "tool_id", "tool_name",
    "input_summary", "output_summary",   # -> text embeddings
]

# ---- HELD-OUT GROUND TRUTH (never a model input) --------------------------
# Splitting these at the source is what prevents label leakage: if the model
# is asked to predict propagation, it cannot also be *given* the propagation.
LABEL_FIELDS = [
    "lep_injected", "lep_type", "lep_category", "lep_severity", "lep_location",
    "downstream_failure", "failure_type", "failure_event",
    "caused_by_event", "propagates_to", "depends_on",
    "risk_tags",
]

# ---- categorical vocabularies (for one-hot encoding) ---------------------
EVENT_TYPES = [
    "user_input", "system_init", "reasoning", "tool_call",
    "tool_result", "agent_handoff", "final_response",
]

# ---- node typing ---------------------------------------------------------
# Entities are nodes. Type is inferred from the id string.
def node_type(node_id: str) -> str:
    n = str(node_id)
    if n.startswith("agent_"):
        return "agent"
    if n.startswith("tool_"):
        return "tool"
    if n in ("user",):
        return "user"
    if n in ("system", "multi_agent_system"):
        return "system"
    if n in ("internal",):
        return "internal"     # agent's own scratchpad / reasoning sink
    return "other"

NODE_TYPES = ["agent", "tool", "user", "system", "internal", "other"]


# =========================================================================
# CONVERT -- traces -> entity-node graph IR
# =========================================================================
# ---------------------------------------------------------------- loading
def load_events(paths, prefer_labeled=True):
    """Read JSONL files into a flat event list.

    `prefer_labeled`: when both `X.jsonl` and `X_labeled.jsonl` exist they carry
    the SAME trace_id, so loading both would merge two copies of one run into a
    doubled graph. We keep the labeled one.

    (This is a workaround for the distinction living in the *filename* rather
    than in the data. The robust fix is a `labeled: true` field -- flagged to KJ.)
    """
    paths = list(paths)
    if prefer_labeled:
        shadowed = {p.replace("_labeled.jsonl", ".jsonl")
                    for p in paths if p.endswith("_labeled.jsonl")}
        paths = [p for p in paths if p not in shadowed]

    events, bad = [], 0
    for path in paths:
        with open(path) as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                    ev["_src_file"] = os.path.basename(path)
                    events.append(ev)
                except json.JSONDecodeError:
                    bad += 1
    if bad:
        print(f"  [WARN] skipped {bad} malformed line(s)")
    return events


def group_by_run(events, min_events=5):
    """Group events by trace_id; sort each run by event_id.

    Runs shorter than `min_events` are dropped (aborted generations).
    """
    runs = defaultdict(list)
    for ev in events:
        runs[ev[F_TRACE]].append(ev)

    kept, dropped = {}, []
    for tid, evs in runs.items():
        evs.sort(key=lambda e: e[F_EVENT])
        if len(evs) < min_events:
            dropped.append((tid, len(evs)))
        else:
            kept[tid] = evs
    if dropped:
        print(f"  [INFO] dropped {len(dropped)} degenerate run(s) "
              f"(<{min_events} events)")
    return kept


# ---------------------------------------------------- feature/label split
def split_features_labels(ev):
    """The leakage firewall. Features go to the model; labels never do."""
    feats = {k: ev.get(k) for k in EDGE_FEATURE_FIELDS}
    labels = {k: ev.get(k) for k in LABEL_FIELDS}
    return feats, labels


def _to_epoch(ts):
    """ISO-8601 -> float seconds. Temporal models need numeric, monotonic time."""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


# ------------------------------------------------------------- conversion
def run_to_graph(trace_id, events):
    """Convert one sorted run into the neutral intermediate representation.

    This IR is deliberately plain Python (not tensors) so it stays inspectable.
    The export layer turns it into model-ready tensors.
    """
    # nodes are derived from the distinct source/target values
    node_set = set()
    for ev in events:
        node_set.add(ev[F_SRC])
        node_set.add(ev[F_DST])
    nodes = sorted(node_set)
    node_index = {n: i for i, n in enumerate(nodes)}
    node_types = [node_type(n) for n in nodes]

    t0 = _to_epoch(events[0][F_TIME])
    temporal_events = []
    for ev in events:
        feats, labels = split_features_labels(ev)
        temporal_events.append({
            "event_id": ev[F_EVENT],
            "t": _to_epoch(ev[F_TIME]) - t0,     # seconds since run start
            "src": ev[F_SRC],
            "dst": ev[F_DST],
            "src_idx": node_index[ev[F_SRC]],
            "dst_idx": node_index[ev[F_DST]],
            "features": feats,                   # MODEL INPUT
            "labels": labels,                    # HELD OUT
        })

    injected = [e["event_id"] for e in temporal_events
                if e["labels"].get("lep_injected")]
    failures = [e["event_id"] for e in temporal_events
                if e["labels"].get("downstream_failure")]

    return {
        "trace_id": trace_id,
        "execution_id": events[0].get(F_EXEC),
        "nodes": nodes,
        "node_index": node_index,
        "node_types": node_types,
        "temporal_events": temporal_events,
        "n_events": len(temporal_events),
        # run-level labels (Stage 1 targets)
        "has_injection": bool(injected),
        "has_failure": bool(failures),
        "injection_events": injected,
        "failure_events": failures,
        # the quantity of interest, IF both exist
        "gap": (min(failures) - min(injected)
                if injected and failures and min(failures) > min(injected)
                else None),
    }


def convert_dir(trace_dir, min_events=5):
    """Load a directory of traces -> {trace_id: graph_ir}."""
    paths = sorted(glob.glob(os.path.join(trace_dir, "*.jsonl")))
    paths = [p for p in paths if "ipynb_checkpoints" not in p]
    print(f"  loading {len(paths)} trace file(s) from {trace_dir}")
    events = load_events(paths)
    runs = group_by_run(events, min_events=min_events)
    print(f"  grouped into {len(runs)} run(s)")
    graphs = {tid: run_to_graph(tid, evs) for tid, evs in runs.items()}
    return graphs, runs


# =========================================================================
# EXPORT -- IR -> model-ready tensors (static + temporal)
# =========================================================================
# ---------------------------------------------------------------- encoders
def _onehot(value, vocab):
    v = np.zeros(len(vocab), dtype=np.float32)
    if value in vocab:
        v[vocab.index(value)] = 1.0
    return v


class TextEncoder:
    """Encodes input/output summaries.

    Default is a cheap deterministic hashing encoder so the pipeline runs with
    zero heavy dependencies. For real experiments, swap in a fixed sentence
    embedder -- the model and dimension must be FROZEN across all runs, since
    every model in the ladder must see identical inputs.
    """

    def __init__(self, dim=32, mode="hash"):
        self.dim = dim
        self.mode = mode
        self._st = None
        if mode == "sentence":
            from sentence_transformers import SentenceTransformer  # noqa
            self._st = SentenceTransformer("all-MiniLM-L6-v2")
            self.dim = 384

    def encode(self, text):
        text = (text or "").strip()
        if self.mode == "sentence":
            return self._st.encode(text).astype(np.float32)
        # hashing fallback: stable bag-of-words projection
        v = np.zeros(self.dim, dtype=np.float32)
        for tok in text.lower().split():
            v[hash(tok) % self.dim] += 1.0
        n = np.linalg.norm(v)
        return v / n if n > 0 else v


# ------------------------------------------------------------ edge features
def encode_edge_features(graph, text_enc):
    """Per-event (edge) feature matrix. MODEL INPUTS ONLY -- no label fields."""
    rows = []
    for ev in graph["temporal_events"]:
        f = ev["features"]
        parts = [
            _onehot(f.get("event_type"), EVENT_TYPES),
            np.array([1.0 if f.get("tool_name") else 0.0,
                      1.0 if f.get("agent_id") else 0.0], dtype=np.float32),
            text_enc.encode(f.get("input_summary")),
            text_enc.encode(f.get("output_summary")),
        ]
        rows.append(np.concatenate(parts))
    return np.vstack(rows).astype(np.float32) if rows else np.zeros((0, 1), np.float32)


def encode_node_features(graph):
    """Per-entity feature matrix. Node identity/type only -- kept deliberately
    thin, because for TGN the interesting node state is LEARNED in memory."""
    return np.vstack([_onehot(t, NODE_TYPES) for t in graph["node_types"]]
                     ).astype(np.float32)


# -------------------------------------------------------------- label build
def build_labels(graph, stage="detect"):
    """Targets, kept strictly separate from features.

    stage='detect'  (Stage 1): per-event -- is THIS event an injected LEP?
                               plus run-level -- does this run contain one?
    stage='predict' (Stage 2): per-event -- will a downstream failure occur
                               LATER than this event? (strictly future-looking,
                               so a model may only use events before it.)
    """
    evs = graph["temporal_events"]
    if stage == "detect":
        y_edge = np.array([1 if e["labels"].get("lep_injected") else 0
                           for e in evs], dtype=np.int64)
        y_run = int(graph["has_injection"])
    elif stage == "predict":
        fails = graph["failure_events"]
        first_fail = min(fails) if fails else None
        y_edge = np.array(
            [1 if (first_fail is not None and e["event_id"] < first_fail) else 0
             for e in evs], dtype=np.int64)
        y_run = int(graph["has_failure"])
    else:
        raise ValueError(stage)
    return y_edge, y_run


# ------------------------------------------------------------- STATIC view
def to_static(graph, text_enc, stage="detect"):
    """PyG `Data`-compatible dict for the GAT / MPNN baseline.

    Time is DISCARDED here -- that is the point. The static model is the control
    for 'does temporal information help at all'.
    """
    evs = graph["temporal_events"]
    edge_index = np.array([[e["src_idx"] for e in evs],
                           [e["dst_idx"] for e in evs]], dtype=np.int64)
    y_edge, y_run = build_labels(graph, stage)
    return {
        "x": encode_node_features(graph),          # [num_nodes, d_node]
        "edge_index": edge_index,                  # [2, num_edges]
        "edge_attr": encode_edge_features(graph, text_enc),  # [num_edges, d_edge]
        "y_edge": y_edge,
        "y_run": y_run,
        "num_nodes": len(graph["nodes"]),
        "trace_id": graph["trace_id"],
    }


# ----------------------------------------------------------- TEMPORAL view
def to_temporal(graph, text_enc, stage="detect"):
    """DyGLib / PyG `TemporalData`-compatible dict for JODIE / TGN.

    Columns map 1:1 onto what DyGLib expects:
      src, dst, t, msg, labels  (edges consumed in timestamp order)
    """
    evs = graph["temporal_events"]
    y_edge, y_run = build_labels(graph, stage)
    return {
        "src": np.array([e["src_idx"] for e in evs], dtype=np.int64),
        "dst": np.array([e["dst_idx"] for e in evs], dtype=np.int64),
        "t": np.array([e["t"] for e in evs], dtype=np.float64),
        "msg": encode_edge_features(graph, text_enc),   # [num_events, d_edge]
        "y_edge": y_edge,
        "y_run": y_run,
        "num_nodes": len(graph["nodes"]),
        "trace_id": graph["trace_id"],
    }


# ---------------------------------------------------------------- dataset
def export_dataset(graphs, stage="detect", text_dim=32, text_mode="hash"):
    """Export every run into both views, using one shared encoder instance."""
    enc = TextEncoder(dim=text_dim, mode=text_mode)
    static, temporal = {}, {}
    for tid, g in graphs.items():
        static[tid] = to_static(g, enc, stage)
        temporal[tid] = to_temporal(g, enc, stage)
    return static, temporal


def save_npz(static, temporal, outdir):
    """Persist both views to disk (.npz per run)."""
    import os
    os.makedirs(os.path.join(outdir, "static"), exist_ok=True)
    os.makedirs(os.path.join(outdir, "temporal"), exist_ok=True)
    for tid, d in static.items():
        np.savez_compressed(os.path.join(outdir, "static", f"{tid}.npz"),
                            **{k: v for k, v in d.items() if k != "trace_id"})
    for tid, d in temporal.items():
        np.savez_compressed(os.path.join(outdir, "temporal", f"{tid}.npz"),
                            **{k: v for k, v in d.items() if k != "trace_id"})
    return len(static), len(temporal)

# =========================================================================
# CLI
# =========================================================================
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--traces", required=True, help="dir of *.jsonl traces")
    ap.add_argument("--out", default="out", help="output dir for .npz")
    ap.add_argument("--stage", default="detect", choices=["detect", "predict"],
                    help="detect = Stage 1 (is a LEP present); "
                         "predict = Stage 2 (will a failure occur later)")
    ap.add_argument("--text-mode", default="hash", choices=["hash", "sentence"])
    args = ap.parse_args()

    print("\n>>> CONVERT")
    graphs, runs = convert_dir(args.traces)

    print("\n>>> GRAPHS")
    for tid, g in sorted(graphs.items())[:6]:
        gap = f"gap={g['gap']}" if g["gap"] is not None else "gap=n/a"
        print(f"  {tid:<30} nodes={len(g['nodes']):>2} events={g['n_events']:>3} "
              f"inj={len(g['injection_events']):>2} "
              f"fail={len(g['failure_events'])} {gap}")
    if len(graphs) > 6:
        print(f"  ... {len(graphs) - 6} more")

    print("\n>>> EXPORT")
    static, temporal = export_dataset(graphs, stage=args.stage,
                                      text_mode=args.text_mode)
    tid = next(iter(static))
    s, t = static[tid], temporal[tid]
    print(f"  stage = '{args.stage}'")
    print(f"  STATIC   (PyG Data)       x={s['x'].shape}  "
          f"edge_index={s['edge_index'].shape}  edge_attr={s['edge_attr'].shape}")
    print(f"  TEMPORAL (DyGLib/TGN)     src={t['src'].shape}  dst={t['dst'].shape}  "
          f"t={t['t'].shape}  msg={t['msg'].shape}")
    pos = int(sum(d["y_edge"].sum() for d in static.values()))
    tot = int(sum(len(d["y_edge"]) for d in static.values()))
    print(f"  labels: {pos}/{tot} positive edges ({pos / max(tot, 1):.1%})")

    ns, nt = save_npz(static, temporal, args.out)
    print(f"  wrote {ns} static + {nt} temporal .npz -> {args.out}/\n")


if __name__ == "__main__":
    main()
