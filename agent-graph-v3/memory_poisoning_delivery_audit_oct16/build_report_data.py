#!/usr/bin/env python3
"""Compute funnel, breakdowns and cross-tabs from mp_delivery_audit.csv.

Writes funnel.json and breakdown.csv. Read-only on benchmark data.
"""
import csv
import json
import os
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def load():
    with open(os.path.join(HERE, "mp_delivery_audit.csv")) as fh:
        return list(csv.DictReader(fh))


def b(x):
    return str(x).strip().lower() in ("true", "1")


def rates(rows):
    """Stage counts and conditional rates for a set of rows."""
    n = len(rows)
    if n == 0:
        return None
    attempted = n
    mutation = sum(1 for r in rows if r["verified_mutation"] == "True")
    mutated = mutation
    # origin event present but no mutation_evidence -> attempt reached injector, mutation unverified
    origin_no_evidence = sum(
        1 for r in rows if r["has_mutation_evidence"] == "False" and r["n_origin_events_trace"] != "0")
    injected_total = sum(1 for r in rows if r["n_origin_events_trace"] not in ("", "0"))
    persisted = sum(1 for r in rows if r["poison_persisted_to_read_window"] == "True")
    retrieved = sum(1 for r in rows if r["poison_record_retrieved"] == "True")
    exposed = sum(1 for r in rows if r["exposed_any_agent"] == "True")
    final_exposed = sum(1 for r in rows if r["exposed_final_agent"] == "True")
    intermediate_only = sum(1 for r in rows if r["exposed_intermediate_only"] == "True")
    propagated = sum(1 for r in rows if r["propagation_occurred"] == "True")
    measured = sum(1 for r in rows if r["propagation_occurred"] in ("True", "False"))
    poison_in_final = sum(1 for r in rows if r["poison_text_in_final_output"] == "True")
    payload_verified = sum(1 for r in rows if r.get("exposed_payload_verified") == "True")
    payload_final = sum(1 for r in rows if r.get("exposed_payload_final_agent") == "True")
    prov_only = sum(1 for r in rows if r.get("exposed_provenance_only") == "True")
    exposed_and_measured = [r for r in rows if r["exposed_any_agent"] == "True"
                            and r["propagation_occurred"] in ("True", "False")]
    exposed_and_prop = sum(1 for r in exposed_and_measured if r["propagation_occurred"] == "True")
    return {
        "attempts": attempted,
        "injection_origin_event_in_trace": injected_total,
        "verified_mutation": mutated,
        "origin_event_without_mutation_evidence": origin_no_evidence,
        "mutated_persisted_to_read_window": persisted,
        "poison_record_retrieved": retrieved,
        "exposed_any_agent": exposed,
        "exposed_intermediate_only": intermediate_only,
        "exposed_final_answer_agent": final_exposed,
        "poison_text_in_final_output": poison_in_final,
        "analyzer_measured_traces": measured,
        "analyzer_propagated": propagated,
        "exposed_and_measured": len(exposed_and_measured),
        "exposed_and_propagated": exposed_and_prop,
        "exposed_payload_verified": payload_verified,
        "exposed_payload_verified_final_agent": payload_final,
        "exposed_provenance_only": prov_only,
    }


def pct(a, b):
    return round(100.0 * a / b, 2) if b else None


def breakdown(rows, group):
    out = []
    groups = defaultdict(list)
    for r in rows:
        groups[r.get(group, "") or ""].append(r)
    for key, grp in sorted(groups.items()):
        d = rates(grp) or {}
        d["group"] = key
        d["group_field"] = group
        out.append(d)
    return out


def main():
    rows = load()

    funnel = {"overall": rates(rows)}
    f = funnel["overall"]
    f["P_verified_mutation_given_attempt"] = pct(f["verified_mutation"], f["attempts"])
    f["P_origin_event_given_attempt"] = pct(f["injection_origin_event_in_trace"], f["attempts"])
    f["P_verified_mutation_given_origin_event"] = pct(
        f["verified_mutation"], f["injection_origin_event_in_trace"])
    f["P_persisted_given_verified_mutation"] = pct(f["mutated_persisted_to_read_window"], f["verified_mutation"])
    f["P_retrieved_given_persisted"] = pct(f["poison_record_retrieved"], f["mutated_persisted_to_read_window"])
    f["P_retrieved_given_verified_mutation"] = pct(f["poison_record_retrieved"], f["verified_mutation"])
    f["P_exposed_given_verified_mutation"] = pct(f["exposed_any_agent"], f["verified_mutation"])
    f["P_final_agent_exposure_given_verified_mutation"] = pct(
        f["exposed_final_answer_agent"], f["verified_mutation"])
    f["P_measured_propagation_given_exposed"] = pct(
        f["exposed_and_propagated"], f["exposed_and_measured"])
    f["P_measured_propagation_given_any_exposure"] = pct(
        f["exposed_and_propagated"], f["exposed_any_agent"])
    f["P_measured_propagation_given_analysis_ready"] = pct(
        f["analyzer_propagated"], f["analyzer_measured_traces"])
    f["P_final_agent_exposure_given_exposed"] = pct(
        f["exposed_final_answer_agent"], f["exposed_any_agent"])
    f["P_payload_verified_exposure_given_verified_mutation"] = pct(
        f["exposed_payload_verified"], f["verified_mutation"])
    f["P_payload_verified_final_exposure_given_verified_mutation"] = pct(
        f["exposed_payload_verified_final_agent"], f["verified_mutation"])
    f["P_payload_verified_final_exposure_given_exposed"] = pct(
        f["exposed_payload_verified_final_agent"], f["exposed_any_agent"])
    f["P_final_answer_agent_reached_given_payload_verified_exposure"] = pct(
        f["exposed_payload_verified_final_agent"], f["exposed_payload_verified"])
    f["P_verified_mutation_given_attempt_with_mutation_evidence"] = pct(
        f["verified_mutation"], f["verified_mutation"] + f["origin_event_without_mutation_evidence"])

    out = {}
    for field in ("task_family", "topology", "propagation_mode", "execution_variant",
                  "poisoning_subtype", "trace_status", "admission_status",
                  "analysis_status", "record_injection_fired",
                  "delivery_outcome", "strict_delivery_outcome",
                  "termination_reason", "poison_payload_kinds", "fired_in_intended_role"):
        out[field] = breakdown(rows, field)

    with open(os.path.join(HERE, "funnel.json"), "w") as fh:
        json.dump(funnel, fh, indent=1)

    fields = ["group_field", "group"] + [k for k in f if k not in ("overall",)]
    rows_out = []
    for lst in out.values():
        for d in lst:
            rows_out.append({k: d.get(k, "") for k in fields})
    with open(os.path.join(HERE, "breakdown.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows_out)

    print(json.dumps(funnel, indent=1))


if __name__ == "__main__":
    main()
