"""Read historical traces; emit a reproducible, per-boundary mismatch audit.

This does not run models, modify traces, or alter dataset admission.
"""
import argparse
from collections import Counter
import json
from pathlib import Path


def audit(root, fixtures):
    from leps.tool_result_corruption import ToolResultCorruptionLEP
    rows = []
    cross = Counter()
    for path in sorted(root.glob("node_*/rejected_traces/*_trace.json")):
        trace = json.loads(path.read_text())
        meta = trace.get("metadata", {})
        if meta.get("termination_reason") != "injection_count_mismatch":
            continue
        manifest_path = fixtures / meta["fixture_id"] / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        code = meta["lep_codes"][0]
        attack = manifest.get("attack", {}).get(
            "prompt_injection" if "INDIRECT" in code else "tool_result", {})
        target = attack.get("target_file") or manifest["required_files"][0]
        boundaries = []
        for event in trace["events"]:
            if event["event_type"] not in ("tool_result", "agent_handoff", "memory_write"):
                continue
            actual_path = (event.get("tool_arguments") or {}).get("path", "")
            qualifies = (event["event_type"] == "tool_result"
                         and event.get("tool_name") == "read_text_file"
                         and actual_path.removeprefix("./") == target.removeprefix("./"))
            boundaries.append({
                "event_index": event["event_index"], "agent": event["agent_role"],
                "boundary": event["event_type"], "tool": event.get("tool_name"),
                "path": actual_path, "target_read": qualifies,
                "tool_error": event.get("tool_error"),
                "origin": event.get("event_labels", {}).get("is_injection_origin", False),
                "lep_metadata": event.get("hidden", {}),
                "replacement_matches": [r["from"] for r in attack.get("replacements", [])
                    if r["from"] in (event.get("tool_result") or "")] if qualifies else [],
            })
        origins = [e["agent"] for e in boundaries if e["origin"]]
        reads = Counter(e["agent"] for e in boundaries if e["target_read"])
        if "INDIRECT" in code:
            category = "IPI_target_filtered_global_occurrence_2"
        elif not reads["analyst"]:
            category = "TRC_analyst_did_not_read_target"
        else:
            category = "TRC_analyst_target_no_replacement_match"
        replay = []
        if "TOOL_RESULT_CORRUPTION" in code:
            for event in trace["events"]:
                if (event["event_type"] == "tool_result" and event["agent_role"] == "analyst"
                        and event.get("tool_name") == "read_text_file"
                        and (event.get("tool_arguments") or {}).get("path", "").removeprefix("./") == target):
                    original = event.get("tool_result") or ""
                    changed = ToolResultCorruptionLEP._fixture_replacements(
                        original, attack["replacements"]) != original
                    replay.append({"event_index": event["event_index"], "material_mutation": changed})
            assert not any(item["material_mutation"] for item in replay), path
        row = {"trace_path": str(path), **{k: meta.get(k) for k in (
            "fixture_id", "task_family", "topology", "propagation_mode",
            "lep_codes", "expected_injection_origins", "actual_injection_origins",
            "termination_reason", "execution_variant")},
            "actual_label_count": sum(e["origin"] for e in boundaries),
            "origin_agents": origins, "target_file": target,
            "target_reads_by_agent": dict(reads), "attack": attack,
            "category": category, "trc_operator_replay": replay, "boundaries": boundaries}
        assert row["actual_label_count"] == trace["injection_origin_count"] == meta["actual_injection_origins"]
        rows.append(row)
        cross[tuple(str(row[k]) for k in ("task_family", "topology", "propagation_mode",
            "lep_codes", "expected_injection_origins", "actual_injection_origins"))] += 1
    accepted_ipi = []
    for path in sorted(root.glob("node_*/traces/*fin_finqa*INDIRECT*json")):
        trace = json.loads(path.read_text())
        meta = trace["metadata"]
        initial_handoffs = [e["event_index"] for e in trace["events"]
                            if e["event_type"] == "agent_handoff" and e["agent_role"] == "coordinator"]
        accepted_ipi.append({"path": str(path), "fixture_id": meta["fixture_id"],
            "topology": meta["topology"], "mode": meta["propagation_mode"],
            "first_coordinator_handoff": min(initial_handoffs) if initial_handoffs else None,
            "origins": [{"role": e["agent_role"], "event_index": e["event_index"]}
                        for e in trace["events"] if e.get("event_labels", {}).get("is_injection_origin")]})
    return {"total": len(rows), "cross_tab": [{"key": k, "count": n} for k, n in sorted(cross.items())],
            "categories": dict(Counter(r["category"] for r in rows)), "traces": rows,
            "accepted_financial_ipi": accepted_ipi}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--fixtures", type=Path, default=Path("workspace_fixtures"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.root, args.fixtures)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items()
                      if k not in ("traces", "accepted_financial_ipi")}, indent=2))
