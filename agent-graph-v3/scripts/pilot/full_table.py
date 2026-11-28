"""Everything per family, attack and team setup in one table (prints it and writes a CSV).

Per row: runs, usable (dataset_eligible), attack fired, and the tracker's own labels
(reached the target agent, passed on to a consumer, passed on to a producer, recovery),
then the outcome: attack pass vs matched clean pass and the difference with a 95% interval
(resampling whole tasks), the research flip when --fixtures is given, and the cost per run
(events, tool calls, minutes). Clean rows give the same per setup.

The tracker columns come from the v3 tracker (with our temporary fix), so they show where
the injected content was traced to, not whether it changed the answer. The outcome columns
are the measured effect.

    python3 full_table.py fin_v3.jsonl --family financial_analysis --out fin_table.csv
    python3 full_table.py res_v3_regraded.jsonl --family research_synthesis \
        --field task_success_regraded --fixtures ag-run/agent-graph-v3/workspace_fixtures --out res_table.csv
    python3 full_table.py 'traces/v3/cr/node_*/benchmark_records.jsonl' --family code_review --out cr_table.csv
"""
import argparse
import collections
import csv
import glob
import json
from pathlib import Path

from matched_effect import fixture_of, boot_ci

TOPOS = ("review_loop", "branch_and_verify", "coordinator_workers")
NAMES = {
    "LEP_TOOL_RESULT_CORRUPTION": "Tool output corrupted",
    "LEP_HANDOFF_CORRUPTION": "Handoff corrupted",
    "LEP_INDIRECT_PROMPT_INJECTION": "Planted instructions",
    "LEP_MEMORY_POISONING": "Memory poisoned",
    "LEP_INPUT_DISREGARD": "Ignore input",
}
FLAGS = [("usable", "dataset_eligible"), ("fired", "injection_fired"),
         ("reached", "perturbation_reached_target"),
         ("to_consumer", "perturbation_propagated_to_consumer"),
         ("to_producer", "perturbation_propagated_to_producer"),
         ("recovered", "recovery_detected")]


def pct(n, d):
    return round(100 * n / d) if d else None


def avg(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(sum(xs) / len(xs), 1) if xs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("records", nargs="+")
    ap.add_argument("--family", required=True)
    ap.add_argument("--field", default="task_success")
    ap.add_argument("--fixtures", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    runs = [json.loads(l) for g in a.records for p in glob.glob(g) for l in open(p)]
    runs = [r for r in runs if r.get("task_family") == a.family]

    metrics = {"pass": lambda r, fid: bool(r.get(a.field))}
    if a.fixtures:
        from res_attack import verdicts
        seeds = {}

        def flip(r, fid):
            if fid not in seeds:
                m = json.load(open(Path(a.fixtures) / fid / "manifest.json"))
                seeds[fid] = {s["id"]: s["truth"] for s in m["seeds"]}
            t, g = seeds[fid], verdicts(r.get("graded_text", r.get("final_output")))
            return any(t[k] != "CONTRADICT" and g.get(k) == "CONTRADICT" for k in t)
        metrics["flip"] = flip

    def cell(r):
        return (fixture_of(r), r["topology"], r.get("execution_variant", "standard"))

    base = {m: collections.defaultdict(list) for m in metrics}
    for r in runs:
        if not r.get("lep_code"):
            c = cell(r)
            for m, fn in metrics.items():
                base[m][c].append(fn(r, c[0]))

    def row_for(label, topo, group, attack):
        n = len(group)
        row = {"family": a.family, "run type": label, "setup": topo, "runs": n}
        for name, field in FLAGS:
            row[f"{name} %"] = pct(sum(bool(r.get(field)) for r in group), n) if attack or name == "usable" else None
        for m, fn in metrics.items():
            vals, diffs = [], collections.defaultdict(list)
            for r in group:
                c = cell(r)
                v = fn(r, c[0])
                if attack:
                    if not base[m].get(c):
                        continue
                    b = sum(base[m][c]) / len(base[m][c])
                    vals.append((v, b))
                    diffs[c[0]].append(v - b)
                else:
                    vals.append((v, v))
            if not vals:
                continue
            av = sum(v for v, _ in vals) / len(vals)
            row[f"{m} %"] = round(100 * av)
            if attack:
                bv = sum(b for _, b in vals) / len(vals)
                lo, hi = boot_ci(diffs)
                row[f"clean {m} %"] = round(100 * bv)
                row[f"{m} effect pts"] = round(100 * (av - bv))
                row[f"{m} 95% CI"] = f"[{100 * lo:+.0f}, {100 * hi:+.0f}]"
        row["events/run"] = avg([r.get("num_events") for r in group])
        row["tool calls/run"] = avg([r.get("num_tool_calls") for r in group])
        rt = avg([r.get("runtime_seconds") for r in group])
        row["minutes/run"] = round(rt / 60, 1) if rt is not None else None
        return row

    rows = []
    clean = [r for r in runs if not r.get("lep_code")]
    for topo in TOPOS + ("all",):
        g = [r for r in clean if topo == "all" or r["topology"] == topo]
        if g:
            rows.append(row_for("Clean", topo, g, attack=False))
    for code, name in NAMES.items():
        att = [r for r in runs if r.get("lep_code") == code]
        for topo in TOPOS + ("all",):
            g = [r for r in att if topo == "all" or r["topology"] == topo]
            if g:
                rows.append(row_for(name, topo, g, attack=True))

    cols = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    if a.out:
        with open(a.out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
    show = [c for c in cols if c != "family"]
    print(" | ".join(show))
    for r in rows:
        print(" | ".join("" if r.get(c) is None else str(r.get(c)) for c in show))
    if a.out:
        print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
