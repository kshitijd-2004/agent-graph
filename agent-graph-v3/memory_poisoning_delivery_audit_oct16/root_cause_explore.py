import csv, json, os
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
rows = list(csv.DictReader(open(os.path.join(HERE, "mp_delivery_audit.csv"))))

print("=== A. no_mutation_no_origin_event (1523) ===")
sub = [r for r in rows if r["delivery_outcome"] == "no_mutation_no_origin_event"]
print("by termination_reason:", Counter(r["termination_reason"] for r in sub).most_common())
print("by task_family:", Counter(r["task_family"] for r in sub).most_common())
print("by topology:", Counter(r["topology"] for r in sub).most_common())
print("record_injection_fired:", Counter(r["record_injection_fired"] for r in sub).most_common())
print("n_reads_total dist:", Counter(r["n_reads_total"] for r in sub).most_common(6))

print("\n=== B. read_but_no_poison_returned (477) ===")
sub = [r for r in rows if r["delivery_outcome"] == "read_but_no_poison_returned"]
print("n:", len(sub))
print("by task_family:", Counter(r["task_family"] for r in sub).most_common())
print("by topology:", Counter(r["topology"] for r in sub).most_common())
print("n_origin_events_trace:", Counter(r["n_origin_events_trace"] for r in sub).most_common())
print("n_reads_after_write:", Counter(r["n_reads_after_write"] for r in sub).most_common(8))
print("first_poison_write_index quartiles:",
      sorted(int(r["first_poison_write_index"]) for r in sub if r["first_poison_write_index"])[:3],
      sorted(int(r["first_poison_write_index"]) for r in sub if r["first_poison_write_index"])[-3:])
print("poisoned_keys sample:", Counter(r["poisoned_keys"] for r in sub).most_common(8))

print("\n=== C. mutated_never_read (105) ===")
sub = [r for r in rows if r["delivery_outcome"] == "mutated_never_read"]
print("n:", len(sub))
print("trace_status:", Counter(r["trace_status"] for r in sub).most_common())
print("by topology:", Counter(r["topology"] for r in sub).most_common())
print("by task_family:", Counter(r["task_family"] for r in sub).most_common())
print("termination_reason:", Counter(r["termination_reason"] for r in sub).most_common())
print("reads_before_first_poison_write:", Counter(r["reads_before_first_poison_write"] for r in sub).most_common())
print("final_agent_role:", Counter(r["final_agent_role"] for r in sub).most_common())

print("\n=== D. origin_event_no_mutation_evidence (75 strict) ===")
sub = [r for r in rows if r["strict_delivery_outcome"] == "origin_event_no_mutation_evidence"]
print("n:", len(sub))
print("termination_reason:", Counter(r["termination_reason"] for r in sub).most_common())
print("by topology:", Counter(r["topology"] for r in sub).most_common())
print("by task_family:", Counter(r["task_family"] for r in sub).most_common())
print("payload kinds:", Counter(r["poison_payload_kinds"] for r in sub).most_common())

print("\n=== E. exposed outcomes ===")
sub = [r for r in rows if r["delivery_outcome"] in ("exposed_final_answer_agent", "exposed_intermediate_only")]
print("n:", len(sub))
print("by task_family:", Counter(r["task_family"] for r in sub).most_common())
print("by topology:", Counter(r["topology"] for r in sub).most_common())
print("retrieved_roles:", Counter(r["retrieved_roles"] for r in sub).most_common(10))
print("propagation_occurred:", Counter(r["propagation_occurred"] for r in sub).most_common())
print("downstream_failure:", Counter(r["downstream_failure"] for r in sub).most_common())
print("task_success:", Counter(r["task_success"] for r in sub).most_common())
print("poison_payload_kinds:", Counter(r["poison_payload_kinds"] for r in sub).most_common())
print("analysis_status:", Counter(r["analysis_status"] for r in sub).most_common())

print("\n=== F. fired_in_intended_role ===")
print(Counter(r["fired_in_intended_role"] for r in rows if r["fired_in_intended_role"]).most_common())
print("by family:", {tf: Counter(r["fired_in_intended_role"] for r in rows
      if r["task_family"] == tf and r["fired_in_intended_role"]).most_common()
      for tf in ("code_review", "financial_analysis", "research_synthesis")})
