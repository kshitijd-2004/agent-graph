# ID/TRC v2 offline production comparison

Measured population: 10395. HC/IPI/MP metric rows are identical: 6159. No eligibility changes.

**Interpretation:** mixed-detector candidate detection fractions, not independently validated propagation probabilities. Unknown and incomparable cases remain in the measured denominator; neither is a negative. “Not detected” final impact means restoration of the recognized boundary assertions, not independently judged answer correctness.

## All LEPs

| lep_code | measured | old_positive | new_positive | old_rate_pct | new_rate_pct | added | removed | negative | unknown | incomparable |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| LEP_HANDOFF_CORRUPTION | 2319 | 60 | 60 | 2.5873 | 2.5873 | 0 | 0 | 825 | 1434 | 0 |
| LEP_INDIRECT_PROMPT_INJECTION | 1933 | 255 | 255 | 13.1919 | 13.1919 | 0 | 0 | 251 | 1427 | 0 |
| LEP_INPUT_DISREGARD | 2323 | 656 | 74 | 28.2393 | 3.1855 | 0 | 582 | 1628 | 147 | 474 |
| LEP_MEMORY_POISONING | 1907 | 23 | 23 | 1.2061 | 1.2061 | 0 | 0 | 1884 | 0 | 0 |
| LEP_TOOL_RESULT_CORRUPTION | 1913 | 574 | 650 | 30.0052 | 33.978 | 82 | 6 | 108 | 1155 | 0 |

## Priority configurations

| task_family | topology | propagation_mode | lep_code | measured | old_positive | new_positive | old_rate_pct | new_rate_pct | added | removed | negative | unknown | incomparable |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| code_review | coordinator_workers | one_to_many | LEP_INPUT_DISREGARD | 70 | 70 | 0 | 100.0 | 0.0 | 0 | 70 | 0 | 0 | 70 |
| financial_analysis | coordinator_workers | one_to_many | LEP_INPUT_DISREGARD | 205 | 205 | 0 | 100.0 | 0.0 | 0 | 205 | 0 | 0 | 205 |
| research_synthesis | coordinator_workers | one_to_many | LEP_INPUT_DISREGARD | 184 | 184 | 0 | 100.0 | 0.0 | 0 | 184 | 0 | 0 | 184 |
| financial_analysis | coordinator_workers | one_to_many | LEP_TOOL_RESULT_CORRUPTION | 169 | 16 | 69 | 9.4675 | 40.8284 | 53 | 0 | 49 | 51 | 0 |
| financial_analysis | coordinator_workers | single_origin | LEP_TOOL_RESULT_CORRUPTION | 169 | 117 | 129 | 69.2308 | 76.3314 | 12 | 0 | 17 | 23 | 0 |

## All ID/TRC configurations

| task_family | topology | propagation_mode | lep_code | measured | old_positive | new_positive | old_rate_pct | new_rate_pct | added | removed | negative | unknown | incomparable |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| code_review | branch_and_verify | many_to_one | LEP_INPUT_DISREGARD | 85 | 2 | 0 | 2.3529 | 0.0 | 0 | 2 | 83 | 2 | 0 |
| code_review | branch_and_verify | single_origin | LEP_INPUT_DISREGARD | 85 | 3 | 0 | 3.5294 | 0.0 | 0 | 3 | 82 | 3 | 0 |
| code_review | coordinator_workers | one_to_many | LEP_INPUT_DISREGARD | 70 | 70 | 0 | 100.0 | 0.0 | 0 | 70 | 0 | 0 | 70 |
| code_review | coordinator_workers | single_origin | LEP_INPUT_DISREGARD | 70 | 20 | 6 | 28.5714 | 8.5714 | 0 | 14 | 42 | 7 | 15 |
| code_review | review_loop | single_origin | LEP_INPUT_DISREGARD | 73 | 11 | 5 | 15.0685 | 6.8493 | 0 | 6 | 60 | 8 | 0 |
| financial_analysis | branch_and_verify | many_to_one | LEP_INPUT_DISREGARD | 216 | 10 | 0 | 4.6296 | 0.0 | 0 | 10 | 206 | 10 | 0 |
| financial_analysis | branch_and_verify | single_origin | LEP_INPUT_DISREGARD | 216 | 14 | 0 | 6.4815 | 0.0 | 0 | 14 | 202 | 14 | 0 |
| financial_analysis | coordinator_workers | one_to_many | LEP_INPUT_DISREGARD | 205 | 205 | 0 | 100.0 | 0.0 | 0 | 205 | 0 | 0 | 205 |
| financial_analysis | coordinator_workers | single_origin | LEP_INPUT_DISREGARD | 205 | 34 | 18 | 16.5854 | 8.7805 | 0 | 16 | 171 | 16 | 0 |
| financial_analysis | review_loop | single_origin | LEP_INPUT_DISREGARD | 218 | 23 | 12 | 10.5505 | 5.5046 | 0 | 11 | 177 | 29 | 0 |
| research_synthesis | branch_and_verify | many_to_one | LEP_INPUT_DISREGARD | 164 | 23 | 13 | 14.0244 | 7.9268 | 0 | 10 | 141 | 10 | 0 |
| research_synthesis | branch_and_verify | single_origin | LEP_INPUT_DISREGARD | 164 | 18 | 9 | 10.9756 | 5.4878 | 0 | 9 | 146 | 9 | 0 |
| research_synthesis | coordinator_workers | one_to_many | LEP_INPUT_DISREGARD | 184 | 184 | 0 | 100.0 | 0.0 | 0 | 184 | 0 | 0 | 184 |
| research_synthesis | coordinator_workers | single_origin | LEP_INPUT_DISREGARD | 184 | 39 | 11 | 21.1957 | 5.9783 | 0 | 28 | 145 | 28 | 0 |
| research_synthesis | review_loop | single_origin | LEP_INPUT_DISREGARD | 184 | 0 | 0 | 0.0 | 0.0 | 0 | 0 | 173 | 11 | 0 |
| code_review | branch_and_verify | many_to_one | LEP_TOOL_RESULT_CORRUPTION | 73 | 0 | 0 | 0.0 | 0.0 | 0 | 0 | 0 | 73 | 0 |
| code_review | branch_and_verify | single_origin | LEP_TOOL_RESULT_CORRUPTION | 73 | 0 | 0 | 0.0 | 0.0 | 0 | 0 | 0 | 73 | 0 |
| code_review | coordinator_workers | one_to_many | LEP_TOOL_RESULT_CORRUPTION | 61 | 0 | 0 | 0.0 | 0.0 | 0 | 0 | 0 | 61 | 0 |
| code_review | coordinator_workers | single_origin | LEP_TOOL_RESULT_CORRUPTION | 60 | 0 | 0 | 0.0 | 0.0 | 0 | 0 | 0 | 60 | 0 |
| code_review | review_loop | single_origin | LEP_TOOL_RESULT_CORRUPTION | 61 | 0 | 0 | 0.0 | 0.0 | 0 | 0 | 0 | 61 | 0 |
| financial_analysis | branch_and_verify | many_to_one | LEP_TOOL_RESULT_CORRUPTION | 163 | 139 | 144 | 85.2761 | 88.3436 | 5 | 0 | 11 | 8 | 0 |
| financial_analysis | branch_and_verify | single_origin | LEP_TOOL_RESULT_CORRUPTION | 187 | 149 | 151 | 79.6791 | 80.7487 | 5 | 3 | 14 | 22 | 0 |
| financial_analysis | coordinator_workers | one_to_many | LEP_TOOL_RESULT_CORRUPTION | 169 | 16 | 69 | 9.4675 | 40.8284 | 53 | 0 | 49 | 51 | 0 |
| financial_analysis | coordinator_workers | single_origin | LEP_TOOL_RESULT_CORRUPTION | 169 | 117 | 129 | 69.2308 | 76.3314 | 12 | 0 | 17 | 23 | 0 |
| financial_analysis | review_loop | single_origin | LEP_TOOL_RESULT_CORRUPTION | 172 | 133 | 137 | 77.3256 | 79.6512 | 7 | 3 | 17 | 18 | 0 |
| research_synthesis | branch_and_verify | many_to_one | LEP_TOOL_RESULT_CORRUPTION | 140 | 3 | 3 | 2.1429 | 2.1429 | 0 | 0 | 0 | 137 | 0 |
| research_synthesis | branch_and_verify | single_origin | LEP_TOOL_RESULT_CORRUPTION | 140 | 2 | 2 | 1.4286 | 1.4286 | 0 | 0 | 0 | 138 | 0 |
| research_synthesis | coordinator_workers | one_to_many | LEP_TOOL_RESULT_CORRUPTION | 150 | 7 | 7 | 4.6667 | 4.6667 | 0 | 0 | 0 | 143 | 0 |
| research_synthesis | coordinator_workers | single_origin | LEP_TOOL_RESULT_CORRUPTION | 151 | 5 | 5 | 3.3113 | 3.3113 | 0 | 0 | 0 | 146 | 0 |
| research_synthesis | review_loop | single_origin | LEP_TOOL_RESULT_CORRUPTION | 144 | 3 | 3 | 2.0833 | 2.0833 | 0 | 0 | 0 | 141 | 0 |

## Transitions

| lep_code | old | new | count |
| --- | --- | --- | --- |
| LEP_INPUT_DISREGARD | negative | incomparable | 8 |
| LEP_INPUT_DISREGARD | negative | negative | 1628 |
| LEP_INPUT_DISREGARD | negative | unknown | 31 |
| LEP_INPUT_DISREGARD | positive | incomparable | 466 |
| LEP_INPUT_DISREGARD | positive | positive | 74 |
| LEP_INPUT_DISREGARD | positive | unknown | 116 |
| LEP_TOOL_RESULT_CORRUPTION | negative | negative | 108 |
| LEP_TOOL_RESULT_CORRUPTION | negative | positive | 82 |
| LEP_TOOL_RESULT_CORRUPTION | negative | unknown | 1149 |
| LEP_TOOL_RESULT_CORRUPTION | positive | positive | 568 |
| LEP_TOOL_RESULT_CORRUPTION | positive | unknown | 6 |

## Lifecycle and coverage

Event counts count each event once, even when it contains several corrupted facts. A new adoption event is absent from the old anomalous-event set; it can occur in a previously positive trace. Writes are forwarding actions; cross-agent transmission additionally requires an actual receiver and recorded content transport. Recovery counts are assertion-level restoration, not human correctness judgments.

```json
{
  "LEP_INPUT_DISREGARD": {
    "adoption_events": 0,
    "adoption_events_in_new_positive_traces": 0,
    "adoption_traces": 0,
    "classification:incomparable": 474,
    "classification:negative": 1628,
    "classification:positive": 74,
    "classification:unknown": 147,
    "comparability:comparable": 1849,
    "comparability:incomparable": 474,
    "cross_agent_events": 0,
    "cross_agent_traces": 0,
    "delivered_traces": 2323,
    "downstream_agent:analyst": 12,
    "downstream_agent:coordinator": 12,
    "downstream_agent:reviewer": 5,
    "downstream_agent:specialist_b": 23,
    "downstream_agent:verifier": 22,
    "downstream_agent_count:1": 74,
    "event_depth:12": 24,
    "event_depth:15": 1,
    "event_depth:6": 14,
    "event_depth:9": 35,
    "final_impact:detected": 34,
    "final_impact:unknown": 2289,
    "forwarding_events": 0,
    "forwarding_traces": 0,
    "handoff_depth:1": 74,
    "legacy_matched_effect_events": 0,
    "legacy_only_positive_traces": 0,
    "max_supported_path_depth:12": 24,
    "max_supported_path_depth:15": 1,
    "max_supported_path_depth:6": 14,
    "max_supported_path_depth:9": 35,
    "measured": 2323,
    "new_adoption_events": 0,
    "persistent:unknown": 2323,
    "recognized_boundary_traces": 0,
    "recovered_adoption_events": 0,
    "recovered_traces": 0,
    "recovery_assertion_links": 0,
    "shape_matched_controls:0": 461,
    "shape_matched_controls:1": 5,
    "shape_matched_controls:2": 8,
    "shape_matched_controls:3": 1849,
    "traces_with_new_adoption_events": 0,
    "transient:unknown": 2323,
    "unmatched_slot_adoption_events": 0,
    "verified_boundary_traces": 2323
  },
  "LEP_TOOL_RESULT_CORRUPTION": {
    "adoption_events": 452,
    "adoption_events_in_new_positive_traces": 93,
    "adoption_traces": 301,
    "classification:negative": 108,
    "classification:positive": 650,
    "classification:unknown": 1155,
    "comparability:comparable": 1519,
    "comparability:incomparable": 394,
    "cross_agent_events": 99,
    "cross_agent_traces": 47,
    "delivered_traces": 1913,
    "downstream_agent:analyst": 38,
    "downstream_agent:coordinator": 19,
    "downstream_agent:specialist_a": 9,
    "downstream_agent:specialist_b": 13,
    "downstream_agent:synthesizer": 12,
    "downstream_agent:verifier": 105,
    "downstream_agent_count:0": 476,
    "downstream_agent_count:1": 153,
    "downstream_agent_count:2": 20,
    "downstream_agent_count:3": 1,
    "event_depth:14": 9,
    "event_depth:17": 22,
    "event_depth:2": 8,
    "event_depth:20": 2,
    "event_depth:26": 2,
    "event_depth:29": 2,
    "event_depth:32": 1,
    "event_depth:35": 1,
    "event_depth:41": 3,
    "event_depth:5": 598,
    "event_depth:8": 2,
    "final_impact:detected": 36,
    "final_impact:not_detected": 190,
    "final_impact:unknown": 1687,
    "forwarding_events": 380,
    "forwarding_traces": 301,
    "handoff_depth:0": 464,
    "handoff_depth:1": 150,
    "handoff_depth:2": 21,
    "handoff_depth:3": 15,
    "legacy_matched_effect_events": 647,
    "legacy_only_positive_traces": 349,
    "max_supported_path_depth:11": 1,
    "max_supported_path_depth:12": 4,
    "max_supported_path_depth:14": 25,
    "max_supported_path_depth:15": 2,
    "max_supported_path_depth:17": 114,
    "max_supported_path_depth:2": 3,
    "max_supported_path_depth:20": 7,
    "max_supported_path_depth:23": 2,
    "max_supported_path_depth:26": 5,
    "max_supported_path_depth:29": 12,
    "max_supported_path_depth:32": 2,
    "max_supported_path_depth:35": 3,
    "max_supported_path_depth:38": 2,
    "max_supported_path_depth:41": 6,
    "max_supported_path_depth:44": 4,
    "max_supported_path_depth:5": 455,
    "max_supported_path_depth:8": 3,
    "measured": 1913,
    "new_adoption_events": 158,
    "persistent:detected": 36,
    "persistent:not_detected": 190,
    "persistent:unknown": 1687,
    "recognized_boundary_traces": 586,
    "recovered_adoption_events": 259,
    "recovered_traces": 229,
    "recovery_assertion_links": 1924,
    "traces_with_new_adoption_events": 129,
    "transient:detected": 123,
    "transient:not_detected": 36,
    "transient:unknown": 1754,
    "unmatched_slot_adoption_events": 62,
    "verified_boundary_traces": 1913
  }
}
```

## Evidence files

- `changed_trace_evidence.jsonl`: all added/removed positive traces, old and new event evidence, intervention boundaries, controls and downstream paths.
- `new_trc_adoption_event_evidence.jsonl`: every newly identified adoption event, affected assertion and original/corrupted values, source boundary, downstream path, recovery.
- `lifecycle_and_coverage.json`: lifecycle, comparability, event depth, handoff depth and agent-count distributions by LEP and configuration.
- `classification_transitions.csv`, `before_after_by_configuration.csv`: complete configuration comparisons.
- `transition_examples.json`: trace pointers for review.
- `../detector_evidence.jsonl`: complete evidence for every measured trace.
