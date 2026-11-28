#!/usr/bin/env python3.11
"""Plotting-data export for the three AgentProp figure panels, computed
exclusively from the FROZEN V4.4 per-execution ledger
(out_v44/final/per_execution_v44.jsonl) with the FROZEN fixture-cluster
bootstrap (m44.fixture_cluster_ci: N_FIX_BOOT=4000, SEED_FIX_BOOT=20270301).

Panels:
  1. task_family: financial_analysis / research_synthesis / code_review
     (all propagation modes, all topologies).
  2. topology: review_loop / branch_and_verify / coordinator_workers,
     RESTRICTED to single_origin executions.
  3. propagation_mode: single_origin / one_to_many / many_to_one
     (aggregated across all compatible topologies and task families).

Per category: total comparable executions; M1/M2/M3 positive counts;
incidence percentages over the comparable population; fixture-cluster
bootstrap 95% limits (lower, upper) for each metric. Read-only: the frozen
evaluator, thresholds, and all frozen artifacts are untouched.
"""
import json
import sys
from collections import OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import m44  # frozen bootstrap constants + fixture_cluster_ci

LEDGER = HERE / "out_v44" / "final" / "per_execution_v44.jsonl"
OUT = HERE / "out_v44" / "final" / "plot_data_v44_breakdowns.json"

rows = [json.loads(l) for l in open(LEDGER)]
comparable = [r for r in rows if r["comparable"]]
assert len(comparable) == 16767, len(comparable)
# sanity: headline counts match the frozen aggregates
assert sum(1 for r in comparable if r["M1_selected"]["positive"]) == 5984
assert sum(1 for r in comparable if r["M2_selected"]["positive"]) == 4123
assert sum(1 for r in comparable if r["M3_selected"]["matched_degraded_observed"]) == 1992

METRICS = OrderedDict([
    ("M1", lambda r: int(r["M1_selected"]["positive"])),
    ("M2", lambda r: int(r["M2_selected"]["positive"])),
    ("M3", lambda r: int(r["M3_selected"]["matched_degraded_observed"])),
])

# (key, label) pairs per panel — labels are the chart-facing names.
PANELS = OrderedDict([
    ("task_family", [
        ("financial_analysis", "Financial analysis"),
        ("research_synthesis", "Research synthesis"),
        ("code_review", "Code review"),
    ]),
    ("topology_single_origin", [
        ("review_loop", "Review loop"),
        ("branch_and_verify", "Branch and verify"),
        ("coordinator_workers", "Coordinator workers"),
    ]),
    ("propagation_mode", [
        ("single_origin", "Single origin"),
        ("one_to_many", "One-to-many"),
        ("many_to_one", "Many-to-one"),
    ]),
])


def category_out(panel, key, label):
    if panel == "topology_single_origin":
        sel = [r for r in comparable if r["topology"] == key and r["propagation_mode"] == "single_origin"]
    elif panel == "task_family":
        sel = [r for r in comparable if r["task_family"] == key]
    else:
        sel = [r for r in comparable if r["propagation_mode"] == key]

    n = len(sel)
    fix = {}
    per_metric = {}
    for mk, fn in METRICS.items():
        vals = [fn(r) for r in sel]
        k = sum(vals)
        fix.setdefault(mk, {})
        for r, v in zip(sel, vals):
            fix[mk].setdefault(r["fixture_id"], []).append(v)
        ci = m44.fixture_cluster_ci(fix[mk])  # [lo, hi, point]
        rate = k / n * 100.0
        per_metric[mk] = OrderedDict([
            ("positives", k),
            ("incidence_percent", round(rate, 4)),
            ("bootstrap_ci95_percent", [round(ci[0] * 100.0, 4), round(ci[1] * 100.0, 4)]),
            ("n_fixtures", len(fix[mk])),
        ])
    return OrderedDict([
        ("key", key),
        ("label", label),
        ("comparable_executions", n),
        ("metrics", per_metric),
    ])


result = OrderedDict()
for panel, cats in PANELS.items():
    result[panel] = OrderedDict([
        ("restriction",
         "single_origin executions only" if panel == "topology_single_origin"
         else "all propagation modes, all topologies" if panel == "task_family"
         else "aggregated across compatible topologies and task families"),
        ("categories", [category_out(panel, k, lab) for k, lab in cats]),
    ])

result["method"] = OrderedDict([
    ("source_ledger", "out_v44/final/per_execution_v44.jsonl (frozen V4.4, 16,767 comparable executions, 903 fixtures)"),
    ("bootstrap", "m44.fixture_cluster_ci: fixture-cluster bootstrap, N_FIX_BOOT=4000 resamples, SEED_FIX_BOOT=20270301, percentile CI (2.5th/97.5th over the 4000 resampled rates)"),
    ("metrics", "M1=M1-C whole-trace divergence; M2=M2-O4 downstream divergence; M3=frozen five-way observed matched degradation (matched_degraded_observed). Incidences over the comparable population per category."),
    ("frozen_evaluator_modified", False),
])

OUT.write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
