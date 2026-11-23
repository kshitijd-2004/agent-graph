"""Filter coordinator_workers executions out of production_regeneration_manifest_v1.

Writes analysis/coordinator_workers_regeneration/:
  - filtered_manifest.json   (execution-ready, original identities preserved)
  - execution_counts.json    (exact counts by category / topology / mode)
  - sharding_plan.json       (Slurm array -> runner shard assignment)

No execution, no submission, no modification of manifest v1.
"""
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "analysis" / "production_regeneration_manifest_v1" / "regeneration_manifest.json"
OUT = ROOT / "analysis" / "coordinator_workers_regeneration"
WS = ROOT / "workspace_fixtures"

EXPECTED_TOTAL = 4937
EXPECTED = {
    "coordinator_workers_benign": 2109,
    "coordinator_workers_single_origin_lep": 2812,
    "coordinator_workers_one_to_many_lep": 16,
}


def sha256_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    data = json.loads(SRC.read_text())
    assert data["schema"] == "agentprop-readiness-selection-v1"
    scenarios = data["scenarios"]
    fixture_hashes = data["fixture_manifest_sha256"]

    # ── Filter to coordinator_workers, preserving every original field ──────
    filtered = [s for s in scenarios if s["topology"] == "coordinator_workers"]

    # ── Requirement 5: no memory variants; requirement 4: no memory poisoning ─
    mem = [s for s in filtered if s["execution_variant"] != "standard"
           or "MEMORY_POISONING" in s["lep_code"] or "MEMORY_POISONING" in s["lep_codes"]]
    assert not mem, f"memory executions leaked into filter: {mem[:5]}"

    # ── Requirement 1+6: identities unchanged, no duplicates ───────────────
    canon = Counter(
        (s["fixture_id"], s["topology"], s["execution_variant"], s["lep_code"],
         s["propagation_mode"], s["repetition_index"])
        for s in filtered
    )
    dups = {k: v for k, v in canon.items() if v > 1}
    assert not dups, f"duplicate canonical identities: {dups}"
    assert len({s["run_id"] for s in filtered}) == len(filtered), "duplicate run_id"
    assert len({s["scenario_id"] for s in filtered}) == len(filtered), "duplicate scenario_id"
    assert len({s["pair_tag"] for s in filtered}) <= len(filtered)

    # ── Requirement 2: every fixture resolves to its recorded original manifest ─
    fixtures_used = sorted({s["fixture_id"] for s in filtered})
    fixture_report = {}
    for fx in fixtures_used:
        raw = (WS / fx / "manifest.json").read_bytes()
        got = hashlib.sha256(raw).hexdigest()
        fixture_report[fx] = {
            "workspace_manifest_sha256": got,
            "recorded_sha256": fixture_hashes.get(fx),
            "resolves_to_recorded": got == fixture_hashes.get(fx),
        }
    unresolved = [fx for fx, r in fixture_report.items() if not r["resolves_to_recorded"]]
    assert not unresolved, f"fixtures not resolving to recorded hashes: {unresolved}"
    assert len(fixtures_used) == 703, f"fixture population is {len(fixtures_used)}, expected 703"

    # ── SWE 200 exclusion: no swe_101..swe_300 may exist in the 703 population ─
    swe = [fx for fx in fixtures_used if fx.startswith("code_review_swe_")]
    swe_numbers = sorted({int(fx.split("_swe_")[1].split("_")[0]) for fx in swe})
    assert min(swe_numbers) == 1 and max(swe_numbers) == 100, swe_numbers
    swe_excluded = [fx for fx in fixtures_used if fx.startswith("code_review_swe_")
                    and int(fx.split("_swe_")[1].split("_")[0]) > 100]
    assert not swe_excluded, swe_excluded

    # ── Counts ───────────────────────────────────────────────────────────────
    c = Counter()
    for s in filtered:
        if s["condition"] == "benign":
            c["coordinator_workers_benign"] += 1
        elif s["propagation_mode"] == "single_origin":
            c["coordinator_workers_single_origin_lep"] += 1
        elif s["propagation_mode"] == "one_to_many":
            c["coordinator_workers_one_to_many_lep"] += 1
        else:
            c[f"UNEXPECTED:{s['condition']}:{s['propagation_mode']}"] += 1
    assert dict(c) == EXPECTED, f"count mismatch:\n{dict(c)}\nvs {EXPECTED}"
    assert len(filtered) == EXPECTED_TOTAL

    by_lep = Counter(s["lep_code"] for s in filtered if s["lep_code"])
    by_family = Counter(s["task_family"] for s in filtered)
    by_mode = Counter(s["propagation_mode"] for s in filtered)

    manifest = {
        "schema": "agentprop-readiness-selection-v1",
        "settings": data["settings"],
        "selection": "coordinator-workers-regeneration-from-v1-audit",
        "execution_ready": True,
        "blocker": None,
        "fixture_manifest_sha256": fixture_hashes,
        "scenarios": filtered,
    }
    out_manifest = OUT / "filtered_manifest.json"
    out_manifest.write_text(json.dumps(manifest, indent=2) + "\n")

    provenance = {
        "derived_from": {
            "source_manifest": "analysis/production_regeneration_manifest_v1/regeneration_manifest.json",
            "source_sha256": sha256_bytes(SRC),
            "filter": "topology == coordinator_workers (categories A, B, B-O2M)",
            "excluded_categories": ["C_review_loop_benign_missing",
                                    "D_branch_and_verify_benign_missing",
                                    "E_non_cw_lep_missing"],
            "swe_fixtures_101_to_300": "absent from 703-fixture population; nothing to exclude",
            "memory_poisoning_and_memory_enabled": "absent from source manifest; asserted absent",
            "note": "Provenance is recorded here, NOT in the manifest JSON, because "
                    "benchmark.plan_manifest.load_plan_manifest rejects any top-level field "
                    "outside the agentprop-readiness-selection-v1 schema.",
        },
        "fixtures_used": fixtures_used,
        "fixture_resolution": fixture_report,
    }
    (OUT / "manifest_provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")

    counts = {
        "total": len(filtered),
        "by_category": EXPECTED,
        "by_category_verified": dict(c),
        "by_condition": dict(Counter(s["condition"] for s in filtered)),
        "by_propagation_mode": dict(by_mode),
        "by_lep": dict(by_lep),
        "by_task_family": dict(by_family),
        "fixture_count": len(fixtures_used),
        "execution_identity_fields_preserved_from_source": True,
        "duplicate_canonical_identities": 0,
        "duplicate_run_ids": 0,
        "duplicate_scenario_ids": 0,
        "fixtures_unresolved": 0,
    }
    (OUT / "execution_counts.json").write_text(json.dumps(counts, indent=2) + "\n")

    # ── Sharding plan: mirror production.sbatch pair_tag-hash partition ──────
    num_shards = 64
    shards = [[] for _ in range(num_shards)]
    for s in filtered:
        node = int(hashlib.md5(s["pair_tag"].encode()).hexdigest()[:8], 16) % num_shards
        shards[node].append(s["scenario_id"])
    sharding = {
        "num_shards": num_shards,
        "partitioning": "md5(pair_tag)[:8] %% num_shards — identical to "
                        "benchmark.split_merge.split_plan, so --run-node/--num-nodes "
                        "reproduce these assignments deterministically",
        "paired_traces_stay_together": True,
        "per_shard": [len(x) for x in shards],
    }
    (OUT / "sharding_plan.json").write_text(json.dumps(sharding, indent=2) + "\n")

    print(json.dumps({k: v for k, v in counts.items() if k != "by_lep"}, indent=2))
    print("by_lep:", dict(by_lep))
    print("shard range:", min(len(x) for x in shards), "-", max(len(x) for x in shards))
    print("source manifest sha256:", sha256_bytes(SRC))


if __name__ == "__main__":
    main()
