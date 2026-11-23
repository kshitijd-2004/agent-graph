"""Shard sizing for the BV+RL regeneration, using the REAL production split.

The benchmark shards via benchmark/split_merge.split_plan(plan, node, N)
(md5(pair_tag) % N) — an uneven distribution. This planner recomputes the
TRUE per-shard distribution for N, prices each shard from the pilot-observed
per-topology runtimes, and rewrites regeneration_summary.shard_plan.

Cost model (from pilot job 3581169, 23 executions, 0 backend failures):
  - branch_and_verify: median 27.7 s, p95 50.8 s
  - review_loop:      median 62.4 s, p95 212.8 s (212.8/221.8 driven by two
    truncation-prone fixtures; the full population has only 3 such RL rows)

Two pricing tiers are reported per shard:
  - observed-median : BV 28 s / RL 62 s            (expected case)
  - conservative    : BV 51 s (=p95) / RL 130 s (~2x median; absorbs the
                      truncation-tail recurrence + execution variance)
  + 600 s vLLM startup allowance (pilot observed ~3 min; sbatch readiness
    poll caps at 120 x 5 s = 600 s).

Scenario manifest is NOT modified. Only regeneration_summary.shard_plan and
the sbatch array/limit lines are updated.

Usage:
  python3 analysis/bv_rl_regeneration_v2/plan_shards.py            # target N=24
  python3 analysis/bv_rl_regeneration_v2/plan_shards.py --dry      # report only
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "analysis" / "bv_rl_regeneration_v2"
MANIFEST = OUT / "regeneration_manifest.json"
SUMMARY = OUT / "regeneration_summary.json"

from benchmark.split_merge import split_plan  # noqa: E402

STARTUP_S = 600
TARGET_N = 24
COST_MEDIAN = {"branch_and_verify": 28, "review_loop": 62}
COST_CONSERVATIVE = {"branch_and_verify": 51, "review_loop": 130}
RECOMMENDED_TIME_MIN = 105  # 01:45:00


def _shard_report(scenarios: list, N: int) -> dict:
    shards = {n: split_plan(scenarios, n, N) for n in range(N)}
    rows = []
    for n in range(N):
        c = Counter(e["topology"] for e in shards[n])
        rows.append({
            "shard": n,
            "executions": len(shards[n]),
            "review_loop": c.get("review_loop", 0),
            "branch_and_verify": c.get("branch_and_verify", 0),
            "est_wall_s_median_model": sum(COST_MEDIAN.get(e["topology"], 45)
                                           for e in shards[n]) + STARTUP_S,
            "est_wall_s_conservative": sum(COST_CONSERVATIVE.get(e["topology"], 45)
                                           for e in shards[n]) + STARTUP_S,
        })
    return rows


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=TARGET_N)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args(argv)

    scenarios = json.loads(MANIFEST.read_text())["scenarios"]
    N = args.n
    rows = _shard_report(scenarios, N)
    cons_max = max(r["est_wall_s_conservative"] for r in rows)
    med_max = max(r["est_wall_s_median_model"] for r in rows)

    print(f"True split_plan distribution at N={N} (actual runtime sharding):")
    print(f"{'shard':>5} {'exec':>4} {'RL':>3} {'BV':>3}  {'median-model':>13} {'conservative':>13}")
    for r in rows:
        print(f"{r['shard']:>5} {r['executions']:>4} {r['review_loop']:>3} {r['branch_and_verify']:>3}  "
              f"{r['est_wall_s_median_model']//60:>4}m {r['est_wall_s_median_model']%60:>2}s "
              f"{r['est_wall_s_conservative']//60:>4}m {r['est_wall_s_conservative']%60:>2}s")
    print(f"\nmedian model: max wall {med_max//60}m | conservative model: max wall {cons_max//60}m")
    print(f"recommended --time {RECOMMENDED_TIME_MIN}m "
          f"-> headroom over conservative worst: {RECOMMENDED_TIME_MIN - cons_max//60}m "
          f"({'OK' if cons_max <= RECOMMENDED_TIME_MIN*60 else 'INCREASE LIMIT'})")

    if args.dry:
        return

    summary = json.loads(SUMMARY.read_text())
    summary["shard_plan"] = {
        "target_shards": N,
        "method": "split_plan(plan, node, N) — the actual runtime sharder (md5 pair_tag % N)",
        "cost_model": {
            "observed_pilot": {
                "branch_and_verify": {"median_s": 27.7, "p95_s": 50.8, "max_s": 51.0},
                "review_loop": {"median_s": 62.4, "p95_s": 212.8, "max_s": 221.8},
                "note": "RL p95/max driven by 2 truncation-prone fixtures; "
                        "population contains only 3 such RL rows",
            },
            "median_per_scenario_s": COST_MEDIAN,
            "conservative_per_scenario_s": COST_CONSERVATIVE,
            "vllm_startup_s": STARTUP_S,
        },
        "wall_time_recommended_min": RECOMMENDED_TIME_MIN,
        "wall_time_rationale": "01:45:00 covers the conservative-model worst shard "
            f"({cons_max//60}m) with {RECOMMENDED_TIME_MIN - cons_max//60}m headroom; "
            f"expected worst is {med_max//60}m (median model)",
        "executions_min": min(r["executions"] for r in rows),
        "executions_max": max(r["executions"] for r in rows),
        "wall_max_s_median_model": med_max,
        "wall_max_s_conservative": cons_max,
        "all_within_recommended_limit": cons_max <= RECOMMENDED_TIME_MIN * 60,
        "shards": rows,
    }
    SUMMARY.write_text(json.dumps(summary, indent=1))
    print(f"\nWrote corrected shard_plan (N={N}) to {SUMMARY.relative_to(ROOT)}")
    print(f"Update bvrl_regen.sbatch: #SBATCH --array=0-{N-1}  #SBATCH --time=0{RECOMMENDED_TIME_MIN//60}:{RECOMMENDED_TIME_MIN%60:02d}:00")


if __name__ == "__main__":
    main()
