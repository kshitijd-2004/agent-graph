"""Build the Branch & Verify + Review Loop regeneration package (v2 scope).

Sources (all persistent, frozen):
  - planning_audit_oct19/canonical_plan.json      (intended standard matrix)
  - analysis/full_production_consolidated/source/traces|rejected_traces (history)
  - analysis/injection_mismatch_audit/semantic_impact.json (structure-invalid audit)

Scope (per the GO audit):
  P1: benign reference shortfall (<3 valid accepted benign reps per fixture+topology),
      only the missing repetitions, deduplicated with any other need for the same rep.
  P2: attacked (single_lep) standard executions with no valid accepted execution,
      across the 4 retained LEPs, both propagation modes per topology.
  P3: accepted traces structurally invalid under the corrected origin-identity
      contract (financial IPI single-origin, BV+RL only; 183 rows).

Excluded permanently: LEP_MEMORY_POISONING, all memory_enabled variants.
Frozen v1 manifest and all frozen production/BND artifacts are untouched; this
writes only under analysis/bv_rl_regeneration_v2/.

No execution, no submission, no inference.
"""
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CANON = ROOT / "planning_audit_oct19" / "canonical_plan.json"
CONS = ROOT / "analysis" / "full_production_consolidated" / "source"
IMPACT = ROOT / "analysis" / "injection_mismatch_audit" / "semantic_impact.json"
WS = ROOT / "workspace_fixtures"
OUT = ROOT / "analysis" / "bv_rl_regeneration_v2"

RETAINED = ("LEP_TOOL_RESULT_CORRUPTION", "LEP_INDIRECT_PROMPT_INJECTION",
            "LEP_HANDOFF_CORRUPTION", "LEP_INPUT_DISREGARD")
TOPOS = ("branch_and_verify", "review_loop")
# The loader accepts exactly these fields per scenario entry (benchmark/plan_manifest.py).
# Category/status annotations live in regeneration_inventory.csv only.
ENTRY_FIELDS = ("run_id", "scenario_id", "task_family", "fixture_id", "task_variant",
                "condition", "execution_variant", "lep_codes", "topology",
                "propagation_mode", "lep_code", "repetition_index", "pair_tag", "is_baseline")


def entry_for_manifest(e: dict) -> dict:
    out = {k: e[k] for k in ENTRY_FIELDS if k in e}
    for k in ("lep_code", "execution_variant"):
        out.setdefault(k, "" if k == "lep_code" else "standard")
    out.setdefault("pair_tag", e.get("pair_tag") or f"b_{e['scenario_id']}")
    out.setdefault("run_id", "run-" + e["scenario_id"][-6:])
    out.setdefault("is_baseline", e.get("is_baseline", False))
    out.setdefault("task_variant", e.get("task_variant", ""))
    out.setdefault("lep_codes", [])
    assert set(out) == set(ENTRY_FIELDS), f"field drift: {set(out) ^ set(ENTRY_FIELDS)}"
    return out

# Historical per-scenario runtime estimates (p95, seconds), from the 160-shard
# production benchmark_records.jsonl census. vLLM startup adds ~600-900 s/shard.
COST_P95_S = {"review_loop": 59, "branch_and_verify": 37}
VLLM_STARTUP_S = 900
SHARD_WALL_BUDGET_S = 2 * 3600  # <= 2 h per shard target


def is_mem_entry(e: dict) -> bool:
    codes = e.get("lep_codes") or ([e["lep_code"]] if e.get("lep_code") else [])
    return "LEP_MEMORY_POISONING" in codes or e.get("execution_variant") == "memory_enabled"


def scenario_terminations(consolidated: dict, scenario_id: str) -> dict:
    """Valid-accepted and rejected termination census for one scenario_id."""
    accepted = []
    for p in consolidated.get(scenario_id, []):
        kind, trace = p
        md = trace.get("metadata", {})
        if kind == "accepted" and md.get("termination_reason") == "completed" \
                and md.get("dataset_eligible") is True:
            accepted.append(md)
    rejected_terms = Counter()
    for p in consolidated.get(scenario_id, []):
        kind, trace = p
        if kind == "rejected":
            rejected_terms[trace.get("metadata", {}).get("termination_reason")] += 1
    return {"valid_accepted": accepted, "rejected_terms": rejected_terms}


def load_consolidated() -> dict:
    """Map scenario_id -> [(kind, trace dict)] from the frozen consolidated store."""
    mapping: dict[str, list] = {}
    for kind, sub in (("accepted", "traces"), ("rejected", "rejected_traces")):
        for p in (CONS / sub).glob("*_trace.json"):
            trace = json.loads(p.read_text())
            mapping.setdefault(trace["metadata"]["scenario_id"], []).append((kind, trace))
    return mapping


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    plan = json.loads(CANON.read_text())
    by_id = {e["scenario_id"]: e for e in plan}
    impact = json.loads(IMPACT.read_text())
    consolidated = load_consolidated()

    scope = [e for e in plan
             if e["topology"] in TOPOS
             and e.get("execution_variant", "standard") == "standard"
             and not is_mem_entry(e)]
    for e in scope:
        codes = e.get("lep_codes") or []
        scalar = e.get("lep_code")
        assert sorted(codes) == ([scalar] if scalar else []), f"inconsistent LEP fields: {e['scenario_id']}"

    entries: dict[str, dict] = {}

    # ── P1: missing benign repetitions (dedup across the 3 benign reps) ─────
    p1_by_fixture: dict[tuple, list[dict]] = {}
    for e in scope:
        if e["condition"] == "benign":
            p1_by_fixture.setdefault((e["topology"], e["fixture_id"]), []).append(e)
    p1_rows = []
    for (topo, fx), reps in sorted(p1_by_fixture.items()):
        reps.sort(key=lambda e: e["repetition_index"])
        status = []
        for e in reps:
            info = scenario_terminations(consolidated, e["scenario_id"])
            ok = bool(info["valid_accepted"])
            status.append((e, ok, info))
        have = sum(1 for _, ok, _ in status if ok)
        for e, ok, info in status:
            if ok:
                continue
            row = dict(e)
            row.update(
                category="P1_benign_missing_rep",
                have_valid_before=have,
                missing_rep=str(not ok),
                historical_status=("rejected_only"
                                   if info["rejected_terms"] else "never_attempted"),
                rejection_terms="|".join(sorted(info["rejected_terms"])) or "",
            )
            p1_rows.append(row)
            entries[e["scenario_id"]] = row
    p1_count = len(p1_rows)

    # ── P2: attacked executions with no valid accepted execution ────────────
    p2_rows = []
    for e in scope:
        if e["condition"] != "single_lep" or not e.get("lep_codes"):
            continue
        if not set(e["lep_codes"]) <= set(RETAINED):
            continue
        info = scenario_terminations(consolidated, e["scenario_id"])
        if info["valid_accepted"]:
            continue
        row = dict(e)
        row.update(
            category="P2_attacked_missing_accepted",
            historical_status=("rejected_only"
                               if info["rejected_terms"] else "never_attempted"),
            rejection_terms="|".join(sorted(info["rejected_terms"])) or "",
        )
        p2_rows.append(row)
        entries[e["scenario_id"]] = row
    p2_count = len(p2_rows)

    # ── P3: accepted but structurally invalid under the corrected contract ───
    p3_rows = []
    for r in impact["traces"]:
        if not (r.get("accepted")
                and "accepted_origin_structure_invalid" in (r.get("rerun_reasons") or [])
                and r["topology"] in TOPOS
                and r["task_family"] == "financial_analysis"
                and r["condition"] == "single_lep"
                and "LEP_INDIRECT_PROMPT_INJECTION" in r.get("lep_codes", [])
                and r.get("execution_variant", "standard") == "standard"):
            continue
        e = by_id.get(r["scenario_id"])
        assert e is not None, f"P3 scenario not in canonical plan: {r['scenario_id']}"
        row = dict(e)
        row.update(
            category="P3_accepted_structure_invalid",
            historical_status="accepted",
            rejection_terms="origin_structure_invalid_under_corrected_contract",
        )
        p3_rows.append(row)
        entries[r["scenario_id"]] = row
    p3_count = len(p3_rows)

    all_rows = sorted(entries.values(), key=lambda r: r["scenario_id"])
    total = len(all_rows)

    # Invariants
    assert not any(is_mem_entry(r) for r in all_rows), "memory execution leaked"
    assert len({r["scenario_id"] for r in all_rows}) == total, "duplicate scenario ids"
    cats = Counter(r["category"] for r in all_rows)
    assert cats["P1_benign_missing_rep"] == p1_count
    assert cats["P2_attacked_missing_accepted"] == p2_count
    assert cats["P3_accepted_structure_invalid"] == p3_count

    # Manifest carries ONLY the loader's exact ENTRY_FIELDS; the rich category /
    # historical-status annotations stay in regeneration_inventory.csv.
    manifest_entries = [entry_for_manifest(r) for r in all_rows]

    # ── settings: identical to the frozen v1 manifest ───────────────────────
    v1 = json.loads((ROOT / "analysis" / "production_regeneration_manifest_v1"
                     / "regeneration_manifest.json").read_text())
    settings = dict(v1["settings"])
    settings["vllm_url"] = "http://127.0.0.1:8001/v1"  # runtime script rewrites per shard

    # ── fixture hashes: refresh from workspace, require stability vs v1 ─────
    fixtures_used = sorted({r["fixture_id"] for r in all_rows})
    fixture_sha = {}
    for fx in fixtures_used:
        raw = (WS / fx / "manifest.json").read_bytes()
        h = hashlib.sha256(raw).hexdigest()
        fixture_sha[fx] = h
        if fx in v1.get("fixture_manifest_sha256", {}):
            assert h == v1["fixture_manifest_sha256"][fx], \
                f"fixture {fx} differs from frozen v1 hash — stop"

    manifest = {
        "schema": "agentprop-readiness-selection-v1",
        "settings": settings,
        "selection": "bv_rl_regeneration_v2: P1 benign-missing + P2 attacked-missing + P3 structure-invalid (BV & RL only)",
        "execution_ready": True,
        "blocker": None,
        "fixture_manifest_sha256": fixture_sha,
        "scenarios": manifest_entries,
    }
    (OUT / "regeneration_manifest.json").write_text(json.dumps(manifest, indent=1))

    # ── sharding ───────────────────────────────────────────────────────────
    # A shard runs one vLLM instance; wall-clock ~= startup + sum(p95 cost).
    # Runtime sharding IS benchmark/split_merge.assign_node (md5(pair_tag) % N),
    # so the real per-shard distribution must be computed with the SAME hash.
    from benchmark.split_merge import assign_node
    per_shard_capacity = int((SHARD_WALL_BUDGET_S - VLLM_STARTUP_S) / 1.25)
    # 1.25x headroom on p95 sums to absorb retries of backend-failed executions
    cost_sum_total = sum(COST_P95_S.get(r["topology"], 45) for r in all_rows)
    target = max(2, -(-cost_sum_total // per_shard_capacity))

    # Real distribution under the production split: scenario -> shard via
    # assign_node(pair_tag, target). A benign twin shares its attack's
    # pair_tag, so pairs stay together on one shard.
    shard_costs: dict[int, int] = {}
    shard_ns: dict[int, int] = {}
    orphan = 0
    for r in all_rows:
        pg = r["pair_tag"]
        if not pg:
            pg = f"b_{r['scenario_id']}"
            orphan += 1
        shard = assign_node(pg, target)
        shard_costs[shard] = shard_costs.get(shard, 0) + COST_P95_S.get(r["topology"], 45)
        shard_ns[shard] = shard_ns.get(shard, 0) + 1
    shard_plan = [
        {"shard": i, "executions": shard_ns.get(i, 0),
         "cost_p95_s": shard_costs.get(i, 0),
         "est_wall_s_with_startup": shard_costs.get(i, 0) + VLLM_STARTUP_S}
        for i in range(target)
    ]
    bin_ns = [shard_ns.get(i, 0) for i in range(target)]
    bin_costs = [shard_costs.get(i, 0) for i in range(target)]
    if orphan:
        print(f"NOTE: {orphan} rows lacked a pair_tag; hashed by b_<scenario_id>")

    (OUT / "regeneration_inventory.csv").write_text(
        "\n".join(
            ["category,scenario_id,topology,task_family,lep_code,propagation_mode,"
             "repetition_index,condition,execution_variant,historical_status,rejection_terms"]
            + [
                f"{r['category']},{r['scenario_id']},{r['topology']},{r['task_family']},"
                f"{r['lep_codes'][0] if r['lep_codes'] else ''},{r['propagation_mode']},"
                f"{r['repetition_index']},{r['condition']},{r['execution_variant']},"
                f"{r['historical_status']},{r['rejection_terms']}"
                for r in all_rows
            ],
        ) + "\n",
    )

    by_breakdown = lambda key: Counter(r[key] for r in all_rows)
    summary = {
        "generated": "2026-11-12",
        "schema": "agentprop-readiness-selection-v1",
        "scope": "branch_and_verify + review_loop only; coordinator_workers excluded",
        "total_regeneration_executions": total,
        "categories": {"P1_benign_missing_rep": p1_count,
                       "P2_attacked_missing_accepted": p2_count,
                       "P3_accepted_structure_invalid": p3_count},
        "memory_excluded": {"LEP_MEMORY_POISONING_scenarios": 0, "memory_enabled_variants": 0},
        "deduplication": "by scenario_id; P1/P2/P3 disjoint by construction",
        "by_topology": dict(by_breakdown("topology")),
        "by_task_family": dict(by_breakdown("task_family")),
        "by_lep_type": {("BENIGN" if not r["lep_codes"] else r["lep_codes"][0]): 0
                        for r in []} | dict(Counter(
                        ("BENIGN" if not r["lep_codes"] else r["lep_codes"][0]) for r in all_rows)),
        "by_propagation_mode": dict(by_breakdown("propagation_mode")),
        "by_historical_status": dict(Counter(r["historical_status"] for r in all_rows)),
        "shard_plan": {
            "target_shards": target,
            "per_shard_capacity_s": per_shard_capacity,
            "vllm_startup_s": VLLM_STARTUP_S,
            "cost_p95_s_per_scenario": COST_P95_S,
            "executions_min": min(bin_ns), "executions_max": max(bin_ns),
            "executions_avg": round(total / target, 1),
            "wall_min_s": min(bin_costs[i] + VLLM_STARTUP_S for i in range(target)),
            "wall_max_s": max(bin_costs[i] + VLLM_STARTUP_S for i in range(target)),
            "all_within_2h_target": all(bin_costs[i] + VLLM_STARTUP_S <= SHARD_WALL_BUDGET_S
                                        for i in range(target)),
            "shards": shard_plan,
        },
    }
    (OUT / "regeneration_summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: summary[k] for k in
                      ("total_regeneration_executions", "categories", "by_topology",
                       "by_task_family", "by_lep_type", "by_propagation_mode",
                       "by_historical_status")}, indent=1))
    print("fixtures:", len(fixtures_used), "shards:", target,
          "max wall s:", summary["shard_plan"]["wall_max_s"])


if __name__ == "__main__":
    main()
