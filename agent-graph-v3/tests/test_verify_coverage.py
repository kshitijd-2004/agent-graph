"""Regression tests for analysis/bv_rl_regeneration_v2/verify_coverage.py.

Covers the two post-pilot defects:
  1. the per-cell aggregation crash when a cell has any number of records
     (the old ``sids, rs = cells[key]`` unpack assumed exactly two),
  2. origin-count + pilot-only handling: records carry no
     expected/actual_injection_origins fields (those live in the record's
     trace metadata, joined via trace_path), and pilot-only scenarios
     (absent from the canonical inventory) must be partitioned, not
     silently dropped or double-counted.
"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis" / "bv_rl_regeneration_v2"))

import verify_coverage  # noqa: E402


def _record(sid, topo="branch_and_verify", family="financial_analysis",
            lep="", mode="single_origin", condition="benign",
            term="completed", eligible=True, trace_path=None,
            runtime=30.0, trace_meta=None):
    return {
        "scenario_id": sid, "topology": topo, "task_family": family,
        "lep_code": lep, "propagation_mode": mode, "condition": condition,
        "termination_reason": term, "dataset_eligible": eligible,
        "trace_path": trace_path, "runtime_seconds": runtime,
    }


def _write_trace(directory: Path, sid: str, meta: dict) -> str:
    rel = f"traces/{sid}_abc123_trace.json"
    p = directory / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"metadata": meta}))
    return rel


def _inventory(tmp_path: Path, sids):
    inv = tmp_path / "inventory.csv"
    with open(inv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["scenario_id", "category"])
        for s in sids:
            w.writerow([s, "P1_benign_missing_rep"])
    return {r["scenario_id"]: r for r in csv.DictReader(open(inv))}


def test_cell_with_many_records_does_not_crash(tmp_path):
    """The original crash: >2 records per cell must aggregate cleanly."""
    recs = [_record(f"branch_and_verify_fin_00{i}_standard_00_benign",
                    lep="", condition="benign", runtime=10.0 + i)
            for i in range(5)]
    out = tmp_path / "report.json"
    rep = _run_on_paths(tmp_path, [recs], _inventory(tmp_path, []), out)
    key = "('branch_and_verify', 'financial_analysis', 'BENIGN', 'single_origin')"
    assert rep["by_cell"][key]["total"] == 5
    assert rep["by_cell"][key]["admitted"] == 5


def _run_on_paths(work_dir: Path, record_lists, inventory, out: Path):
    """Write record lists to jsonl files and call run() on those paths."""
    paths = []
    for i, recs in enumerate(record_lists):
        p = work_dir / f"records_{i}.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in recs))
        paths.append(str(p))
    return verify_coverage.run(paths, inventory, out)


def test_origin_counts_joined_from_trace_metadata(tmp_path):
    """Records have no origin fields; they must come from trace metadata."""
    side = tmp_path / "side"
    t_rel = _write_trace(side, "bv_m2o_ok", {
        "expected_injection_origins": 2, "actual_injection_origins": 2,
        "termination_reason": "completed", "dataset_eligible": True,
    })
    r_rel = _write_trace(side, "bv_m2o_incomplete", {
        "expected_injection_origins": 2, "actual_injection_origins": 1,
        "termination_reason": "injection_count_mismatch",
        "dataset_eligible": False,
    })
    ok = _record("bv_m2o_ok", lep="LEP_TOOL_RESULT_CORRUPTION",
                 mode="many_to_one", condition="single_lep",
                 trace_path=str(side / t_rel), eligible=True)
    inc = _record("bv_m2o_incomplete", lep="LEP_TOOL_RESULT_CORRUPTION",
                  mode="many_to_one", condition="single_lep",
                  trace_path=str(side / r_rel), eligible=False,
                  term="injection_count_mismatch")
    rep = _run_on_paths(tmp_path, [[ok, inc]], _inventory(tmp_path, []),
                       tmp_path / "report.json")
    key = "('branch_and_verify', 'financial_analysis', " \
          "'LEP_TOOL_RESULT_CORRUPTION', 'many_to_one')"
    assert rep["by_cell"][key]["expected_origins"] == {"2": 2}
    assert rep["by_cell"][key]["actual_origins"] == {"2": 1, "1": 1}
    # M2O gate: admitted trace met expected; incomplete trace was NOT admitted
    assert rep["m2o_gate"]["admitted_count"] == 1
    assert rep["m2o_gate"]["admitted_all_expected_met"] is True
    assert rep["m2o_gate"]["rejected_incomplete_count"] == 1
    assert rep["m2o_gate"]["incomplete_never_admitted"] is True
    assert rep["traces_unmatched"] == []


def test_dedup_keeps_latest_per_scenario(tmp_path):
    """Pilot record then full-batch record for the same scenario: keep batch."""
    pilot = _record("dup", lep="LEP_TOOL_RESULT_CORRUPTION",
                    condition="single_lep", eligible=False,
                    term="injection_count_mismatch", runtime=40.0)
    batch = _record("dup", lep="LEP_TOOL_RESULT_CORRUPTION",
                    condition="single_lep", eligible=True, runtime=35.0)
    rep = _run_on_paths(tmp_path, [[pilot], [batch]],
                       _inventory(tmp_path, ["dup"]), tmp_path / "r.json")
    assert rep["unique_scenarios"] == 1
    assert rep["deduplicated_records"] == 1
    assert rep["status_counts"] == {"admitted": 1}


def test_pilot_only_partition(tmp_path):
    """5 pilot-only benign controls: partitioned, all-benign, not counted
    against the canonical population, flagged separately."""
    canon = _record("canon_benign", runtime=20.0)
    pilot_only = [_record(f"extra_ctrl_{i}", runtime=25.0 + i)
                  for i in range(5)]
    inv = _inventory(tmp_path, ["canon_benign"])
    rep = _run_on_paths(tmp_path, [[canon, *pilot_only]], inv,
                       tmp_path / "r.json")
    assert rep["in_canonical_population"] == 1
    assert rep["pilot_only"] == 5
    assert rep["pilot_only_all_benign"] is True
    assert rep["pilot_only_scenarios"] == [f"extra_ctrl_{i}" for i in range(5)]
    assert rep["total_records"] == 6


def test_review_loop_termination_review(tmp_path):
    """RL: no max_events_reached; protocol_violation listed; benign anomaly."""
    rl_ok = _record("rl_ok", topo="review_loop", lep="LEP_HANDOFF_CORRUPTION",
                    condition="single_lep", runtime=50.0)
    rl_pv = _record("rl_pv", topo="review_loop", lep="LEP_INPUT_DISREGARD",
                    condition="single_lep", eligible=False,
                    term="protocol_violation", runtime=200.0)
    rl_benign_pv = _record("rl_benign_pv", topo="review_loop",
                           condition="benign", eligible=False,
                           term="protocol_violation", runtime=210.0)
    rep = _run_on_paths(tmp_path, [[rl_ok, rl_pv, rl_benign_pv]],
                       _inventory(tmp_path, []), tmp_path / "r.json")
    assert rep["review_loop"]["max_events_reached"] == 0
    assert set(rep["review_loop"]["protocol_violations"]) == {"rl_pv", "rl_benign_pv"}
    assert rep["review_loop"]["admitted"] == 1
    # the benign arm rejection is surfaced as a comparability anomaly
    assert any(a["scenario_id"] == "rl_benign_pv"
               for a in rep["benign_anomalies"])
    stats = rep["runtime_by_topology"]["review_loop"]
    assert stats["n"] == 3 and stats["min"] == 50.0 and stats["max"] == 210.0


if __name__ == "__main__":
    # minimal standalone runner (pytest also collects this file normally)
    import tempfile
    for fn in (test_cell_with_many_records_does_not_crash,
               test_origin_counts_joined_from_trace_metadata,
               test_dedup_keeps_latest_per_scenario,
               test_pilot_only_partition,
               test_review_loop_termination_review):
        with tempfile.TemporaryDirectory() as td:
            fn(Path(td))
            print(f"PASS {fn.__name__}")
