"""Read-only production inventory.

Reuses the repository's own experiment identity and admission rules
(generation.admission.validate_trace) to classify every saved trace and every
planned execution slot. No inference, no writes to production_run/.

Outputs (all under planning_audit_oct19/):
  inventory.json          machine-readable full inventory
"""
from __future__ import annotations

import collections
import hashlib
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.WARNING)

from generation.admission import validate_trace  # noqa: E402
from benchmark.attempt_accounting import is_perturbed  # noqa: E402
from benchmark.behavioral_anomaly import MIN_CLEAN_RUNS_REQUIRED  # noqa: E402
from benchmark.propagation_analysis import (  # noqa: E402
    PropagationAnalyzer,
    _select_execution_variant,
)

SOURCE = ROOT / "production_run"
OUT = ROOT / "planning_audit_oct19"


def main() -> None:
    dirs = sorted(SOURCE.glob("node_*")) or [SOURCE]
    paths = sorted(p for d in dirs for folder in ("traces", "rejected_traces")
                   for p in (d / folder).glob("*_trace.json"))
    logging.warning("scanning %d trace files across %d shards", len(paths), len(dirs))

    seen: dict[str, str] = {}
    duplicates = []
    ingestion_errors = []

    benign: dict[tuple, dict] = collections.defaultdict(
        lambda: {"reps": {}, "traces": []})
    lep_cells: dict[tuple, dict] = collections.defaultdict(
        lambda: {"traces": [], "attempts": []})
    attempts: list[dict] = []
    protocols: set[str] = set()

    for trace_path in paths:
        raw = trace_path.read_text()
        try:
            data = json.loads(raw)
            fingerprint = hashlib.sha256(
                json.dumps(data, sort_keys=True).encode()).hexdigest()
            trace = PropagationAnalyzer._trace_from_dict(data, trace_path)
        except Exception as error:  # noqa: BLE001
            ingestion_errors.append({"path": str(trace_path), "error": str(error)})
            continue
        meta = trace.metadata
        identity = meta.get("attempt_id") or trace.trace_id
        if identity in seen:
            if fingerprint != seen[identity]:
                duplicates.append({"attempt_id": identity, "conflict": True,
                                   "path": str(trace_path)})
            else:
                duplicates.append({"attempt_id": identity, "conflict": False,
                                   "path": str(trace_path)})
            continue
        seen[identity] = fingerprint

        protocols.add(meta.get("memory_protocol") or "write_interception_v1")
        admission = validate_trace(trace)
        row = {
            "attempt_id": identity,
            "trace_id": trace.trace_id,
            "scenario_id": meta.get("scenario_id", ""),
            "fixture_id": meta.get("fixture_id", ""),
            "task_family": meta.get("task_family", ""),
            "condition": meta.get("condition", ""),
            "topology": meta.get("topology", ""),
            "propagation_mode": meta.get("propagation_mode", ""),
            "lep_codes": meta.get("lep_codes", []),
            "execution_variant": meta.get("execution_variant", "standard"),
            "repetition_index": meta.get("repetition_index", 0),
            "termination_reason": meta.get("termination_reason", ""),
            "dataset_eligible": meta.get("dataset_eligible"),
            "admission_status": admission.status,
            "admission_reason": admission.reason,
            "memory_protocol": meta.get("memory_protocol") or "write_interception_v1",
            "trace_path": str(trace_path.relative_to(ROOT)),
            "in_folder": trace_path.parent.name,
        }
        attempts.append(row)

        if not admission.admitted:
            continue
        if is_perturbed(meta):
            code = (meta.get("lep_codes") or [""])[0]
            key = (meta.get("fixture_id", ""), meta.get("task_family", ""),
                   meta.get("topology", ""), code, meta.get("propagation_mode", ""))
            cell = lep_cells[key]
            cell["traces"].append(row)
        else:
            key = (meta.get("fixture_id", ""), meta.get("topology", ""),
                   meta.get("execution_variant", "standard"))
            ref = benign[key]
            rep = meta.get("repetition_index", 0)
            ref["reps"].setdefault(rep, row)
            ref["traces"].append(row)

    # Attempts recorded in ledgers but with no readable trace on disk.
    identities = {r["attempt_id"] for r in attempts}
    ledger_only = []
    for directory in dirs:
        for filename in ("benchmark_records.jsonl", "attempt_starts.jsonl"):
            path = directory / filename
            if not path.exists():
                continue
            for line in path.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                identity = (record.get("attempt_id") or record.get("trace_id")
                            or record.get("scenario_id"))
                if identity in identities or not is_perturbed(record):
                    continue
                identities.add(identity)
                term = record.get("termination_reason", "")
                ledger_only.append({
                    "attempt_id": identity,
                    "scenario_id": record.get("scenario_id", ""),
                    "fixture_id": record.get("fixture_id", ""),
                    "task_family": record.get("task_family", ""),
                    "topology": record.get("topology", ""),
                    "propagation_mode": record.get("propagation_mode", ""),
                    "lep_codes": record.get("lep_codes", []),
                    "execution_variant": record.get("execution_variant", "standard"),
                    "termination_reason": term,
                    "reason": ("infrastructure_failure" if term.startswith("backend_")
                               else "unverifiable_legacy"),
                    "shard": directory.name,
                })

    # Frozen plans: every planned execution slot, regardless of outcome.
    planned: dict[str, dict] = {}
    for directory in dirs:
        path = directory / "benchmark_plan.json"
        if not path.exists():
            continue
        saved = json.loads(path.read_text())
        if saved.get("schema") != "agentprop-frozen-plan-v1":
            raise SystemExit("Unsupported frozen plan schema in %s" % path)
        for entry in saved["entries"]:
            sid = entry["scenario_id"]
            if sid in planned and planned[sid] != entry:
                raise SystemExit("Conflicting planned slot: %s" % sid)
            planned[sid] = entry

    # Shard-level completion: which shards produced any accepted trace.
    shard_status = {}
    for i in range(160):
        d = SOURCE / ("node_%d" % i)
        if not d.is_dir():
            shard_status[str(i)] = "absent"
            continue
        accepted = len(list((d / "traces").glob("*_trace.json"))) if (d / "traces").is_dir() else 0
        rejected = len(list((d / "rejected_traces").glob("*_trace.json"))) if (d / "rejected_traces").is_dir() else 0
        has_plan = (d / "benchmark_plan.json").exists()
        has_records = (d / "benchmark_records.jsonl").exists()
        if accepted == 0 and rejected == 0 and not has_plan and not has_records:
            shard_status[str(i)] = "empty"
        elif accepted == 0:
            shard_status[str(i)] = "no_accepted_traces"
        else:
            shard_status[str(i)] = "ok"

    # ── Clean reference adequacy ────────────────────────────────────────────
    # Analysis key for an LEP cell: (fixture_id, topology, execution_variant)
    # where execution_variant = memory_enabled for LEP_MEMORY_POISONING else standard.
    clean_deficit = []
    for cell_key, cell in sorted(lep_cells.items()):
        fixture_id, _family, topology, code, mode = cell_key
        exec_variant = ("existing_record_poisoning_v1"
                        if cell["traces"][0]["memory_protocol"] == "existing_record_poisoning_v1"
                        else _select_execution_variant(code))
        ref_key = (fixture_id, topology, exec_variant)
        ref = benign.get(ref_key)
        have = len(ref["reps"]) if ref else 0
        if have >= MIN_CLEAN_RUNS_REQUIRED:
            continue
        clean_deficit.append({
            "fixture_id": fixture_id,
            "task_family": _family,
            "topology": topology,
            "lep_code": code,
            "propagation_mode": mode,
            "execution_variant": exec_variant,
            "valid_clean_runs": have,
            "required": MIN_CLEAN_RUNS_REQUIRED,
            "missing": MIN_CLEAN_RUNS_REQUIRED - have,
            "existing_rep_indices": sorted(ref["reps"]) if ref else [],
            "lep_traces_blocked": len(cell["traces"]),
        })

    # Benign slots that were planned but never produced an admitted trace.
    benign_missing = []
    for sid, entry in sorted(planned.items()):
        if entry.get("condition") not in ("benign", "counterfactual"):
            continue
        key = (entry["fixture_id"], entry["topology"], entry["execution_variant"])
        ref = benign.get(key)
        if ref and entry["repetition_index"] in ref["reps"]:
            continue
        benign_missing.append({
            "scenario_id": sid,
            "fixture_id": entry["fixture_id"],
            "task_family": entry["task_family"],
            "topology": entry["topology"],
            "execution_variant": entry["execution_variant"],
            "repetition_index": entry["repetition_index"],
            "propagation_mode": entry.get("propagation_mode", ""),
            "pair_tag": entry.get("pair_tag", ""),
        })

    # Perturbed slots with no admitted trace, split by recoverable vs not.
    perturbed_recoverable = []
    perturbed_unreachable = []
    perturbed_other = []
    admitted_by_slot = collections.Counter()
    for key, cell in lep_cells.items():
        for row in cell["traces"]:
            admitted_by_slot[row["scenario_id"]] += 1
    ledger_by_slot = collections.defaultdict(list)
    for row in ledger_only:
        ledger_by_slot[row["scenario_id"]].append(row)

    INFRA = ("backend_http_error", "backend_connection_error", "backend_timeout")
    for sid, entry in sorted(planned.items()):
        if entry.get("condition") in ("benign", "counterfactual"):
            continue
        if admitted_by_slot.get(sid, 0) > 0:
            continue
        reasons = []
        for row in attempts:
            if row["scenario_id"] == sid:
                reasons.append((row["termination_reason"], row["admission_reason"],
                                row["admission_status"]))
        for row in ledger_by_slot.get(sid, []):
            reasons.append((row["termination_reason"], row["reason"], "rejected"))
        if not reasons:
            perturbed_other.append({"scenario_id": sid, "entry": entry,
                                    "reason": "no_attempt_recorded"})
            continue
        terms = {r[0] for r in reasons}
        if terms & set(INFRA):
            perturbed_recoverable.append({"scenario_id": sid, "entry": entry,
                                          "termination_reasons": sorted(terms)})
        elif "injection_target_unreachable" in terms:
            perturbed_unreachable.append({"scenario_id": sid, "entry": entry,
                                          "termination_reasons": sorted(terms)})
        else:
            perturbed_other.append({"scenario_id": sid, "entry": entry,
                                    "termination_reasons": sorted(terms),
                                    "admission_reasons": sorted({r[1] for r in reasons})})

    result = {
        "source_dir": str(SOURCE.relative_to(ROOT)),
        "protocols_observed": sorted(protocols),
        "trace_files_scanned": len(paths),
        "distinct_attempts": len(seen),
        "duplicate_attempts": duplicates,
        "ingestion_errors": ingestion_errors,
        "admitted_perturbed_traces": sum(len(c["traces"]) for c in lep_cells.values()),
        "admitted_benign_traces": sum(len(r["traces"]) for r in benign.values()),
        "clean_reference_groups": len(benign),
        "lep_cells": len(lep_cells),
        "planned_slots": len(planned),
        "shard_status": shard_status,
        "clean_deficit_cells": clean_deficit,
        "benign_slots_missing": benign_missing,
        "perturbed_slots_recoverable": perturbed_recoverable,
        "perturbed_slots_unreachable": perturbed_unreachable,
        "perturbed_slots_other": perturbed_other,
        "ledger_only_attempts": ledger_only,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "inventory.json").write_text(json.dumps(result, indent=2))

    logging.warning("admitted perturbed=%d benign=%d",
                    result["admitted_perturbed_traces"],
                    result["admitted_benign_traces"])
    logging.warning("clean deficit cells=%d benign slots missing=%d",
                    len(clean_deficit), len(benign_missing))
    logging.warning("perturbed recoverable=%d unreachable=%d other=%d",
                    len(perturbed_recoverable), len(perturbed_unreachable),
                    len(perturbed_other))


if __name__ == "__main__":
    main()
