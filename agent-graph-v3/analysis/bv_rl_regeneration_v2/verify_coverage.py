"""Post-run coverage and admission verification for the BV+RL regeneration.

Usage (from agent-graph-v3/):
  python3 analysis/bv_rl_regeneration_v2/verify_coverage.py \
      --records production_run/bv_rl_regeneration_v2/pilot/benchmark_records.jsonl \
      [--records production_run/bv_rl_regeneration_v2/node_*/benchmark_records.jsonl] \
      [--output analysis/bv_rl_regeneration_v2/post_run_report.json]

Reports, per cell and overall:
  - completed / admitted / rejected / backend-failed counts, kept separate
  - admission breakdown by rejection reason (injection_count_mismatch,
    injection_structure_mismatch, admission_failed, protocol_violation,
    max_events_reached, max_review_cycles, ...)
  - injection origin counts (expected/actual, from the record's trace metadata)
  - Review Loop termination reasons (incl. review_cap / review_finalization_reason)
  - runtime distribution (min/median/p90/p95/max) per topology
  - benign/perturbed comparability: benign rows must be 0-origin and clean;
    flagged if any benign row is rejected for structure or count reasons
  - pilot-only vs canonical-population partition: scenarios present in the
    records but ABSENT from the 1,064-execution canonical regeneration inventory
    are "pilot-only" (matched benign controls not re-run by the full batch);
    they are reported separately, never double-counted against the canonical set.
  - systematic-rejection check: any (topology, family, LEP, mode) cell with
    >70% rejections at the perturbed arm (>=3 records) is flagged.

Consolidation rule: canonical identity = scenario_id. When pilot and full-batch
records are passed together, the LATEST record per scenario_id counts (the full
batch runs after the pilot). Writes post_run_report.json.
"""
import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "analysis" / "bv_rl_regeneration_v2"
DEFAULT_INVENTORY = OUT / "regeneration_inventory.csv"
DEFAULT_OUTPUT = OUT / "post_run_report.json"
BACKEND_PREFIXES = ("backend_",)


def _load_inventory(path: Path) -> dict:
    with open(path) as f:
        return {r["scenario_id"]: r for r in csv.DictReader(f)}


def _dedupe(records: list) -> tuple:
    """Keep the LAST record per scenario_id (full batch runs after the pilot)."""
    rows, order = {}, []
    for r in records:
        sid = r.get("scenario_id") or f"__no_id_{len(order)}"
        if sid not in rows:
            order.append(sid)
        rows[sid] = r
    deduped = len(records) - len(order)
    return [rows[s] for s in order], deduped


def _join_trace_meta(r: dict) -> dict:
    """Attach expected/actual injection origins from the record's trace metadata.

    benchmark_records.jsonl does NOT carry origin counts; they live on the
    trace. Falls back to None when the trace is absent (e.g. backend failure
    before any trace is written).
    """
    out = dict(r)
    tp = r.get("trace_path")
    if tp:
        p = Path(tp) if Path(tp).is_absolute() else ROOT / tp
        if p.exists():
            try:
                md = json.loads(p.read_text()).get("metadata", {})
                out["expected_injection_origins"] = md.get("expected_injection_origins")
                out["actual_injection_origins"] = md.get("actual_injection_origins")
                out["origin_validation"] = md.get("origin_validation")
                out["review_finalization_reason"] = md.get("review_finalization_reason")
                out["review_cycle_count"] = md.get("review_cycle_count")
                out["traces_matched"] = True
            except (OSError, json.JSONDecodeError):
                out["traces_matched"] = False
        else:
            out["traces_matched"] = False
    else:
        out["traces_matched"] = False
    return out


def classify(r: dict) -> str:
    term = str(r.get("termination_reason", ""))
    if term.startswith(BACKEND_PREFIXES):
        return "backend_failed"
    if r.get("dataset_eligible") is True:
        return "admitted"
    return "rejected"


def _runtime_stats(values: list) -> dict:
    if not values:
        return {"n": 0}
    s = sorted(values)

    def pct(q):
        return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]

    return {
        "n": len(s), "min": s[0], "median": s[len(s) // 2],
        "p90": pct(0.9), "p95": pct(0.95), "max": s[-1],
    }


def run(records_paths: list, inventory: dict, output: Path) -> dict:
    """Run the verification and write ``output``; returns the report dict."""
    raw = []
    for p in records_paths:
        for line in open(p):
            line = line.strip()
            if line:
                raw.append(json.loads(line))

    rows, deduped = _dedupe(raw)
    rows = [_join_trace_meta(r) for r in rows]

    inv_ids = set(inventory)
    in_population = [r for r in rows if r.get("scenario_id") in inv_ids]
    pilot_only = [r for r in rows if r.get("scenario_id") not in inv_ids]

    rep = {
        "total_records": len(raw),
        "unique_scenarios": len(rows),
        "deduplicated_records": deduped,
        "in_canonical_population": len(in_population),
        "pilot_only": len(pilot_only),
        "pilot_only_scenarios": sorted(r.get("scenario_id") for r in pilot_only),
        "status_counts": dict(Counter(classify(r) for r in rows)),
        "termination_counts": dict(Counter(str(r.get("termination_reason")) for r in rows)),
        "rejected_by_reason": dict(Counter(
            r.get("termination_reason") for r in rows if classify(r) == "rejected")),
        "by_cell": {},
        "flagged_cells": [],
        "benign_anomalies": [],
        "m2o_gate": {},
        "review_loop": {},
        "runtime_by_topology": {},
        "systematic_rejection_check": "pass",
        "traces_unmatched": [],
    }

    # ── per-cell aggregation (the previously-crashing unpack is fixed) ─────
    cells = defaultdict(list)
    for r in rows:
        cells[(r.get("topology"), r.get("task_family"),
               r.get("lep_code") or "BENIGN", r.get("propagation_mode"))].append(r)

    for key in sorted(cells):
        rs = cells[key]
        status = Counter(classify(r) for r in rs)
        perturbed = [r for r in rs if r.get("condition") != "benign"]
        base = perturbed if perturbed else rs
        rep["by_cell"][str(key)] = {
            "total": len(rs),
            "admitted": status["admitted"],
            "rejected": status["rejected"],
            "backend_failed": status["backend_failed"],
            "expected_origins": dict(Counter(str(r.get("expected_injection_origins")) for r in base)),
            "actual_origins": dict(Counter(str(r.get("actual_injection_origins")) for r in base)),
        }
        if perturbed:
            rej = status["rejected"] + status["backend_failed"]
            if len(perturbed) >= 3 and rej / len(perturbed) > 0.7:
                rep["flagged_cells"].append(
                    {"cell": str(key), "rejected_frac": round(rej / len(perturbed), 3),
                     "reasons": dict(Counter(
                         r.get("termination_reason") for r in perturbed
                         if classify(r) != "admitted"))})

    # ── M2O admission gate: admitted => actual == expected (== 2), complete ──
    m2o = [r for r in rows if r.get("propagation_mode") == "many_to_one"]
    admitted_m2o = [r for r in m2o if classify(r) == "admitted"]
    incomplete_m2o = [r for r in m2o
                      if classify(r) == "rejected"
                      and r.get("termination_reason") == "injection_count_mismatch"]
    rep["m2o_gate"] = {
        "total_m2o": len(m2o),
        "admitted_all_expected_met": all(
            (r.get("expected_injection_origins") is not None
             and r.get("actual_injection_origins") == r.get("expected_injection_origins"))
            for r in admitted_m2o),
        "admitted_count": len(admitted_m2o),
        "rejected_incomplete_count": len(incomplete_m2o),
        "incomplete_never_admitted": all(
            classify(r) != "admitted" for r in incomplete_m2o),
    }

    # ── Review Loop termination review ──────────────────────────────────────
    rl = [r for r in rows if r.get("topology") == "review_loop"]
    rep["review_loop"] = {
        "total": len(rl),
        "termination": dict(Counter(str(r.get("termination_reason")) for r in rl)),
        "admitted": sum(1 for r in rl if classify(r) == "admitted"),
        "max_events_reached": sum(1 for r in rl
                                  if r.get("termination_reason") == "max_events_reached"),
        "review_finalization_reasons": dict(Counter(
            str(r.get("review_finalization_reason")) for r in rl
            if r.get("review_finalization_reason") is not None)),
        "protocol_violations": [r.get("scenario_id") for r in rl
                                if r.get("termination_reason") == "protocol_violation"],
    }

    # ── runtime distribution per topology ───────────────────────────────────
    for topo in ("branch_and_verify", "review_loop"):
        vals = [r["runtime_seconds"] for r in rows
                if r.get("topology") == topo and isinstance(r.get("runtime_seconds"), (int, float))]
        rep["runtime_by_topology"][topo] = _runtime_stats(vals)

    # ── benign/perturbed comparability anomalies ───────────────────────────
    for r in rows:
        if r.get("condition") == "benign" and classify(r) == "rejected":
            rep["benign_anomalies"].append(
                {"scenario_id": r.get("scenario_id"),
                 "termination_reason": r.get("termination_reason"),
                 "in_canonical_population": r.get("scenario_id") in inv_ids,
                 "note": "benign arm rejected — check model/scheduler, not injection"})

    # ── unmatched traces ────────────────────────────────────────────────────
    rep["traces_unmatched"] = [r.get("scenario_id") for r in rows if not r.get("traces_matched")]

    # ── pilot-only partition: must all be benign matched controls ──────────
    rep["pilot_only_all_benign"] = all(
        r.get("condition") == "benign" and (r.get("expected_injection_origins") in (0, None))
        for r in pilot_only)
    rep["systematic_rejection_check"] = (
        "SYSTEMATIC REJECTION SUSPECTED" if rep["flagged_cells"] else "pass")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rep, indent=1, default=str))
    return rep


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", nargs="+", required=True)
    ap.add_argument("--output", default=str(DEFAULT_OUTPUT))
    ap.add_argument("--inventory", default=str(DEFAULT_INVENTORY),
                    help="canonical population CSV used to partition pilot-only vs canonical")
    args = ap.parse_args(argv)

    inventory = _load_inventory(Path(args.inventory))
    rep = run(args.records, inventory, Path(args.output))

    print(json.dumps({k: rep[k] for k in
                      ("total_records", "unique_scenarios", "deduplicated_records",
                       "in_canonical_population", "pilot_only",
                       "status_counts", "termination_counts",
                       "systematic_rejection_check")}, indent=1))
    if rep["flagged_cells"]:
        print("flagged cells:")
        for c in rep["flagged_cells"]:
            print("  ", c)
    if rep["benign_anomalies"]:
        print("benign anomalies:", len(rep["benign_anomalies"]))
    print("report ->", args.output)
    sys.exit(0)


if __name__ == "__main__":
    main()
