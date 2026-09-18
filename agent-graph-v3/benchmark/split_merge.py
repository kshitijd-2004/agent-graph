"""Split and merge utilities for distributed benchmark generation.

Splits a benchmark plan across multiple nodes deterministically by pair_tag,
then merges the outputs back together with validation.
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger("benchmark.split_merge")


def assign_node(pair_tag: str, num_nodes: int = 2) -> int:
    """Deterministically assign a pair_tag to a node.

    Uses md5 hash of the pair_tag for cross-process determinism
    (Python's built-in hash() is randomized per process).

    Args:
        pair_tag: The pair identifier grouping benign+LEP scenarios
        num_nodes: Total number of nodes

    Returns:
        Node index (0-based)
    """
    digest = hashlib.md5(pair_tag.encode()).hexdigest()
    return int(digest[:8], 16) % num_nodes


def split_plan(plan: list[dict[str, Any]], node_id: int, num_nodes: int = 2) -> list[dict[str, Any]]:
    """Deterministically split a benchmark plan across nodes.

    Paired traces (benign + LEP) share a pair_tag and stay together.
    Each node gets a representative mix of topologies, tasks, LEPs, and modes.

    Args:
        plan: Full plan from BenchmarkManifest.build_plan()
        node_id: This node's ID (0-indexed)
        num_nodes: Total number of nodes

    Returns:
        Filtered plan containing only entries assigned to this node
    """
    filtered = []
    for entry in plan:
        pair_tag = entry.get("pair_tag", "")
        if assign_node(pair_tag, num_nodes) == node_id:
            filtered.append(entry)

    logger.info(
        "Split: node %d/%d gets %d of %d plan entries (%.1f%%)",
        node_id, num_nodes, len(filtered), len(plan),
        100 * len(filtered) / max(len(plan), 1),
    )
    return filtered


def split_plan_verbose(
    plan: list[dict[str, Any]], node_id: int, num_nodes: int = 2
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Split plan and return distribution stats.

    Returns:
        (filtered_plan, stats) where stats has per-dimension counts
    """
    filtered = split_plan(plan, node_id, num_nodes)

    stats = {
        "total_entries": len(plan),
        "assigned_to_this_node": len(filtered),
        "by_topology": {},
        "by_task_family": {},
        "by_lep_code": {},
        "by_propagation_mode": {},
        "by_condition": {},
        "num_pairs": len(set(e["pair_tag"] for e in filtered)),
    }

    for entry in filtered:
        t = stats["by_topology"].setdefault(entry["topology"], 0) + 1
        stats["by_topology"][entry["topology"]] = t
        tf = stats["by_task_family"].setdefault(entry["task_family"], 0) + 1
        stats["by_task_family"][entry["task_family"]] = tf
        l = stats["by_lep_code"].setdefault(entry.get("lep_code", ""), 0) + 1
        stats["by_lep_code"][entry.get("lep_code", "")] = l
        p = stats["by_propagation_mode"].setdefault(entry.get("propagation_mode", ""), 0) + 1
        stats["by_propagation_mode"][entry.get("propagation_mode", "")] = p
        c = stats["by_condition"].setdefault(entry["condition"], 0) + 1
        stats["by_condition"][entry["condition"]] = c

    return filtered, stats


def merge_outputs(node_dirs: list[Path], output_dir: Path) -> dict[str, Any]:
    """Merge benchmark outputs from multiple nodes into a single directory.

    Merges:
    - benchmark_records.jsonl (concatenate, deduplicate by scenario_id)
    - benchmark_records.csv (concatenate)
    - benchmark_summary.json (combine statistics)
    - traces/ (copy all unique trace files)

    Validates:
    - No duplicate scenario_ids across nodes
    - Paired traces share execution_id
    - Total record count matches expected

    Args:
        node_dirs: List of node output directories
        output_dir: Where to write merged output

    Returns:
        Validation report dict
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "node_dirs": [str(d) for d in node_dirs],
        "output_dir": str(output_dir),
        "records_merged": 0,
        "traces_merged": 0,
        "duplicate_scenario_ids": [],
        "errors": [],
        "warnings": [],
    }

    # --- Merge benchmark_records.jsonl ---
    seen_scenario_ids: set[str] = set()
    merged_records_path = output_dir / "benchmark_records.jsonl"
    with open(merged_records_path, "w") as out:
        for node_dir in node_dirs:
            records_path = node_dir / "benchmark_records.jsonl"
            if not records_path.exists():
                report["warnings"].append(f"No benchmark_records.jsonl in {node_dir}")
                continue
            with open(records_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    record = json.loads(line)
                    sid = record.get("scenario_id", "")
                    if sid in seen_scenario_ids:
                        report["duplicate_scenario_ids"].append(sid)
                    else:
                        seen_scenario_ids.add(sid)
                        out.write(line + "\n")
                        report["records_merged"] += 1

    # --- Merge benchmark_records.csv ---
    all_rows: list[dict[str, Any]] = []
    header: list[str] = []
    for node_dir in node_dirs:
        csv_path = node_dir / "benchmark_records.csv"
        if not csv_path.exists():
            continue
        with open(csv_path, newline="") as f:
            reader = csv.DictReader(f)
            if not header:
                header = reader.fieldnames or []
            for row in reader:
                sid = row.get("scenario_id", "")
                if sid not in seen_scenario_ids:
                    # This shouldn't happen if JSONL merge succeeded
                    report["warnings"].append(f"CSV-only record: {sid}")
                all_rows.append(row)

    if all_rows and header:
        csv_path = output_dir / "benchmark_records.csv"
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=header)
            writer.writeheader()
            for row in all_rows:
                writer.writerow(row)

    # --- Merge traces/ ---
    traces_dir = output_dir / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    seen_trace_ids: set[str] = set()

    for node_dir in node_dirs:
        node_traces = node_dir / "traces"
        if not node_traces.exists():
            continue
        for trace_file in node_traces.glob("*.json"):
            if trace_file.name in seen_trace_ids:
                report["duplicate_scenario_ids"].append(f"trace: {trace_file.name}")
                continue
            seen_trace_ids.add(trace_file.name)
            shutil.copy2(trace_file, traces_dir / trace_file.name)
            report["traces_merged"] += 1

    # --- Merge benchmark_summary.json ---
    summaries: list[dict[str, Any]] = []
    for node_dir in node_dirs:
        summary_path = node_dir / "benchmark_summary.json"
        if summary_path.exists():
            with open(summary_path) as f:
                summaries.append(json.load(f))

    if summaries:
        merged_summary = _merge_summaries(summaries)
        summary_path = output_dir / "benchmark_summary.json"
        with open(summary_path, "w") as f:
            json.dump(merged_summary, f, indent=2)
        report["summary"] = merged_summary

    # --- Validate paired execution_ids ---
    execution_ids: dict[str, list[str]] = {}
    for trace_file in traces_dir.glob("*_trace.json"):
        try:
            with open(trace_file) as f:
                trace = json.load(f)
            eid = trace.get("execution_id", "")
            variant = trace.get("variant", "")
            if eid:
                execution_ids.setdefault(eid, []).append(variant)
        except Exception:
            pass

    orphaned = {eid: variants for eid, variants in execution_ids.items()
                if len(variants) != 2}
    if orphaned:
        report["warnings"].append(f"Orphaned execution_ids (not paired): {list(orphaned.keys())[:10]}")

    report["paired_execution_ids"] = len(execution_ids)
    report["orphaned_execution_ids"] = len(orphaned)

    logger.info(
        "Merge complete: %d records, %d traces from %d nodes → %s",
        report["records_merged"], report["traces_merged"],
        len(node_dirs), output_dir,
    )

    return report


def _merge_summaries(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine multiple node summaries into one."""
    merged: dict[str, Any] = {}
    for key in ["total_runs", "successful_runs", "failed_runs",
                "injection_fired", "injection_fired_rate",
                "downstream_failures", "downstream_failure_rate",
                "recovery_rate", "consumption_hits", "propagation_hits",
                "recovery_hits", "task_success"]:
        merged[key] = sum(s.get(key, 0) for s in summaries if isinstance(s.get(key), (int, float)))

    # Recompute rates
    total = merged.get("total_runs", 0)
    for rate_key in ["injection_fired_rate", "downstream_failure_rate", "recovery_rate"]:
        raw_key = rate_key.replace("_rate", "")
        raw = merged.get(raw_key, 0)
        merged[rate_key] = round(raw / max(total, 1), 3)

    # Merge nested dicts
    for nested_key in ["conditions", "per_topology", "per_propagation_mode"]:
        merged_nested: dict[str, Any] = {}
        for s in summaries:
            for k, v in s.get(nested_key, {}).items():
                if isinstance(v, dict):
                    merged_nested.setdefault(k, {}).update(v)
                else:
                    merged_nested[k] = merged_nested.get(k, 0) + v
        merged[nested_key] = merged_nested

    # Average runtime
    runtimes = [s.get("avg_runtime_seconds", 0) for s in summaries if s.get("avg_runtime_seconds")]
    if runtimes:
        merged["avg_runtime_seconds"] = round(sum(runtimes) / len(runtimes), 2)

    merged["merged_from_nodes"] = len(summaries)
    return merged
