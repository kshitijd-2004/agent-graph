# Consolidated AgentProp Production Traces — Full BND V4.1 + Downstream M1–M4 Audit Report

**Run date:** 2026-11-21
**Dataset:** `/projects/biha/agentprop_consolidated_20261121/traces/`
**BND module:** `benchmark/baseline_deviation_v41.py` (SHA-256: `75f1e77f...`)
**Downstream module:** `analysis/downstream_propagation_corrected/analyze_downstream.py`
**Frozen extractor:** `analysis/bnd_v41_cw_swe200_provisional/frozen_run/benchmark/baseline_deviation_v41.py`

---

## 1. Raw and Unique Trace Counts

| Population | Raw files | Staged (after dedup) |
|-----------|-----------|---------------------|
| Benign (condition in {benign, counterfactual}) | 9,793 | 9,028 |
| LEP (condition = single_lep) | 18,208 | 17,937 |
| **Total** | **28,001** | **26,965** |

### Deduplication decisions

| Dedup type | Removed | Kept |
|-----------|---------|------|
| LEP duplicate execution IDs within cell | 271 | 17,937 |
| Benign duplicate execution IDs within cell | 765 | 9,028 |
| **Total removed** | **1,036** | — |

**Dedup policy:** Within each cell `(fixture_id, topology, lep_code, propagation_mode, repetition_index)` for LEPs and `(fixture_id, topology, execution_variant, repetition_index)` for benigns, traces sharing the same `attempt_id` are exact duplicates. Distinct `attempt_id` values are distinct legitimate repetitions and all are preserved.

**Note:** The 1,036 removed traces match exactly the reported "1,036 exact duplicate copies" from the dataset audit. The 1,045 "extra occurrences of repeated execution IDs" are the subset where the duplicate had the same execution ID but different file names (different consolidation source).

---

## 2. Benign and Admitted LEP Populations

After staging:
- **Benign cells:** 9,028 files across 2,707 cells (fixture/topology/variant)
- **LEP cells:** 17,937 files across 17,931 cells
- **Complete benign cells (≥3 reps):** 0 — all benign cells have fewer than 3 repetitions

### LEP breakdown by code

| LEP code | Raw | Staged | % of LEP |
|----------|-----|--------|----------|
| LEP_INDIRECT_PROMPT_INJECTION | 4,692 | 4,185 | 23.3% |
| LEP_HANDOFF_CORRUPTION | 4,511 | 4,211 | 23.5% |
| LEP_INPUT_DISREGARD | 4,501 | 4,203 | 23.4% |
| LEP_TOOL_RESULT_CORRUPTION | 4,504 | 4,174 | 23.3% |

### LEP breakdown by topology

| Topology | Staged | % |
|----------|--------|---|
| branch_and_verify | 7,350 | 41.0% |
| coordinator_workers | 7,146 | 39.8% |
| review_loop | 3,712 | 20.7% |

### LEP breakdown by propagation mode

| Mode | Staged | % |
|------|--------|---|
| single_origin | 11,009 | 61.4% |
| many_to_one | 3,624 | 20.2% |
| one_to_many | 3,575 | 19.9% |

**All 17,937 staged LEPs are `execution_variant=standard`.** No memory_enabled traces found in the dataset. The `--exclude-lep LEP_MEMORY_POISONING` flag was set as a safety guard but had no effect (no memory poisoning LEPs present).

---

## 3. BND V4.1 Analysis Results

### Population

| Metric | Count |
|--------|-------|
| LEP traces submitted | 17,937 |
| Successfully measured | 17,937 |
| Comparable | 16,773 |
| **Incomparable** | **1,164** |
| Unparseable | 0 |

### Incomparability reasons (exact)

| Reason | Count |
|--------|-------|
| `need_at_least_3_benign_references:2` (only 2 shape-compatible refs) | 578 |
| `need_at_least_3_benign_references:1` (only 1 shape-compatible ref) | 532 |
| `need_at_least_3_benign_references:0` (no shape-compatible refs) | 54 |
| **Total** | **1,164** |

**All 1,164 incomparable traces have a valid benign reference pool (≥0 candidates) but fewer than 3 execution-shape-compatible references.** The incomparability is reference-compatibility shortage, not data quality.

### Incomparability: pool shortage vs shape incompatibility

| Cause | Count |
|-------|-------|
| True pool shortage (<3 candidates exist in cell) | 9 |
| Shape-incompatible among ≥3 candidates | 1,155 |

**Topology/mode breakdown of shape-incompatible (pool ≥3, <3 compatible):**

| Topology | Mode | Incomparable |
|----------|------|-------------|
| coordinator_workers | single_origin | 1,151 |
| coordinator_workers | one_to_many | 4 |
| review_loop | single_origin | 6 |

This is consistent with the CW topology's execution shape (fan-out, cross-role handoffs) differing from benign CW references even for single_origin LEPs. The V4.1 structural gate (`execution_shape`) requires identical `(initial_roles, transfers)` between LEP and each benign candidate. CW LEP traces with coordinator handoff patterns that differ from benign CW traces are rejected at this gate.

**Note:** The downstream analyzer's scope guard asserts all comparable rows are within the authoritative scope (4 LEPs × 3 topologies × 3 modes, standard only). The 1,164 incomparable CW single_origin traces are structurally valid but lack sufficient compatible references — they are excluded from M1–M4 denominators.

### Comparable population: 16,773

| Metric | Value |
|--------|-------|
| BND mean (comparable) | 0.0505 |
| Behavioral anomaly (D_LEP > T) | 4,497 (26.8%) |
| Distinct benign reference sets | 2,729 |
| Reference pools complete (≥3 candidates) | 17,928 of 17,937 |

### Comparable by LEP code

| LEP code | Comparable | Anomaly rate |
|----------|-----------|-------------|
| LEP_HANDOFF_CORRUPTION | 4,211 | 18.3% |
| LEP_INDIRECT_PROMPT_INJECTION | 4,185 | 36.6% |
| LEP_INPUT_DISREGARD | 4,203 | 16.8% |
| LEP_TOOL_RESULT_CORRUPTION | 4,174 | 35.6% |

### Comparable by topology

| Topology | Comparable | Anomaly rate |
|----------|-----------|-------------|
| branch_and_verify | 7,193 | 23.0% |
| coordinator_workers | 5,987 | 30.1% |
| review_loop | 3,593 | 29.0% |

### Comparable by propagation mode

| Mode | Comparable | Anomaly rate |
|------|-----------|-------------|
| single_origin | 9,619 | 24.1% |
| many_to_one | 3,585 | 25.4% |
| one_to_many | 3,569 | 35.5% |

### Stage summary (comparable population)

| Metric | True | False | None (unavailable) |
|--------|------|-------|-------------------|
| high_intermediate_deviation | 5,891 | 10,882 | — |
| persistent_final_deviation | 4,372 | 9,541 | 2,860 |
| observed_correction | 1,755 | 2,287 | 12,731 |
| dependency_supported_correction | 754 | 574 | 13,445 |
| relapse | 1 | 2 | 15,770 |

---

## 4. Downstream M1–M4 Metrics

### Denominators

| Denominator | Count | Note |
|------------|-------|------|
| All comparable | 16,773 | Full population |
| Delivery verified | 15,578 | Byte-verified mutation evidence |
| Delivery unverified | 1,195 | No mutation evidence |
| Downstream evaluable | 14,441 | Has stage thresholds |
| Undetermined (never negative) | 2,332 | No stage threshold available |

### M1: Whole-trace deviation

| Metric | Value |
|--------|-------|
| Positive | 4,150 |
| Denominator | 16,773 |
| Rate | 24.74% |
| DD0 positive | 6,435 (38.37%) |
| Cluster binomial CI95 | [60.5%, 64.1%] (1,701/2,729 clusters) |

### M2: Cross-agent downstream deviation

| Metric | Value |
|--------|-------|
| Positive (corrected, origin-excluded) | 3,052 |
| Denominator (all) | 16,773 |
| Rate (all) | 18.20% |
| Positive evaluable | 3,052 |
| Denominator (evaluable) | 14,441 |
| Rate (evaluable) | 21.13% |
| Legacy (same-agent inclusive) | 3,808 (22.70%) |
| Nonzero threshold | 1,031 (6.15%) |
| Cluster binomial CI95 | [49.4%, 53.1%] (1,399/2,729 clusters) |

**Task grades among M2 positives:**
| Grade | Count |
|-------|-------|
| failure | 2,332 |
| correct | 433 |
| not_gradeable | 287 |

### M3: Dependency-supported propagation

| Metric | Value |
|--------|-------|
| Verified | 346 |
| Plausible | 1,301 |
| **Total M3** | **1,647** |
| Denominator (M2 + delivery verified) | 2,862 |
| Rate (all comparable) | 9.82% |
| Cluster binomial CI95 | [31.4%, 34.9%] (904/2,729 clusters) |

### M3 by LEP

| LEP | Verified | Plausible |
|-----|----------|-----------|
| LEP_HANDOFF_CORRUPTION | 0 | 392 |
| LEP_INDIRECT_PROMPT_INJECTION | 187 | 326 |
| LEP_INPUT_DISREGARD | 8 | 337 |
| LEP_TOOL_RESULT_CORRUPTION | 151 | 246 |

### IPI/TRC transformed-handoff recovery

| LEP | M2+delivery verified | Verified recovered | Plausible recovered |
|-----|---------------------|-------------------|---------------------|
| LEP_INDIRECT_PROMPT_INJECTION | 1,083 | 187 (17.3%) | 326 (30.1%) |
| LEP_TOOL_RESULT_CORRUPTION | 1,042 | 151 (14.5%) | 246 (23.6%) |

### Reach basis breakdown (all M2+delivery verified = 2,862)

| Basis | Count |
|-------|-------|
| Content in downstream agent's own output (verified) | 346 |
| Content in downstream input only (plausible) | 909 |
| Lineage on downstream role (plausible) | 392 |
| Structural DAG exposure only (unsupported) | 1,215 |

### M4: Ground-truth downstream task failure

| Metric | Value |
|--------|-------|
| Positive | 1,213 |
| Denominator (all comparable) | 16,773 |
| Rate | 7.23% |
| Denominator (M3-supported) | 1,647 |
| Rate (over M3-supported) | 73.65% |
| Still correct among M3 | 228 |
| Not gradeable among M3 | 206 |

### M4 breakdown

**By reach tier:**
| Tier | M4 positives |
|------|-------------|
| Verified | 217 |
| Plausible | 996 |

**By task family:**
| Family | M4 | Rate (over M3-supported by family) |
|--------|----|-----------------------------------|
| research_synthesis | 836 | — |
| code_review | 331 | — |
| financial_analysis | 46 | — |

**By LEP:**
| LEP | M4 |
|-----|----|
| LEP_INPUT_DISREGARD | 225 |
| LEP_HANDOFF_CORRUPTION | 258 |
| LEP_INDIRECT_PROMPT_INJECTION | 418 |
| LEP_TOOL_RESULT_CORRUPTION | 312 |

### M4 benign baseline contrast

| Metric | Value |
|--------|-------|
| Unique benign refs graded | 8,187 |
| Benign task failures | 3,920 |
| **Benign failure rate** | **47.88%** |

**By family:**
| Family | Failures / Total | Rate |
|--------|-----------------|------|
| code_review | 1,914 / 2,739 | 69.88% |
| financial_analysis | 332 / 2,742 | 12.11% |
| research_synthesis | 1,674 / 2,706 | 61.86% |

---

## 5. Comparison with Previous 12,891 Comparable Population (v2 / bvrl-regen)

| Metric | v2 (12,891) | v3 consolidated (16,773) | Δ |
|--------|-------------|------------------------|---|
| Comparable | 12,891 | 16,773 | +3,882 (+30.1%) |
| Incomparable | 2,789 | 1,164 | -1,625 |
| M1 positive | 3,156 (24.48%) | 4,150 (24.74%) | +994, rate +0.26pp |
| M2 positive (all) | 2,242 (17.39%) | 3,052 (18.20%) | +810, rate +0.81pp |
| M2 (evaluable) | 2,242 (20.55%) | 3,052 (21.13%) | +810, rate +0.58pp |
| M3 verified | 293 | 346 | +53 |
| M3 plausible | 891 | 1,301 | +410 |
| M3 total | 1,184 | 1,647 | +463 |
| M4 positive | 842 | 1,213 | +371 |
| M4 rate | 6.53% | 7.23% | +0.70pp |
| Benign baseline | 47.5% | 47.88% | +0.38pp |

### Changes in measurement definitions or denominator

1. **No changes to measurement definitions.** The same frozen BND V4.1 module and corrected downstream analyzer are used. No thresholds, weights, or grading rules were modified.

2. **Denominator change:** The previous v2 population used 4 sources (consolidated + cw_regen + swe_200 + bvrl_regen). This run uses 1 source (consolidated only). The consolidated dataset is a superset that already includes all four source populations, so the 16,773 comparable traces represent the full union. The increase from 12,891 to 16,773 (+3,882) reflects:
   - Additional benign repetitions improving reference pool completeness (fewer incomparable traces: 2,789 → 1,164)
   - The bvrl-regeneration traces that were part of the v2 staging are already embedded in the consolidated dataset

3. **Benign baseline change:** 47.5% → 47.88% (slight increase due to different graded benign reference set)

---

## 6. All Output Paths

### Staging
```
analysis/consolidated_20261121_bnd_v41/
├── staged/merged/traces/          # 26,965 symlinks (input to BND V4.1)
└── provenance.jsonl               # Dedup decisions and staging provenance
```

### BND V4.1
```
analysis/consolidated_20261121_bnd_v41/bnd_v41_run/
├── bnd_v41_per_trace.jsonl        # 17,937 rows (all measured, 182MB)
├── bnd_v41_summary.json           # Overall + group summaries
├── run_manifest.json              # Module SHA-256, source dir, parameters
├── reconciliation_v41.json        # Comparable/incomparable/unparseable
├── coverage_report_v41.json       # Component availability, feature states
├── origin_exclusion_evidence.json # Provenance evidence
├── stage_summary_v41.json         # Correction, relapse, recovery stats
├── stage_coverage_v41.json        # Stage field availability
├── boundary_gate_v41.json         # INPUT_DISREGARD boundary restoration
├── threshold_sensitivity_v41.json # R0-R5 sensitivity + near-threshold
├── v4_v41_transition.json         # V4→V4.1 transition grid
├── bnd_v41_by_lep_type.json
├── bnd_v41_by_task_family.json
├── bnd_v41_by_topology.json
└── bnd_v41_by_propagation_mode.json
```

### Downstream M1–M4
```
analysis/consolidated_20261121_bnd_v41/downstream_results/
├── per_trace.jsonl                # Per-trace M1-M4 flags + grades
└── summary.json                   # Headline metrics, breakdowns, CIs
```

---

## 7. Scope Guard Verification

The downstream analyzer's scope guard passed without assertion failures:
- All 16,773 comparable rows are `execution_variant=standard`
- All rows have topology in {coordinator_workers, branch_and_verify, review_loop}
- All rows have propagation_mode in {single_origin, one_to_many (CW), many_to_one (B&V)}
- All rows have lep_code in {LEP_TOOL_RESULT_CORRUPTION, LEP_INDIRECT_PROMPT_INJECTION, LEP_HANDOFF_CORRUPTION, LEP_INPUT_DISREGARD}
- Zero rows outside the authoritative scope

---

## 8. Final M1–M4 Metrics

| Metric | Count | Rate | Denominator |
|--------|-------|------|-------------|
| **M1** whole-trace deviation | 4,150 | 24.74% | 16,773 |
| **M2** cross-agent downstream deviation | 3,052 | 18.20% | 16,773 |
| M2 (evaluable only) | 3,052 | 21.13% | 14,441 |
| **M3** dependency-supported propagation | 1,647 | 9.82% | 16,773 |
| ├─ verified | 346 | — | — |
| └─ plausible | 1,301 | — | — |
| **M4** ground-truth task failure | 1,213 | 7.23% | 16,773 |
| M4 (over M3-supported) | 1,213 | 73.65% | 1,647 |
| Benign task-failure baseline | 3,920 | 47.88% | 8,187 |

**Cluster binomial CIs (2,729 reference sets):**
- M1: 62.3% [60.5%, 64.1%]
- M2: 51.3% [49.4%, 53.1%]
- M3: 33.1% [31.4%, 34.9%]
