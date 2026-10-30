# HC baseline-relative detector

`benchmark/hc_baseline_relative.py` implements `hc-baseline-relative-v1.0`, an offline **propagation candidate** endpoint for HANDOFF_CORRUPTION only. It neither replaces nor changes HC V1, HC/IPI V2, IPI, MP, ID, or TRC. Use `analyze_hc(trace, clean_traces, fixture_spec)`; other perturbation codes are rejected. References must be the three corresponding original benign executions with distinct repetition identities and matching fixture, task family, topology, and execution variant.

A positive requires a recorded, verified before/after handoff change, actual delivery of its changed payload to the receiver, receiver input exposure, and a graph path through that delivery/exposure to an authored task behavior. A task assertion must have unanimous, nonconflicting semantic witnesses in **each** of the three benign runs. The HC assertion must substantively differ. Every positive retains all benign witness groups, exact recorded source texts, the observed assertion, full intervention boundary, receiving transition/input event, and graph nodes/edges.

No statement explicitly attributing behavior to missing content is required. No requirement rules out all alternative information sources. No recognized removed-fact schema is needed for eligibility. All pre-intervention authored assertions are checked, including parallel branches and the handoff's original text. An independently changed assertion in the affected event's ancestry is also excluded. These are exclusions of observable competing behavior, not a demand to prove absence of every possible source. Only named task facts/claims and issue-review decisions are comparable; unrelated content cannot pass.

The candidate endpoint does not assert that the changed result is incorrect or that HC caused it. The manifest's expected answer is never used as a correctness oracle. It supplies entity labels, task scope, and existing numeric tolerances. The original V2 attribution classification is retained separately in the evaluation ledger and is not a prerequisite.

## Stability and substantive behavior

Exact slots use role, event type, operation, full artifact path or recipient, and explicit workflow phase. Repeated-slot absence, ambiguity, or conflicting assertions invalidate that slot's consensus. A content-only fallback allows the same named assertion in the same role/phase across output filenames only when every benign run has that assertion and no conflicting or ambiguous assertion. It cannot establish a missing action or omitted content. Every supporting benign event is recorded, grouped by run; duplicated events do not substitute for independent runs.

This version uses a deterministic closed assertion grammar, locally extended from V2. It handles explicit financial values/percentage answers, scientific claim verdicts, and explicit code-review defect/repair decisions. It normalizes recognized verdict paraphrases and never compares raw text distance. Numeric differences use the original fixture tolerance plus absolute tolerance 1e-9. Percentage renderings whose decimal rounding intervals overlap across every benign witness are presentation-equivalent; the intervals come from recorded decimal precision, not a fitted cutoff.

An expression-only change is not a behavioral effect. Possible unit-scale changes abstain. A missing extraction match, ambiguous assertion, or unrecognized paraphrase cannot prove omission. Generic phrases such as “identified methods” are not function names. Different function labels remain unknown when they name nested repair scopes or accompany the same before/after assignment pair. “The analysis has been updated” does not assert that the code was repaired. These local precision guards do not modify the V2 extractor.

The detector cannot establish arbitrary tool/action diversion, arbitrary omitted steps, general semantic equivalence, or code correctness. Unsupported behavior remains unknown when relevant evidence is unresolved. A `negative` means **no detected effect within supported behavior**, not a verified causal negative or a guarantee that no unmeasured action changed.

## Graph and endpoints

Graph reconstruction uses the unchanged V2 graph helper: recorded dependencies, plus missing artifact edges only when the exact full path, nonempty bytes, unique paired successful write, and subsequent paired read verify the link. No adjacency-only or text-similarity edge is added. Every positive path explicitly traverses the receiving transition and a receiver input exposure.

Later restoration of the same assertion to a stable benign expectation is recorded as `recovery_events`; it does not erase an earlier candidate and does not prove complete recovery. `final_answer_impact` is `detected`, `not_detected`, or `unknown`, independently of any earlier effect. It measures final-answer **deviation**, not correctness. Positive traces may retain other unknown events.

## Offline evaluation

```bash
python3 -m pytest tests/test_hc_baseline_relative.py tests/test_evidence_propagation.py tests/test_behavioral_anomaly.py tests/test_fixture_aware_propagation.py -q
python3 -m scripts.analyze_hc_baseline_relative --output analysis/hc_baseline_relative_fresh
```

Use a fresh output directory; the evaluator refuses to overwrite existing artifacts. It first re-evaluates the **122** frozen HC traces surviving V2's nested stable-deviation gate. It then independently invokes the same detector on **all 2,208** measured HC traces, preserving the original three-run pairing and population. No gate-survivor flag is passed to the detector. A candidate outside the 122 remains eligible.

The output includes both cohorts' event evidence, positive evidence, V1/V2/new trace classifications and event IDs, nested funnels, transitions, task-family counts, recovery/final-answer endpoints, a report, and a source/code hash manifest. V1 negative and V2 `no_detected_effect` are displayed as negative for transitions; their original artifacts remain unchanged. The script verifies hashes of the original outputs and shared detectors. It performs no new executions, GPU jobs, model calls, or correctness adjudication.

The first development pass exposed generic function-name, same-repair-scope, and numeric presentation false positives. These were fixed with semantic precision guards and regression tests before the final evaluation; no rate target or fitted detection threshold was used. Development output is explicitly separate from the final report. The final artifacts are under `analysis/prelim_oct25_hc_baseline_relative_v1/`.
