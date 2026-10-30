#!/usr/bin/env python3
"""Corrected, strictly-disjoint delivery funnel for MP.

The naive funnel conflates two different things: an origin write event that is
present in the trace but carries no hidden.mutation_evidence (the production
run predates that field on some cells), and a genuine absence of injection.

This script recomputes tiers with explicit denominators and reports the
mutated+exposed intersection separately from the exposure-only set.
"""
import csv
import json
import os
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
rows = list(csv.DictReader(open(os.path.join(HERE, "mp_delivery_audit.csv"))))


def pct(a, b):
    return round(100.0 * a / b, 2) if b else None


N = len(rows)
verified_mut = [r for r in rows if r["verified_mutation"] == "True"]
origin_event = [r for r in rows if r["n_origin_events_trace"] not in ("", "0")]
mut_ev_missing = [r for r in rows if r["n_origin_events_trace"] not in ("", "0")
                  and r["has_mutation_evidence"] == "False"]
exposed = [r for r in rows if r["poison_record_retrieved"] == "True"]
exposed_payload = [r for r in rows if r["exposed_payload_verified"] == "True"]
exposed_final = [r for r in rows if r["exposed_payload_final_agent"] == "True"]
exposed_inter_only = [r for r in rows if r["exposed_intermediate_only"] == "True"]
mut_and_exposed = [r for r in rows if r["verified_mutation"] == "True"
                   and r["poison_record_retrieved"] == "True"]
evmissing_and_exposed = [r for r in mut_ev_missing if r["poison_record_retrieved"] == "True"]
no_origin = [r for r in rows if r["n_origin_events_trace"] in ("", "0")]
reads_no_poison = [r for r in rows
                   if r["delivery_outcome"] == "read_but_no_poison_returned"]
never_read = [r for r in rows if r["delivery_outcome"] == "mutated_never_read"]

measured = [r for r in rows if r["propagation_occurred"] in ("True", "False")]
prop = [r for r in measured if r["propagation_occurred"] == "True"]
exposed_measured = [r for r in exposed if r["propagation_occurred"] in ("True", "False")]
exposed_prop = [r for r in exposed_measured if r["propagation_occurred"] == "True"]
mut_exp_measured = [r for r in mut_and_exposed if r["propagation_occurred"] in ("True", "False")]
mut_exp_prop = [r for r in mut_exp_measured if r["propagation_occurred"] == "True"]

funnel = {
    "stage_1_attempted": N,
    "stage_1_pct_of_total": 100.0,
    "stage_2_trace_evidence_usable": len(rows) - sum(1 for r in rows if r["trace_status"] == "no_trace_file"),
    "stage_3_injection_origin_event_present": len(origin_event),
    "stage_3b_injection_never_fired": len(no_origin),
    "stage_4_verified_mutation": len(verified_mut),
    "stage_4b_origin_event_without_mutation_evidence": len(mut_ev_missing),
    "stage_5_mutated_persisted_to_read_window":
        sum(1 for r in rows if r["poison_persisted_to_read_window"] == "True"),
    "stage_6_poison_record_retrieved": len(exposed),
    "stage_7_exposed_to_agent_payload_verified": len(exposed_payload),
    "stage_7b_exposed_intermediate_only": len(exposed_inter_only),
    "stage_8_exposed_to_final_answer_agent": len(exposed_final),
    "stage_9_mutated_and_exposed_intersection": len(mut_and_exposed),
    "stage_9b_exposed_without_verified_mutation": len(evmissing_and_exposed),
    "stage_10_analyzer_measured_propagated": len(prop),
    "stage_11_measured_propagation_among_mutated_and_exposed": len(mut_exp_prop),
    "poison_text_verbatim_in_final_output":
        sum(1 for r in rows if r["poison_text_in_final_output"] == "True"),
}

funnel["rates"] = {
    "P_attempted_has_trace_evidence": pct(funnel["stage_2_trace_evidence_usable"], N),
    "P_injection_fired_given_attempt": pct(len(origin_event), N),
    "P_verified_mutation_given_attempt": pct(len(verified_mut), N),
    "P_verified_mutation_given_injection_fired": pct(len(verified_mut), len(origin_event)),
    "P_persisted_given_verified_mutation": pct(
        funnel["stage_5_mutated_persisted_to_read_window"], len(verified_mut)),
    "P_retrieved_given_persisted": pct(len(exposed), funnel["stage_5_mutated_persisted_to_read_window"]),
    "P_retrieved_given_verified_mutation": pct(len(exposed), len(verified_mut)),
    "P_final_agent_exposure_given_verified_mutation": pct(len(exposed_final), len(verified_mut)),
    "P_final_agent_exposure_given_any_exposure": pct(len(exposed_final), len(exposed)),
    "P_measured_propagation_given_any_exposure": pct(len(exposed_prop), len(exposed_measured)),
    "P_measured_propagation_given_mutated_and_exposed":
        pct(len(mut_exp_prop), len(mut_exp_measured)),
    "P_measured_propagation_given_analysis_ready": pct(len(prop), len(measured)),
}

# ---- failure attribution among the non-exposed ----
non_exposed = [r for r in rows if r["poison_record_retrieved"] != "True"]
attr = Counter()
for r in non_exposed:
    if r["n_origin_events_trace"] in ("", "0"):
        tr = r["termination_reason"]
        if tr in ("backend_http_error", "backend_connection_error", "max_events_reached",
                  "protocol_violation"):
            attr["infrastructure_failure_no_injection"] += 1
        elif tr == "injection_target_unreachable":
            attr["injection_target_unreachable"] += 1
        elif tr == "injection_count_mismatch":
            attr["injection_count_mismatch"] += 1
        else:
            attr["no_origin_event_other"] += 1
    elif r["delivery_outcome"] == "mutated_never_read":
        attr["mutated_but_never_read"] += 1
    elif r["delivery_outcome"] == "read_but_no_poison_returned":
        attr["read_but_poison_not_returned"] += 1
    elif r["has_mutation_evidence"] == "False":
        attr["origin_event_mutation_unverified_and_not_retrieved"] += 1
    else:
        attr["other"] += 1
funnel["non_exposed_attribution"] = dict(attr.most_common())
funnel["non_exposed_total"] = len(non_exposed)
funnel["non_exposed_reason"] = {
    "attribution_is_exact_partition": sum(attr.values()) == len(non_exposed),
}

with open(os.path.join(HERE, "funnel_corrected.json"), "w") as fh:
    json.dump(funnel, fh, indent=1)

print(json.dumps(funnel, indent=1))
