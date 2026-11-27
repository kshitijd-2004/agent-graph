# AgentProp BND v7 — candidate measurement implementation + independent AI-judge validation

v7 is a CANDIDATE improvement of the four-dimensional Behavioral Divergence
(BND) measurement pipeline, plus an independent two-judge + third-model-
adjudicator evaluation of whether the pipeline's measurements correspond to
actual behavioral differences in multi-agent execution traces.

**Methodology is preserved, not re-derived.** BND remains four independent
dimensions (assertions, decisions, actions, outcomes), each with its own
extraction, distance, and benign-calibrated threshold; overall divergence is
the **logical union** of the four binary indicators. There is **no weighted
composite**. The historical frozen implementation and the v6 correction layer
are imported in place and never modified; every changed behaviour lives in a
separately named v7 module.

All new work is under `analysis/measurement_v7/` (requirement 2). Nothing
outside this directory was modified.

## Layout

```
base.py                 shared bootstrap: frozen/v6/v7 imports, GT-containment feed
partitioning.py         dev / held-out fixture-level partition (seed 20261129)
run_pipeline.py         the v7 measurement pipeline (per-trace + summaries)
extractors/             v7 extractors (research / swe / financial) + measure.py
distances/action_diagnostics.py   diagnostic measurements (counts, retry, order, failures)
judges/                 two independent AI judges + orchestrator + caches
adjudication/adjudicate.py        third-model adjudication prompt
evaluation/             reference build + frozen/v6/v7 scoring + threshold sensitivity + audit package
tests/                  v7 test suite
out/                    all run artifacts (partitions, per-trace, judges, references, reports)
```

## Reproducible commands

Run from the repository root (`agent-graph-v3`). All commands write only
under `analysis/measurement_v7/out/`.

### 0. Preconditions

```
cd agent-graph-v3
export ANTHROPIC_AUTH_TOKEN=...        # judge + adjudicator relay (existing, no new creds hardcoded)
# an OpenAI/GPT key is optional; if set, judge_b becomes the gpt family.
```

### 1. Freeze the dev / held-out partition (run once; output is deterministic)

```
python3.11 analysis/measurement_v7/partitioning.py
# -> out/partitions.json, out/partition_report.md
```

Fixture-level split, seed `agentprop-v7-partition-20261123`, 0.4 held-out.
The combined 300-trace + 150-pair fixture-reference graph is what is split;
related benign/LEP executions stay in one partition.

### 2. Run the v7 measurement pipeline

```
python3.11 analysis/measurement_v7/run_pipeline.py                 # full consolidated frame
python3.11 analysis/measurement_v7/run_pipeline.py --partition dev
python3.11 analysis/measurement_v7/run_pipeline.py --partition heldout
# -> out/per_trace_v7.jsonl, out/summary_v7.json
# -> out/per_trace_v7_dev.jsonl, out/summary_v7_dev.json
# -> out/per_trace_v7_heldout.jsonl, out/summary_v7_heldout.json
```

### 3. Build the blinded judge packets (300 extraction + 150 pair items)

```
python3.11 analysis/measurement_v7/judges/packet_builder.py
# -> out/judge_packets/extraction_packet_judge.jsonl, pair_packet_judge.jsonl
#    (+ build reports; a forbidden-token problem aborts the build)
```

### 4. Run the two independent AI judges (cached, provenance-logged)

The orchestrator runs chunked and idempotent (the cache makes a repeat a
no-op). A foreground shell needs `--max-calls` + `--offset` chunking:

```
# pair + extraction judgments for judge A and B:
python3.11 analysis/measurement_v7/judges/orchestrator.py --judge judge_a --tasks pair,extraction --max-calls 40 --offset 0
python3.11 analysis/measurement_v7/judges/orchestrator.py --judge judge_b --tasks pair,extraction --max-calls 40 --offset 0
# (raise --offset by 40 and re-run until needs_live_call == 0; the budget
#  file at out/judge_<X>_budget.json shows the remaining live work)
# -> out/judge_<X>_judgments.jsonl, out/judge_<X>_pair_judgments.jsonl,
#    out/judge_<X>_extraction_judgments.jsonl + budgets + reports
```

Successful responses are cached to `judges/out/judge_<X>_cache/`; a repeated
run never re-issues a paid call. Failed jobs are written to the failure
ledger, never replaced by a same-family model, and excluded from agreement
counts (a missing annotation is not a negative label).

### 5. Compute judge agreement (independent, before adjudication)

```
python3.11 analysis/measurement_v7/judges/run_agreement.py
# -> out/judge_agreement.json, out/judge_agreement_{extr,pair}_rows.jsonl
```

Labeled `ai_judge_agreement` throughout; carries the two-model/same-relay
caveat when no GPT endpoint is reachable.

### 6. Adjudicate the disagreements (third model; retry pass for transport/JSON failures)

```
python3.11 analysis/measurement_v7/judges/run_adjudication.py --max-calls 40 --offset 0
# repeat the offset in 40-call chunks until the failing set stops shrinking
python3.11 analysis/measurement_v7/judges/retry_adjudication.py --max-calls 40 --offset 0
# -> out/judge_adjudicated_ai.jsonl (originals preserved, never overwritten)
```

### 7. Build the AI-adjudicated reference sets + score frozen / v6 / v7

```
python3.11 analysis/measurement_v7/evaluation/build_ai_reference.py
# -> out/ai_reference_extraction.jsonl, out/ai_reference_pairs.jsonl, out/ai_reference_report.json
python3.11 analysis/measurement_v7/evaluation/run_reference_eval.py
# -> out/{extraction,divergence}_vs_reference_{dev,heldout}.json
# -> out/m4_matched_{dev,heldout,full}.json
```

### 8. Zero-threshold sensitivity (separately named variants; theta_d=0 is NOT changed)

```
python3.11 analysis/measurement_v7/evaluation/threshold_sensitivity.py --partition dev
python3.11 analysis/measurement_v7/evaluation/threshold_sensitivity.py --partition heldout
# -> out/thr_<partition>_threshold_sensitivity.json
```

### 9. Human-audit package (a PACKAGE — the audit is NOT performed; no labels fabricated)

```
python3.11 analysis/measurement_v7/evaluation/audit_package.py --n 60
# -> out/human_audit_package.json
```

### 10. Tests and verification

```
python3.11 -m pytest analysis/measurement_v7/tests/ -q            # 52 passed
python3.11 -m pytest analysis/measurement_v6/test_regression_v6.py -q   # 21 passed
python3.11 -m pytest analysis/measurement_validation/tests/ -q           # 142 passed
python3.11 analysis/measurement_validation/scripts/verify.py          # 11 passed
```

### 11. Gated legacy entry point (kept for reference)

```
python3.11 analysis/measurement_v7/evaluation/run_evaluation.py --partition dev
# the extraction stage is GATED (ReferenceMissing) unless a --reference file
# is supplied; it never runs against invented labels.
```

## Reading the results

- Per-trace divergence: `out/summary_v7*.json`
- Cross-implementation M1 / per-dimension / zero-threshold dependence:
  `out/cross_impl.json`
- Judge provenance: `out/judge_config_report.json` (provider, model id,
  prompt version + hash, inference params, input/output hashes, timestamps,
  token usage, cache/retry/failure state)
- Extraction & divergence vs the AI-adjudicated reference:
  `out/{extraction,divergence}_vs_reference_{dev,heldout}.json`
- M4-matched propagation: `out/m4_matched_*.json`
- Full claims, with their evidence classes, are in
  `VALIDATION_RESULTS.md`.

## What this is NOT

- Not a claim that propagation was measured more accurately in a human-
  validated sense: **no human labels exist yet** (`human_audit_package.json`
  is pending completion).
- Not a two-vendor agreement: judge_b is a distinct **same-relay** model
  because no GPT endpoint was reachable (recorded substitution_note).
- Not a change to `theta_d = 0`: zero-threshold sensitivity is a separately
  named analysis; the frozen threshold set is untouched.
- v7 is a candidate, not an automatic replacement for v6.