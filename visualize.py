"""AgentGraphs NetworkX visualisation.

    python visualize.py --traces traces --trace 02e9040b30b --out figs/
    python visualize.py --traces traces --all --out figs/

Renders the converted entity-node graph: entities are nodes, events are edges.
Injected events and failures are highlighted so a run can be inspected by eye.
"""

import argparse
import glob
import json
import os
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx


NODE_STYLE = {
    "agent":    ("#4C72B0", 2200),
    "tool":     ("#55A868", 1500),
    "user":     ("#8172B2", 1200),
    "system":   ("#937860", 1200),
    "internal": ("#BBBBBB", 1000),
    "other":    ("#CCCCCC", 1000),
}


def node_type(n):
    n = str(n)
    if n.startswith("agent_"):
        return "agent"
    if n.startswith("mcp_") or n.startswith("tool_"):
        return "tool"
    if n == "user":
        return "user"
    if n in ("system", "multi_agent_system"):
        return "system"
    if n == "internal":
        return "internal"
    return "other"


def load_run(trace_dir, trace_id):
    for p in sorted(glob.glob(os.path.join(trace_dir, "*.jsonl"))):
        with open(p) as f:
            first = f.readline()
        if not first.strip():
            continue
        if json.loads(first).get("trace_id") == trace_id:
            evs = [json.loads(l) for l in open(p) if l.strip()]
            return sorted(evs, key=lambda e: e["event_id"])
    raise SystemExit(f"trace {trace_id} not found in {trace_dir}")


def list_runs(trace_dir):
    out = []
    for p in sorted(glob.glob(os.path.join(trace_dir, "*.jsonl"))):
        with open(p) as f:
            first = f.readline()
        if first.strip():
            out.append(json.loads(first)["trace_id"])
    return out


def build_graph(events):
    """Entities as nodes, events as edges. Parallel events are aggregated."""
    G = nx.DiGraph()
    for e in events:
        for n in (e["source"], e["target"]):
            if n not in G:
                G.add_node(n, ntype=node_type(n))
    agg = defaultdict(lambda: {"n": 0, "inj": 0, "types": set(), "eids": []})
    for e in events:
        k = (e["source"], e["target"])
        a = agg[k]
        a["n"] += 1
        a["types"].add(e["event_type"])
        a["eids"].append(e["event_id"])
        if e.get("lep_injected"):
            a["inj"] += 1
    for (s, t), a in agg.items():
        G.add_edge(s, t, count=a["n"], injected=a["inj"],
                   types=sorted(a["types"]), eids=a["eids"])
    return G


def draw_structure(G, events, title, path):
    """Aggregated entity graph; edge colour = share of injected events."""
    fig, ax = plt.subplots(figsize=(11, 8))
    pos = nx.spring_layout(G, seed=7, k=1.5)

    for nt, (colour, size) in NODE_STYLE.items():
        ns = [n for n in G if G.nodes[n]["ntype"] == nt]
        if ns:
            nx.draw_networkx_nodes(G, pos, nodelist=ns, node_color=colour,
                                   node_size=size, ax=ax, edgecolors="white",
                                   linewidths=1.5)
    nx.draw_networkx_labels(G, pos, font_size=8, font_color="white",
                            font_weight="bold", ax=ax)

    for u, v, d in G.edges(data=True):
        frac = d["injected"] / d["count"] if d["count"] else 0
        colour = "#C44E52" if frac > 0 else "#999999"
        nx.draw_networkx_edges(
            G, pos, edgelist=[(u, v)], ax=ax,
            width=1 + 3 * (d["count"] / max(e[2]["count"]
                                            for e in G.edges(data=True))),
            edge_color=colour, alpha=0.35 + 0.65 * frac,
            connectionstyle="arc3,rad=0.12", arrowsize=14)

    labels = {(u, v): (f"{d['count']}" if not d["injected"]
                       else f"{d['count']} ({d['injected']} inj)")
              for u, v, d in G.edges(data=True)}
    nx.draw_networkx_edge_labels(G, pos, edge_labels=labels, font_size=7,
                                 ax=ax, label_pos=0.35)

    n_inj = sum(1 for e in events if e.get("lep_injected"))
    fails = [e["event_id"] for e in events if e.get("downstream_failure")]
    ax.set_title(f"{title}\n{len(events)} events · {n_inj} injected · "
                 f"failure at {fails[0] if fails else 'none'}", fontsize=11)
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(path, dpi=140, bbox_inches="tight")
    plt.close(fig)


def draw_timeline(events, title, path):
    """Event sequence over time; injections and failure marked."""
    entities = sorted({e["source"] for e in events} | {e["target"]
                                                       for e in events})
    row = {n: i for i, n in enumerate(entities)}
    fig, ax = plt.subplots(figsize=(14, 5))

    for e in events:
        x = e["event_id"]
        y0, y1 = row[e["source"]], row[e["target"]]
        inj = bool(e.get("lep_injected"))
        ax.plot([x, x], [y0, y1], color="#C44E52" if inj else "#BBBBBB",
                lw=1.6 if inj else 0.6, alpha=0.9 if inj else 0.4, zorder=2)
        ax.scatter([x], [y1], s=14 if inj else 5,
                   color="#C44E52" if inj else "#888888", zorder=3)

    for e in events:
        if e.get("downstream_failure"):
            ax.axvline(e["event_id"], color="#8C1D18", ls="--", lw=1.5,
                       zorder=1)
            ax.text(e["event_id"], len(entities) - 0.5,
                    f" failure\n {e.get('failure_type')}", fontsize=8,
                    color="#8C1D18", va="top")

    ax.set_yticks(range(len(entities)))
    ax.set_yticklabels(entities, fontsize=8)
    ax.set_xlabel("event_id")
    ax.set_title(f"{title} — event timeline (red = injected)", fontsize=11)
    ax.grid(axis="x", alpha=0.15)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=140, bbox_inches="tight")
    plt.close(fig)


def draw_pair(trace_dir, exec_id, out_dir):
    """Benign vs malignant of the same execution, side by side."""
    a, b = load_run(trace_dir, exec_id + "a"), load_run(trace_dir, exec_id + "b")
    fig, axes = plt.subplots(1, 2, figsize=(18, 7))
    for ax, evs, tag in ((axes[0], a, "benign"), (axes[1], b, "malignant")):
        G = build_graph(evs)
        pos = nx.spring_layout(G, seed=7, k=1.5)
        for nt, (colour, size) in NODE_STYLE.items():
            ns = [n for n in G if G.nodes[n]["ntype"] == nt]
            if ns:
                nx.draw_networkx_nodes(G, pos, nodelist=ns, node_color=colour,
                                       node_size=size * 0.7, ax=ax,
                                       edgecolors="white", linewidths=1.2)
        nx.draw_networkx_labels(G, pos, font_size=7, font_color="white",
                                font_weight="bold", ax=ax)
        for u, v, d in G.edges(data=True):
            frac = d["injected"] / d["count"] if d["count"] else 0
            nx.draw_networkx_edges(G, pos, edgelist=[(u, v)], ax=ax, width=1.4,
                                   edge_color="#C44E52" if frac else "#999999",
                                   alpha=0.35 + 0.65 * frac,
                                   connectionstyle="arc3,rad=0.12",
                                   arrowsize=11)
        n_inj = sum(1 for e in evs if e.get("lep_injected"))
        ax.set_title(f"{exec_id}{'a' if tag == 'benign' else 'b'} — {tag} "
                     f"({n_inj} injected)", fontsize=10)
        ax.axis("off")
    fig.suptitle(f"execution {exec_id}: benign vs malignant", fontsize=12)
    fig.tight_layout()
    p = os.path.join(out_dir, f"pair_{exec_id}.png")
    fig.savefig(p, dpi=140, bbox_inches="tight")
    plt.close(fig)
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--traces", default="traces")
    ap.add_argument("--trace", help="single trace_id")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--pairs", action="store_true",
                    help="benign vs malignant per execution")
    ap.add_argument("--out", default="figs")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    ids = ([args.trace] if args.trace
           else list_runs(args.traces) if args.all else [])
    made = []
    for tid in ids:
        evs = load_run(args.traces, tid)
        G = build_graph(evs)
        p1 = os.path.join(args.out, f"{tid}_structure.png")
        p2 = os.path.join(args.out, f"{tid}_timeline.png")
        draw_structure(G, evs, tid, p1)
        draw_timeline(evs, tid, p2)
        made += [p1, p2]
        print(f"  {tid}: {G.number_of_nodes()} nodes, "
              f"{G.number_of_edges()} distinct edges, {len(evs)} events")

    if args.pairs:
        execs = sorted({t[:-1] for t in list_runs(args.traces)})
        for ex in execs:
            made.append(draw_pair(args.traces, ex, args.out))
            print(f"  pair {ex}")

    print(f"\nwrote {len(made)} figure(s) -> {args.out}/")


if __name__ == "__main__":
    main()
