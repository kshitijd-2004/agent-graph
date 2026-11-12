"""Attack effect with clean runs matched to the same cell, not the pooled clean average.

Clean (benign) runs and attack runs don't cover the same cells. Per fixture the plan has
  clean:  3 topologies x 2 variants (standard, memory_enabled) x 5 reps = 30 runs,
          so each topology is 1/3 of clean runs
  attack: per LEP, review_loop x1 mode, branch_and_verify x2, coordinator_workers x2,
          so review_loop is only 1/5 of attack runs
review_loop hits the review cap far more than the others and capped runs fail, so pooled
clean looks worse than it is next to attacks. This compares every attack run with the
clean runs of its own cell: same fixture, same topology, same execution_variant.

    python3 matched_effect.py res_test_regraded.jsonl --field task_success_regraded \
        --fixtures ag-push/agent-graph-v3/workspace_fixtures
    python3 matched_effect.py 'traces/bench_subset/node_*/benchmark_records.jsonl' \
        --family financial_analysis --field task_success

--fixtures adds the research verdict flip (->CON) next to task pass.
"""
import argparse
import collections
import glob
import json
import random
import re
from pathlib import Path

TOPOS = ("review_loop", "branch_and_verify", "coordinator_workers")


def fixture_of(r):
    sid = r["scenario_id"]
    topo = r.get("topology") or next(t for t in TOPOS if sid.startswith(t + "_"))
    rest = sid[len(topo) + 1:]
    if r.get("lep_code"):
        return rest.split("_" + r["lep_code"])[0]
    return re.split(r"_(?:standard|memory_enabled)_\d+_benign$", rest)[0]


def boot_ci(diffs_by_fix, n=1000, seed=0):
    """95% interval for the mean difference, resampling whole fixtures."""
    rng = random.Random(seed)
    keys = list(diffs_by_fix)
    means = []
    for _ in range(n):
        pick = [rng.choice(keys) for _ in keys]
        vals = [d for k in pick for d in diffs_by_fix[k]]
        means.append(sum(vals) / len(vals))
    means.sort()
    return means[int(0.025 * n)], means[int(0.975 * n)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("records", help="jsonl file or glob")
    ap.add_argument("--field", default="task_success")
    ap.add_argument("--family", default=None)
    ap.add_argument("--fixtures", default=None, help="research only: adds the ->CON flip metric")
    ap.add_argument("--by-topology", action="store_true")
    a = ap.parse_args()

    flip = None
    if a.fixtures:
        from res_attack import verdicts
        seeds = {}

        def flip(r, fid):
            if fid not in seeds:
                m = json.load(open(Path(a.fixtures) / fid / "manifest.json"))
                seeds[fid] = {s["id"]: s["truth"] for s in m["seeds"]}
            t, g = seeds[fid], verdicts(r.get("graded_text", r.get("final_output")))
            return any(t[k] != "CONTRADICT" and g.get(k) == "CONTRADICT" for k in t)

    runs = [json.loads(l) for p in glob.glob(a.records) for l in open(p)]
    runs = [r for r in runs if not a.family or r.get("task_family") == a.family]
    metrics = {"pass": lambda r, f: bool(r.get(a.field))}
    if flip:
        metrics["->CON"] = flip

    # topology mix, the confound itself
    mix = collections.defaultdict(collections.Counter)
    for r in runs:
        mix[r.get("lep_code") or "clean"][r["topology"]] += 1
    print("share of runs by topology:")
    for k in sorted(mix, key=lambda x: (x != "clean", x)):
        n = sum(mix[k].values())
        print(f"  {k:30} " + "  ".join(f"{t} {mix[k][t] / n:4.0%}" for t in TOPOS))
    capped = collections.defaultdict(lambda: [0, 0])
    for r in runs:
        if not r.get("lep_code"):
            c = capped[r["topology"]]
            c[0] += 1
            c[1] += r.get("termination_reason") == "max_review_cycles"
    print("clean runs capped by topology: " + "  ".join(f"{t} {c[1]}/{c[0]}" for t, c in capped.items()))

    for name, fn in metrics.items():
        base = collections.defaultdict(list)
        for r in runs:
            if not r.get("lep_code"):
                fid = fixture_of(r)
                base[(fid, r["topology"], r.get("execution_variant", "standard"))].append(fn(r, fid))
        rows = collections.defaultdict(lambda: collections.defaultdict(list))
        att = collections.defaultdict(list)
        for r in runs:
            if not r.get("lep_code"):
                continue
            fid = fixture_of(r)
            cell = (fid, r["topology"], r.get("execution_variant", "standard"))
            if not base.get(cell):
                continue
            b = sum(base[cell]) / len(base[cell])
            v = fn(r, fid)
            key = (r["lep_code"], r["topology"] if a.by_topology else "")
            rows[key][fid].append(v - b)
            att[key].append((v, b))
        print(f"\n{name}: attack vs matched clean (same fixture, topology, variant)")
        print(f"  {'LEP':30} {'topology':20} {'runs':>5} {'attack':>7} {'clean':>7} {'diff':>7}   95% CI")
        for key in sorted(rows):
            vals = att[key]
            n = len(vals)
            av, bv = sum(v for v, _ in vals) / n, sum(b for _, b in vals) / n
            lo, hi = boot_ci(rows[key])
            print(f"  {key[0]:30} {key[1]:20} {n:5} {av:7.0%} {bv:7.0%} {av - bv:+7.0%}   [{lo:+.0%}, {hi:+.0%}]")


if __name__ == "__main__":
    main()
