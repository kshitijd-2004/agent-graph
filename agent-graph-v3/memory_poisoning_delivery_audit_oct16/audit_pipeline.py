#!/usr/bin/env python3
"""Memory-poisoning delivery/exposure audit for the Oct-16 production run.

Read-only with respect to all benchmark data, source code and prior outputs.

Evidence tiers used (never conflated):
  T1 INJECTION ATTEMPT  - record / admission row says this attempt was MP.
  T2 MUTATION            - event.hidden.mutation_evidence with before != after.
  T3 PERSISTED           - the mutated write precedes at least one later read.
  T4 RETRIEVED           - a memory_retrieval event whose returned payload is
                           linked to an origin write (hidden.delivered_origins,
                           event_labels.receives_perturbed_info, depends_on,
                           or literal payload match).
  T5 EXPOSED            - T4 retrieval by an LLM agent, and the payload text is
                           present (possibly truncated) in the returned value.
  T6 FINAL-AGENT        - T5 retrieval by the final_response agent, or a
                           handoff payload to it containing the poisoned text.
  T7 PROPAGATED         - analyzer's propagation_occurred for the trace.

Emits mp_delivery_audit.csv and funnel.json.
"""
from __future__ import annotations

import csv
import glob
import json
import os
from collections import Counter, defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PR = os.path.join(REPO, "production_run")
PRELIM = os.path.join(REPO, "preliminary_analysis_oct16")
OUT = os.path.dirname(os.path.abspath(__file__))
MP_LEP = "LEP_MEMORY_POISONING"


def load_jsonl(path):
    out = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def rel(path):
    return os.path.relpath(path, REPO).replace(os.sep, "/")


def classify_payload(text):
    t = (text or "").strip()
    if t.startswith("Review of"):
        return "template_defect_absence"
    if "PRELIMINARY and should not be used" in t:
        return "legacy_stale_marker"
    if t.startswith("Verified:"):
        return "verified_wrong_fact"
    return "other_false_fact" if t else "empty"


def analyze_trace(path, status):
    """Return a dict of delivery/exposure facts for one trace JSON."""
    with open(path) as fh:
        d = json.load(fh)
    m = d.get("metadata") or {}
    evs = d.get("events") or []

    f = {
        "trace_path": rel(path),
        "trace_id": d.get("trace_id"),
        "status": status,
        "fixture_id": m.get("fixture_id"),
        "task_family": m.get("task_family"),
        "topology": m.get("topology"),
        "propagation_mode": m.get("propagation_mode"),
        "execution_variant": m.get("execution_variant") or d.get("variant"),
        "poisoning_subtype": m.get("poisoning_subtype") or "",
        "memory_protocol": m.get("memory_protocol") or "",
        "final_agent_role": m.get("final_agent_role"),
        "final_stage": m.get("final_stage"),
        "termination_reason": m.get("termination_reason"),
        "schema_version": d.get("schema_version"),
        "num_events": d.get("num_events"),
        "n_stages": m.get("stage_count"),
    }

    # ---- origin / mutation evidence ----
    # stage_runner.py:1091-1095 retains the agent's ORIGINAL tool arguments on
    # the paired tool_call event and overwrites the memory_write event's
    # tool_arguments["value"] with the stored value. Comparing the two gives a
    # before/after pair independent of the trace-level mutation_evidence block,
    # which is absent on traces written before generation/admission.py added it.
    tool_call_values = {}
    for e in evs:
        if e.get("event_type") == "tool_call" and e.get("tool_call_id"):
            tool_call_values[e.get("tool_call_id")] = (
                (e.get("tool_arguments") or {}).get("value"))
    origin_events = {}          # event_id -> info
    poisoned = {}              # key -> dict(value, key, event_id, desc)
    for e in evs:
        hid = e.get("hidden") or {}
        lab = e.get("event_labels") or {}
        me = hid.get("mutation_evidence")
        if not (lab.get("is_injection_origin") or hid.get("injected") is True or me):
            continue
        args = e.get("tool_arguments") or {}
        key = args.get("key")
        val = args.get("value") or ""
        inj = hid.get("lep_injection") or {}
        info = {
            "event_id": e.get("event_id"),
            "event_index": e.get("event_index"),
            "agent_id": e.get("agent_id"),
            "agent_role": e.get("agent_role"),
            "key": key,
            "value": val,
            "has_mutation_evidence": bool(me),
            "before": (me or {}).get("before", ""),
            "after": (me or {}).get("after", ""),
            "before_eq_after": bool(me) and (me or {}).get("before") == (me or {}).get("after"),
            "independent_before": tool_call_values.get(e.get("tool_call_id")),
            "target_agent_intended": inj.get("target_agent_intended"),
            "fired_in_intended_role": inj.get("fired_in_intended_role"),
            "payload_kind": classify_payload(val),
            "stores_perturbed_info": bool(lab.get("stores_perturbed_info")),
        }
        origin_events[e.get("event_id")] = info
        if key is not None:
            poisoned[key] = {
                "value": val,
                "key": key,
                "event_id": e.get("event_id"),
                "kind": info["payload_kind"],
                "agent_role": e.get("agent_role"),
            }
    f["origin_events"] = origin_events

    # ---- final-answer agent ----
    final_agents = set()
    final_ids = []
    for e in evs:
        if e.get("event_type") == "final_response":
            final_agents.add(e.get("agent_id"))
            final_ids.append(e.get("event_id"))
            f["final_output"] = (e.get("output_text") or "")
    f["final_agents"] = final_agents
    f["final_event_ids"] = final_ids

    # ---- retrievals ----
    f["reads"] = []
    all_reads = []
    for e in evs:
        if e.get("event_type") != "memory_retrieval":
            continue
        all_reads.append(e)
        args = e.get("tool_arguments") or {}
        out = e.get("output_text") or ""
        hid = e.get("hidden") or {}
        lab = e.get("event_labels") or {}
        deps = e.get("depends_on") or []
        delivered = hid.get("delivered_origins") or []
        receives = bool(lab.get("receives_perturbed_info"))

        matched = []
        prov_matched = []
        payload_matched = []
        for orig_id, oi in origin_events.items():
            val = oi["value"]
            link_prov = orig_id in delivered or orig_id in deps
            link_payload = bool(
                val and (val[:60] in out or val[:60] in (e.get("tool_result") or "")))
            if link_prov or link_payload:
                matched.append(orig_id)
            if link_prov:
                prov_matched.append(orig_id)
            if link_payload:
                payload_matched.append(orig_id)

        role = e.get("agent_role")
        rec = {
            "event_id": e.get("event_id"),
            "event_index": e.get("event_index"),
            "agent_id": e.get("agent_id"),
            "agent_role": role,
            "query": args.get("query"),
            "top_k": args.get("top_k"),
            "output_len": len(out),
            "delivered_origins": list(delivered),
            "receives_perturbed_info": receives,
            "depends_on": list(deps),
            "poisoned_matched": list(set(matched)),
            "provenance_matched": list(set(prov_matched)),
            "payload_matched": list(set(payload_matched)),
            "output_excerpt": out[:220],
            "is_final_agent": e.get("agent_id") in final_agents,
            "n_origin_events_in_trace": len(origin_events),
        }
        f["reads"].append(rec)

    # ---- ordering: reads before any poison write ----
    first_poison_index = min(
        [oi["event_index"] for oi in origin_events.values() if oi["event_index"] is not None],
        default=None,
    )
    f["first_poison_write_index"] = first_poison_index
    f["reads_before_first_poison_write"] = sum(
        1 for r in f["reads"]
        if r["event_index"] is not None and first_poison_index is not None
        and r["event_index"] < first_poison_index
    )

    # ---- handoffs carrying poison text ----
    f["handoffs_to_final_with_poison"] = []
    f["handoffs_with_poison"] = []
    for e in evs:
        if e.get("event_type") != "agent_handoff":
            continue
        txt = (e.get("output_text") or "") + " " + (e.get("tool_result") or "")
        for oi in origin_events.values():
            if oi["value"] and oi["value"][:60] in txt:
                ent = {
                    "event_id": e.get("event_id"),
                    "from": e.get("source_entity_id"),
                    "to": e.get("target_entity_id"),
                    "to_is_final_agent": e.get("target_entity_id") in final_agents,
                    "poison_origin": oi["event_id"],
                }
                f["handoffs_with_poison"].append(ent)
                if ent["to_is_final_agent"]:
                    f["handoffs_to_final_with_poison"].append(ent)

    # ---- derived tiers ----
    real_mut = {k: v for k, v in origin_events.items()
                if v["has_mutation_evidence"] and not v["before_eq_after"]}
    any_mut = {k: v for k, v in origin_events.items() if v["has_mutation_evidence"]}
    # Where the paired tool_call value is recorded AND differs from the stored
    # value. This needs no admission contract at all.
    independent = {k: v for k, v in origin_events.items()
                   if v["independent_before"] is not None
                   and v["independent_before"] != v["value"]}
    # Union: a mutation is verified by either the trace-level
    # mutation_evidence block or by an independent before/after pair on the
    # paired tool_call/memory_write events. The 284 traces without a
    # mutation_evidence block were written before generation/admission.py
    # gained it; for those, label_injection (:1081) and the tool_arguments
    # overwrite (:1095) both sit behind write_was_poisoned (:1074), which
    # requires poison_result.fired == (value != original_value).
    real_mut_union = real_mut | independent
    f["n_origin_events"] = len(origin_events)
    f["n_mutated_verified"] = len(real_mut)
    f["n_mutated_independent_only"] = len(independent) - len(real_mut & independent)
    f["n_mutated_verified_union"] = len(real_mut_union)
    f["n_mutated_any"] = len(any_mut)
    f["verified_mutation"] = bool(real_mut_union)
    f["verified_mutation_contract_only"] = bool(real_mut)
    f["mutation_evidence_present"] = bool(any_mut)
    f["poisoned_keys"] = sorted(poisoned)
    f["poison_payload_kinds"] = sorted({v["payload_kind"] for v in origin_events.values()})
    f["injection_fired_flag"] = bool(origin_events)

    reads_after_write = [
        r for r in f["reads"]
        if r["event_index"] is None or first_poison_index is None
        or r["event_index"] >= first_poison_index
    ]
    f["n_reads_after_write"] = len(reads_after_write)
    f["poison_persisted_to_read_window"] = bool(real_mut_union) and bool(reads_after_write)

    retrieved = [r for r in reads_after_write if r["poisoned_matched"]]
    retrieved_payload = [r for r in retrieved if r["payload_matched"]]
    retrieved_prov_only = [r for r in retrieved if r["provenance_matched"] and not r["payload_matched"]]
    f["n_poison_reads"] = len(retrieved)
    f["n_poison_reads_payload_verified"] = len(retrieved_payload)
    f["n_poison_reads_provenance_only"] = len(retrieved_prov_only)
    f["poison_record_retrieved"] = bool(retrieved)
    f["poison_record_retrieved_payload_verified"] = bool(retrieved_payload)
    f["retrieved_event_ids"] = [r["event_id"] for r in retrieved]
    f["retrieved_agents"] = sorted({r["agent_id"] for r in retrieved if r["agent_id"]})
    f["retrieved_roles"] = sorted({r["agent_role"] for r in retrieved if r["agent_role"]})
    f["n_distinct_retrievers"] = len(f["retrieved_agents"])

    # T5/T6
    final_direct = any(r["is_final_agent"] for r in retrieved)
    indirect = bool(f["handoffs_with_poison"])
    f["exposed_any_agent"] = bool(retrieved)
    f["exposed_final_agent_direct"] = final_direct
    f["exposed_final_agent_via_handoff"] = bool(f["handoffs_to_final_with_poison"])
    f["exposed_final_agent"] = bool(final_direct or f["handoffs_to_final_with_poison"])
    f["exposed_intermediate_only"] = bool(retrieved) and not final_direct and not indirect
    f["retrieved_final_agent_via_handoff_only"] = bool(f["handoffs_to_final_with_poison"]) \
        and not final_direct
    # Did the poison text survive into the final answer text?
    f["poison_text_in_final_output"] = any(
        oi["value"] and oi["value"][:40] in (f.get("final_output") or "")
        for oi in origin_events.values()
    )
    f["n_agents_total"] = len({e.get("agent_id") for e in evs if e.get("agent_id")})

    # ---- classification into disjoint delivery outcomes ----
    # delivery_outcome is driven by verified_mutation (union), i.e. whether a
    # real value change can be established by any means.
    # mutation_evidence_status keeps the contract-only signal visible
    # separately, because it carries an extra admission guarantee.
    if not origin_events:
        f["mutation_evidence_status"] = "no_origin_event"
    elif not real_mut_union:
        f["mutation_evidence_status"] = "no_value_change_established"
    elif not real_mut:
        f["mutation_evidence_status"] = "independent_verified_only"
    else:
        f["mutation_evidence_status"] = "contract_verified"

    if not origin_events:
        f["delivery_outcome"] = "no_mutation_no_origin_event"
    elif not real_mut_union:
        f["delivery_outcome"] = "no_mutation_no_origin_event"
    elif not reads_after_write:
        f["delivery_outcome"] = "mutated_never_read"
    elif not retrieved:
        f["delivery_outcome"] = "read_but_no_poison_returned"
    elif final_direct or f["handoffs_to_final_with_poison"]:
        f["delivery_outcome"] = "exposed_final_answer_agent"
    else:
        f["delivery_outcome"] = "exposed_intermediate_only"

    # Strict payload-verified tiers: the returned value's own text must contain
    # the poisoned value's prefix, so a truncated or wrong version cannot pass.
    f["exposed_payload_verified"] = bool(retrieved_payload)
    f["exposed_payload_final_agent"] = any(r["is_final_agent"] for r in retrieved_payload)
    f["exposed_payload_intermediate_only"] = bool(retrieved_payload) and \
        not f["exposed_payload_final_agent"] and not f["handoffs_to_final_with_poison"]
    f["exposed_provenance_only"] = bool(retrieved) and not bool(retrieved_payload)
    if f["exposed_payload_verified"]:
        f["strict_delivery_outcome"] = (
            "payload_verified_final_answer_agent"
            if f["exposed_payload_final_agent"] else "payload_verified_intermediate_only")
    elif f["exposed_provenance_only"]:
        f["strict_delivery_outcome"] = "exposure_provenance_only_payload_unverified"
    else:
        f["strict_delivery_outcome"] = f["delivery_outcome"]

    f["labels"] = dict(d.get("labels") or {})
    return f


def main():
    records = []
    for p in sorted(glob.glob(os.path.join(PR, "node_*", "benchmark_records.jsonl"))):
        records.extend(load_jsonl(p))
    mp_records = [r for r in records if r.get("lep_code") == MP_LEP]

    adm = load_jsonl(os.path.join(PRELIM, "admission_attempts.jsonl"))
    pm = load_jsonl(os.path.join(PRELIM, "propagation_metrics.jsonl"))
    adm_by_trace = {a.get("trace_path"): a for a in adm if a.get("trace_path")}
    pm_by_trace = {r.get("trace_id"): r for r in pm if r.get("lep_code") == MP_LEP}

    # ---- trace evidence: accepted + rejected trace JSONs ----
    trace_files = [
        rel(p) for p in sorted(
            glob.glob(os.path.join(PR, "node_*", "traces", "*_trace.json"))
            + glob.glob(os.path.join(PR, "node_*", "rejected_traces", "*_trace.json"))
        )
        if MP_LEP in p
    ]
    facts = {}
    parse_fail = []
    for p in trace_files:
        status = "rejected_trace" if "/rejected_traces/" in p else "accepted_trace"
        ap = os.path.join(REPO, p) if not p.startswith("/") else p
        try:
            facts[p] = analyze_trace(ap, status)
        except Exception as exc:  # noqa: BLE001
            parse_fail.append((rel(p), str(exc)))

    # ---- join on records (the attempt ledger) ----
    rows = []
    for r in mp_records:
        f = facts.get(r.get("trace_path"))
        rec_path = r.get("trace_path") or ""
        a = adm_by_trace.get(rec_path) or {}
        pmr = pm_by_trace.get(r.get("trace_id")) or {}
        if f is None:
            rows.append({
                "source": "record_only",
                "record": r, "facts": None, "adm": a, "pm": pmr,
                "trace_path": rec_path,
            })
        else:
            rows.append({
                "source": "record+trace",
                "record": r, "facts": f, "adm": a, "pm": pmr,
                "trace_path": rec_path,
            })

    orphan_facts = {k: v for k, v in facts.items() if not any(
        x["trace_path"] == k for x in rows)}

    return {
        "records": records, "mp_records": mp_records, "adm": adm, "pm": pm,
        "facts": facts, "rows": rows, "trace_files": trace_files,
        "parse_fail": parse_fail, "orphan_facts": orphan_facts,
    }


CSV_FIELDS = [
    "node", "trace_id", "trace_path", "trace_status", "record_status", "attribution",
    "fixture_id", "task_family", "topology", "propagation_mode", "execution_variant",
    "poisoning_subtype", "memory_protocol", "schema_version",
    "final_agent_role", "final_stage", "termination_reason",
    "admission_status", "admission_reason", "analysis_status", "num_benign_reference_runs",
    "n_events", "n_agents", "n_reads_total",
    "record_injection_fired", "record_reached_target", "n_injection_event_ids_record",
    "n_storage_event_ids_record", "n_delivery_event_ids_record", "n_consumption_event_ids_record",
    "n_origin_events_trace", "n_mutated_verified", "has_mutation_evidence",
    "mutation_evidence_status", "n_mutated_verified_union", "n_mutated_independent_only",
    "verified_mutation", "verified_mutation_contract_only",
    "poisoned_keys", "poison_payload_kinds",
    "target_agent_intended", "agent_role_actual", "fired_in_intended_role",
    "first_poison_write_index", "n_reads_total", "reads_before_first_poison_write",
    "n_reads_after_write", "poison_persisted_to_read_window",
    "poison_record_retrieved", "n_poison_reads", "retrieved_event_ids",
    "retrieved_roles", "n_distinct_retrievers", "retrieved_agent_ids",
    "exposed_any_agent", "exposed_intermediate_only", "exposed_final_agent",
    "exposed_final_agent_direct", "exposed_final_agent_via_handoff",
    "poison_text_in_final_output", "delivery_outcome", "strict_delivery_outcome", "exposed_payload_verified", "exposed_payload_final_agent", "exposed_provenance_only", "n_poison_reads_payload_verified",
    "propagation_occurred", "anomalous_event_count", "propagation_depth",
    "lep_exposed_label", "lep_consumed_label", "lep_propagated_label",
    "memory_boundary_count", "downstream_failure", "task_success", "evaluator_passed",
    "trace_path_readable", "uncertainty_reason",
]


def write_csv(rows, out_path):
    out = []
    for x in rows:
        r = x["record"] or {}
        f = x["facts"]
        a = x["adm"] or {}
        pmr = x["pm"] or {}
        if f:
            origins = f["origin_events"]
            targs = sorted({str(v.get("target_agent_intended")) for v in origins.values()})
            acts = sorted({str(v.get("agent_role")) for v in origins.values()})
            fir = sorted({str(v.get("fired_in_intended_role")) for v in origins.values()})
            row = {
                "node": f["trace_path"].split("/")[-3],
                "trace_id": f["trace_id"],
                "trace_path": f["trace_path"],
                "trace_status": f["status"],
                "record_status": "admitted_record",
                "attribution": x["source"],
                "fixture_id": f["fixture_id"],
                "task_family": f["task_family"],
                "topology": f["topology"],
                "propagation_mode": f["propagation_mode"],
                "execution_variant": f["execution_variant"],
                "poisoning_subtype": f["poisoning_subtype"],
                "memory_protocol": f["memory_protocol"],
                "schema_version": f["schema_version"],
                "final_agent_role": f["final_agent_role"],
                "final_stage": f["final_stage"],
                "termination_reason": f["termination_reason"],
                "admission_status": a.get("status", ""),
                "admission_reason": a.get("reason", ""),
                "analysis_status": a.get("analysis_status", ""),
                "num_benign_reference_runs": pmr.get("num_benign_reference_runs", ""),
                "n_events": f["num_events"],
                "n_reads_total": len(f["reads"]),
                "n_agents": f["n_agents_total"],
                "record_injection_fired": r.get("injection_fired"),
                "record_reached_target": r.get("perturbation_reached_target"),
                "n_injection_event_ids_record": len(r.get("injection_event_ids") or []),
                "n_storage_event_ids_record": len(r.get("storage_event_ids") or []),
                "n_delivery_event_ids_record": len(r.get("delivery_event_ids") or []),
                "n_consumption_event_ids_record": len(r.get("consumption_event_ids") or []),
                "n_origin_events_trace": f["n_origin_events"],
                "n_mutated_verified": f["n_mutated_verified"],
                "has_mutation_evidence": f["mutation_evidence_present"],
                "mutation_evidence_status": f["mutation_evidence_status"],
                "n_mutated_verified_union": f["n_mutated_verified_union"],
                "n_mutated_independent_only": f["n_mutated_independent_only"],
                "verified_mutation": f["verified_mutation"],
                "verified_mutation_contract_only": f["verified_mutation_contract_only"],
                "poisoned_keys": "|".join(f["poisoned_keys"]),
                "poison_payload_kinds": "|".join(f["poison_payload_kinds"]),
                "target_agent_intended": "|".join(targs),
                "agent_role_actual": "|".join(acts),
                "fired_in_intended_role": "|".join(fir),
                "first_poison_write_index": f["first_poison_write_index"] if f["first_poison_write_index"] is not None else "",
                "n_reads_total": len(f["reads"]),
                "reads_before_first_poison_write": f["reads_before_first_poison_write"],
                "n_reads_after_write": f["n_reads_after_write"],
                "poison_persisted_to_read_window": f["poison_persisted_to_read_window"],
                "poison_record_retrieved": f["poison_record_retrieved"],
                "n_poison_reads": f["n_poison_reads"],
                "retrieved_event_ids": "|".join(str(v) for v in f["retrieved_event_ids"]),
                "retrieved_roles": "|".join(f["retrieved_roles"]),
                "n_distinct_retrievers": f["n_distinct_retrievers"],
                "retrieved_agent_ids": "|".join(f["retrieved_agents"]),
                "exposed_any_agent": f["exposed_any_agent"],
                "exposed_intermediate_only": f["exposed_intermediate_only"],
                "exposed_final_agent": f["exposed_final_agent"],
                "exposed_final_agent_direct": f["exposed_final_agent_direct"],
                "exposed_final_agent_via_handoff": f["exposed_final_agent_via_handoff"],
                "poison_text_in_final_output": f["poison_text_in_final_output"],
                "delivery_outcome": f["delivery_outcome"],
                "strict_delivery_outcome": f["strict_delivery_outcome"],
                "exposed_payload_verified": f["exposed_payload_verified"],
                "exposed_payload_final_agent": f["exposed_payload_final_agent"],
                "exposed_provenance_only": f["exposed_provenance_only"],
                "n_poison_reads_payload_verified": f["n_poison_reads_payload_verified"],
                "propagation_occurred": pmr.get("propagation_occurred", ""),
                "anomalous_event_count": pmr.get("anomalous_event_count", ""),
                "propagation_depth": pmr.get("event_depth", ""),
                "lep_exposed_label": f["labels"].get("lep_exposed", ""),
                "lep_consumed_label": f["labels"].get("lep_consumed", ""),
                "lep_propagated_label": f["labels"].get("lep_propagated", ""),
                "memory_boundary_count": f["labels"].get("memory_boundary_count", ""),
                "downstream_failure": r.get("downstream_failure"),
                "task_success": r.get("task_success"),
                "evaluator_passed": r.get("evaluator_passed"),
                "trace_path_readable": True,
                "uncertainty_reason": "",
            }
        else:
            row = {k: "" for k in CSV_FIELDS}
            row.update({
                "node": (r.get("trace_path") or "").split("/")[-3],
                "trace_id": r.get("trace_id"),
                "trace_path": r.get("trace_path"),
                "trace_status": "no_trace_file",
                "record_status": "admitted_record",
                "attribution": "record_only",
                "fixture_id": r.get("fixture_id", ""),
                "task_family": r.get("task_family"),
                "topology": r.get("topology"),
                "propagation_mode": r.get("propagation_mode"),
                "execution_variant": r.get("execution_variant"),
                "termination_reason": r.get("termination_reason"),
                "admission_status": a.get("status", ""),
                "admission_reason": a.get("reason", ""),
                "analysis_status": a.get("analysis_status", ""),
                "num_benign_reference_runs": pmr.get("num_benign_reference_runs", ""),
                "record_injection_fired": r.get("injection_fired"),
                "record_reached_target": r.get("perturbation_reached_target"),
                "n_injection_event_ids_record": len(r.get("injection_event_ids") or []),
                "n_storage_event_ids_record": len(r.get("storage_event_ids") or []),
                "n_delivery_event_ids_record": len(r.get("delivery_event_ids") or []),
                "n_consumption_event_ids_record": len(r.get("consumption_event_ids") or []),
                "propagation_occurred": pmr.get("propagation_occurred", ""),
                "downstream_failure": r.get("downstream_failure"),
                "task_success": r.get("task_success"),
                "evaluator_passed": r.get("evaluator_passed"),
                "trace_path_readable": False,
                "uncertainty_reason": "trace_json_absent",
            })
        # uncertainty flags
        if f:
            u = []
            if f["delivery_outcome"] == "origin_event_no_mutation_evidence":
                u.append("origin_labeled_but_no_mutation_evidence_in_trace")
            if f["delivery_outcome"] in ("read_but_no_poison_returned", "exposed_intermediate_only") \
                    and f["exposed_intermediate_only"]:
                u.append("")
            if f["delivery_outcome"] == "read_but_no_poison_returned":
                u.append("reads_exist_but_no_poison_linking")
            if f["delivery_outcome"] == "mutated_never_read":
                u.append("mutated_no_read_events")
            if len(f["reads"]) == 0:
                u.append("no_memory_reads_at_all")
            if r.get("termination_reason") in ("backend_http_error", "backend_connection_error",
                                               "max_events_reached", "protocol_violation",
                                               "injection_count_mismatch"):
                u.append("run_not_completed")
            if not f["exposed_any_agent"] and f["delivery_outcome"] not in (
                    "no_mutation_no_origin_event", "mutated_never_read",
                    "read_but_no_poison_returned", "origin_event_no_mutation_evidence"):
                u.append("none")
            row["uncertainty_reason"] = "|".join(x for x in u if x)
        out.append(row)
    with open(out_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(out)
    return out


if __name__ == "__main__":
    data = main()
    out = write_csv(data["rows"], os.path.join(OUT, "mp_delivery_audit.csv"))
    json.dump({
        "n_records_total": len(data["records"]),
        "n_mp_records": len(data["mp_records"]),
        "n_trace_files_mp": len(data["trace_files"]),
        "n_rows": len(out),
        "parse_failures": data["parse_fail"],
        "orphan_trace_paths": list(data["orphan_facts"])[:50],
        "n_orphan_traces": len(data["orphan_facts"]),
    }, open(os.path.join(OUT, "pipeline_counts.json"), "w"), indent=1)
    print("rows:", len(out), "mp records:", len(data["mp_records"]),
          "mp trace files:", len(data["trace_files"]),
          "parse failures:", len(data["parse_fail"]),
          "orphan traces:", len(data["orphan_facts"]))
    c = Counter((x["facts"] or {}).get("delivery_outcome") if x["facts"] else "no_trace_file"
                for x in data["rows"])
    print("delivery_outcome:", dict(c))
