"""Audit accepted AND rejected production traces; never execute a benchmark.

Run as python -m scripts.audit_origin_semantics ROOT --output OUTPUT.json.
The affected union includes cells whose approved origin policy changed, plus
any additional previously accepted structural violations. Minimal reruns omit
traces already rejected for independent execution failures.
"""
import argparse
from collections import Counter
import json
from pathlib import Path

from benchmark.benchmark_runner import BenchmarkRunner
from generation.injection_origins import IPI, origin_structure_errors
from schemas import ScenarioSpec, Trace, WorkflowConfig


def audit(root):
    rows = []
    totals = Counter()
    for node in sorted(root.glob("node_*")):
        for location in ("traces", "rejected_traces"):
            for path in sorted((node / location).glob("*_trace.json")):
                data = json.loads(path.read_text())
                meta = data["metadata"]
                accepted = location == "traces"
                totals["accepted" if accepted else "rejected"] += 1
                codes = meta.get("lep_codes", [])
                configs = [BenchmarkRunner._resolve_lep(None, code, meta["task_family"]) for code in codes]
                spec = ScenarioSpec(scenario_id=meta["scenario_id"], task_family=meta["task_family"],
                    task_variant=meta.get("task_variant", "default"),
                    fixture_id=meta["fixture_id"], condition=meta["condition"], lep_configs=configs,
                    workflow_config=WorkflowConfig(topology=meta["topology"],
                        propagation_mode=meta.get("propagation_mode", "single_origin")))
                trace = Trace.from_dict(data)
                errors = origin_structure_errors(trace, spec)
                financial_ipi = meta["task_family"] == "financial_analysis" and IPI in codes
                o2m = bool(codes) and spec.workflow_config.propagation_mode == "one_to_many"
                reasons = []
                if financial_ipi:
                    reasons.append("financial_IPI_first_read_and_intended_origin_policy")
                if o2m:
                    reasons.append("one_to_many_upstream_origin_before_initial_fanout")
                if accepted and errors:
                    reasons.append("accepted_origin_structure_invalid")
                row = {"trace_path": str(path), "accepted": accepted,
                       **{k: meta.get(k) for k in ("scenario_id", "fixture_id", "task_family", "task_variant",
                          "topology", "propagation_mode", "lep_codes", "condition", "repetition_index",
                          "expected_injection_origins", "execution_variant", "termination_reason")},
                       "actual": trace.injection_origin_count,
                       "origins": [{"index": e.event_index, "role": e.agent_role,
                                    "boundary": e.event_type.value}
                                   for e in trace.events if e.event_labels.is_injection_origin],
                       "structure_errors": errors, "affected": bool(reasons),
                       "rerun": bool(reasons) and (accepted or meta["termination_reason"] == "injection_count_mismatch"),
                       "rerun_reasons": reasons}
                rows.append(row)
    grouped = {}
    for row in rows:
        if not row["affected"]:
            continue
        key = (row["task_family"], row["topology"], row["propagation_mode"], tuple(row["lep_codes"]))
        counts = grouped.setdefault(key, Counter())
        counts["accepted" if row["accepted"] else "rejected"] += 1
        counts["total"] += 1
        counts["minimal_rerun"] += int(row["rerun"])
        if not row["accepted"]:
            counts["rejected_" + row["termination_reason"]] += 1
    matrix = [{"task_family": k[0], "topology": k[1], "mode": k[2], "leps": k[3], **v}
              for k, v in sorted(grouped.items())]
    return {"scanned": dict(totals), "rerun_counts": dict(Counter(
                "accepted" if r["accepted"] else "rejected" for r in rows if r["rerun"])),
            "accepted_structure_invalid": sum(r["accepted"] and bool(r["structure_errors"]) for r in rows),
            "affected_counts": dict(Counter("accepted" if r["accepted"] else "rejected"
                                             for r in rows if r["affected"])),
            "matrix": matrix, "traces": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    reruns = [r for r in result["traces"] if r["rerun"]]
    args.output.with_name("rerun_manifest.json").write_text(json.dumps(reruns, indent=2) + "\n")
    overlap = [r for r in result["traces"] if r["affected"] and not r["rerun"]]
    args.output.with_name("already_rejected_overlap.json").write_text(json.dumps(overlap, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "traces"}, indent=2))
