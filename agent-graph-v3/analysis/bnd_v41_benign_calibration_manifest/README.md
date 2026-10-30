# BND V4.1 — Reproducible Benign-Control Calibration Manifest

Deliverable directory for the **AgentProp BND V4.1 benign-control
calibration** planning/preflight task. This is a **planning and preflight
only** deliverable: no execution was launched, no model inference was run,
and no Slurm job / GPU / vLLM was started. All eligibility evidence is read
from the frozen V4.1 ledger and the frozen production source.

## Files

| file | what it is |
|---|---|
| `SUMMARY.md` | Concise human summary: funnel, the 45 configs, model, coverage limitations, how-to-run. |
| `calibration_manifest.json` | Machine-readable manifest: 45 configurations + alternates + verified production settings. |
| `calibration_manifest.csv` | Same configs, one row each, for spreadsheets. |
| `planned_executions.csv` | The 270 planned clean executions (config × reps 3–8), with calibration-slot + role. |
| `plan_manifest.json` | `agentprop-readiness-selection-v1` plan (270 benign scenarios) runnable by `python -m benchmark --plan-manifest`. |
| `preflight_validate.py` | **Independent, no-inference** validator (re-derives comparability with the frozen analyzer CPU gates + re-checks the plan against the frozen planner). |
| `preflight_report.json` | Output of `preflight_validate.py` (all checks, per-config comparability, blockers). |
| `eligibility_funnel.json` | Eligible counts by family/topology/variant + the exclusion funnel. |
| `execution_spec.md` | Verified model + vLLM + inference + execution config; scheduling semantics; exact 270-run command. |
| `_work/` | Intermediate build artifacts (trace index, enriched ledger, stratum profiles, selection, per-pass scripts). |

## Reproduce the numbers

```bash
cd /u/kdhande/agent-graph/agent-graph-v3
# 1. build the source-trace index (one-off; ~1.5 min, read-only)
python3.11 analysis/bnd_v41_benign_calibration_manifest/_work/index_traces.py
# 2. enrich + cell eligibility + scheduling shape + strata + selection + manifest + report
python3.11 analysis/bnd_v41_benign_calibration_manifest/_work/pass2_cells.py
python3.11 analysis/bnd_v41_benign_calibration_manifest/_work/pass3_shapes.py
python3.11 analysis/bnd_v41_benign_calibration_manifest/_work/pass4_strata.py
python3.11 analysis/bnd_v41_benign_calibration_manifest/_work/pass6_select.py
python3.11 analysis/bnd_v41_benign_calibration_manifest/_work/pass7_manifest.py
python3.11 analysis/bnd_v41_benign_calibration_manifest/_work/pass8_report.py
# 3. independent preflight (no inference)
python3.11 analysis/bnd_v41_benign_calibration_manifest/preflight_validate.py
```

## What "eligible" means here

A configuration `(fixture, topology, execution_variant)` is eligible only when
**all** of the following are read from the frozen V4.1 ledger (nothing is
assumed from trace presence):

- **E1** — a complete 3-benign-reference set (reps 0/1/2), all present in
  the frozen source and in one cell;
- **E2** — ≥1 perturbed (LEP) trace of the cell was successfully analyzed by
  V4.1 with `behavioral_anomaly` True **or** False (not None);
- **E3** — the comparison features/masks for that decided trace are valid
  (non-empty component mask, calculable threshold T, at least one finite
  benign-pair and one finite LEP-pair total);
- **E4** — reproducible: the fixture exists under `workspace_fixtures` with
  a readable manifest supporting the topology + LEP, and the three
  references agree on the recorded execution settings where those settings
  are recorded (a reference with no admission contract is unverifiable, not
  disagreeing — the frozen V4.1 rule);
- **E5** — the chosen perturbed trace is `comparable: true` with an empty
  `reasons` list under the frozen V4.1 scoring path (its execution shape
  matches the references').

Families are further constrained to the three named **materialized**
benchmark fixture families (FinQA / SciFact / SWE-bench Verified); legacy
hand-built fixtures stay in the eligible ledger but are not selected.

## Key findings

- **Eligible: 2,646 configurations** (of 2,741 cells with a complete
  3-reference set; 1,240 excluded for an incomplete reference set, 95 for
  having no V4.1-decided perturbed trace).
- **Selected: 45** (15/family × 5/topology), **270 planned executions**
  (6/config, reps 3–8).
- **Model verified as Qwen2.5-72B-Instruct-AWQ (4-bit AWQ)**, not
  Qwen3-8B; enforced by preflight.
- **`one_to_many` is not representable among eligible cells** (all 2,949
  frozen `one_to_many` traces are `comparable=False` in V4.1) — reported,
  not forced.

## Strict-constraint compliance

No Slurm jobs, no GPUs, no vLLM startup, no model calls, no trace
generation. Frozen BND V4.1 outputs and frozen production traces are read
only. No changes were made to `benchmark/baseline_deviation_v41.py`,
`scripts/analyze_baseline_deviation_v41.py`, or any frozen artifact.
