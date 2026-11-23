# Sampling design

Design and record of the two validation samples. Everything below is
reproducible from the recorded seed via the commands in
[Reproducing](#reproducing).

**Seed:** `20261122` (recorded in `SAMPLING_DESIGN.json` and `PAIR_DESIGN.json`).
Machine-readable design: `SAMPLING_DESIGN.json`, `PAIR_DESIGN.json`.
Generated tables: `reports/sample_report.md`, `reports/pair_report.md`.

---

## 1. Population definitions

Three nested populations exist in this repository and **must not be mixed**.
The task's guidance to avoid denominator mixing is the reason this section
comes first.

| # | population | size | role here |
|---|---|---|---|
| P1 | consolidated BND V4.1 run: measurement rows satisfying the frozen comparability gate | **16,773** | **the frame** |
| P2 | staged LEP traces in the consolidated corpus | 17,937 | P1 + 1,164 non-comparable rows |
| P3 | staged trace files (LEP + benign) | 26,965 | includes 9,028 benign controls |

**The frame is P1: 16,773 comparable LEP measurement rows.** A "measurement
row" is one perturbed execution evaluated against its benign reference set.
Rows exist only where the frozen comparability gate was satisfied — at least
three structurally compatible benign references sharing one
`(fixture_id, topology, cell)`. The 1,164 staged LEP traces that fail that
gate produce **no measurement** and are therefore outside the frame.

Explicitly *not* the frame, and not comparable to it:

- `analysis/bnd_v41_cw_swe200_provisional/run_merged` — 10,057 comparable rows
  from a different module revision and a different trace staging.
- P2 and P3 above.

Verified properties of the frame (see `tests/test_sampling.py`):

- rows: **16,773**
- unique trace ids: **16,767** (6 trace ids appear twice as distinct attempts)
- frozen M1 positives: **4,150** → **24.74 %**, matching the recorded 24.7 %
- v6 M1 positives: **5,037** → **30.03 %**, matching the recorded 30.0 %

Both historical figures are thus confirmed against the artifacts rather than
taken on trust.

### Benign executions are controls, not measurements

The frame contains **no** benign rows. A benign execution enters the study
only as a member of some perturbed row's reference set (8,187 distinct trace
ids are referenced at least once). Benign executions are therefore sampled
from the *reference-set* population, and are labelled with a `#benign` suffix
on `unit_id` to keep them distinct from LEP rows of the same trace. Frame
matching for benign units is on `trace_id`.

---

## 2. Two components, two estimators

The sample is deliberately split because the two components support different
claims.

| component | n | selection | supports |
|---|---|---|---|
| **A — representative** | 180 | stratified SRSWOR, seed 20261122 | population-level accuracy via design weights |
| **B — targeted** | 120 | outcome-enriched, deterministic predicates | characterising known failure modes only |

### A: representative component

Strata are `(task_family, kind, grade/stratum)`. Within each stratum the frame
is sorted before drawing, and the draw uses an explicit Mersenne-Twister
stream, so the result does not depend on `random.sample`'s implementation
details.

Each component-A row carries:

- `inclusion_probability` π = *n*ₕ/*N*ₕ (SRSWOR within stratum *h*)
- `design_weight` = 1/π
- `n_in_stratum`, `n_drawn_from_stratum`
- `population_estimable: true`, `weight_meaning: "srs_within_stratum"`

A test asserts `π == n_drawn/n_in_stratum` exactly, so a stratum-size
mis-recording cannot pass silently.

The achieved component-A M1-positive rate is **27.7 %** (30/108) against a
population rate of **24.74 %**. A 3-point gap at n=108 is well within
sampling error (SE ≈ 4.3 pp), so component A is not detectably biased.

### B: targeted component

Component B oversamples specific failure modes. Each unit records
`targeted_predicates` (all predicates it satisfies) and
`primary_targeted_predicate` (the one it was drawn for), so overlap is visible
rather than hidden.

Covered predicates include: `zero_threshold_positive` (24),
`v6_zero_threshold_ony` (31), `extractor_state_differs` (38),
`extractor_value_differs` (26), `ambiguous_answer` (22),
`answer_unavailable` (31), `polarity_inversion` (3),
`frozen_v6_m1_disagreement` (20), `assertions_decisions_disagree` (13),
`retry_or_duplicate_actions` (43), `dimension_partially_unevaluable` (43),
`delivery_unverified` (3), `m4_matched_degraded` (7),
`m4_persistent_failure` (7), `unresolved_duplicate_id` (1).

`all_dimensions_unevaluable` has **0** members. This stratum is genuinely
empty in the frame — every frame row has at least one evaluable dimension by
construction, since a row with no evaluable dimension could not have passed
the comparability gate. It is reported as 0 rather than forced.

**Component B is not population-estimable.** Units were selected *because*
they satisfy an outcome-dependent predicate, so weighting them back to the
population is biased by construction. Every component-B row carries
`population_estimable: false` and
`weight_meaning: "within_targeted_bucket_only"`. The `design_weight` value is
retained only to make the bucket draw auditable.

> **Reporting rule.** Never pool A and B and report a weighted "accuracy" over
> the union. Component A yields population estimates; component B yields
> conditional characterisation ("among zero-threshold positives, how many are
> real?"). Naive pooled accuracy is not a population accuracy and must not be
> labelled as one.

---

## 3. What the sample covers

Realised composition (300 unique units, no duplicates):

| dimension | coverage |
|---|---|
| family | code_review 100, financial_analysis 100, research_synthesis 100 |
| topology | coordinator_workers 120, branch_and_verify 99, review_loop 81 |
| LEP | TRC 36, IPI 62, HC 32, ID 50 |
| kind | LEP 180, benign 120 |
| propagation mode | single_origin 222, one_to_many 41, many_to_one 37 |
| thresholds | zero-threshold positives 24, nonzero 10 |

This meets the "≈100 traces per task family" target and covers all four
retained LEPs, all three topologies, all three propagation modes, and both
threshold regimes.

### One_to_many caveat

41 sampled units are `one_to_many`. Per a standing finding, all 2,949
`one_to_many` LEP traces are `comparable=False` under frozen V4.1, so no
`one_to_many` decided trace can carry a one_to_many-decided measurement. These
units are retained because they exercise the propagation-mode machinery, but
**any per-propagation-mode rate for `one_to_many` is not comparable to the
other modes** and is reported separately, never pooled.

---

## 4. Matched-pair sample

150 benign/LEP comparisons (`pair_manifest.jsonl`), 85 of which reuse a trace
already in the 300-unit sample (disclosed per-pair via `in_sample_300`).

### What a "pair" is — the multi-reference semantics

A pair is **not** a `(perturbed, benign)` tuple. The pipeline:

1. forms the candidate pool = benign executions sharing
   `(fixture_id, topology, execution_variant)`;
2. filters that pool through the frozen structural gate (`execution_shape`
   equality plus the 5 `HARD_IDENTITY_FIELDS`), keeping the first 3 by
   `repetition_index` with `trace_id` as tie-break;
3. computes a per-dimension distance to **each** surviving reference and
   **averages** them → `component_benign[d]`;
4. sets θ_d = the **maximum** benign-to-benign distance among those reference
   pairs (the benign side's own internal spread);
5. flags dimension *d* positive iff
   `component_lep[d] − component_benign[d] > θ_d`.

The unit of comparison is therefore a **perturbed execution against an
averaged reference set, relative to a threshold the reference set itself
defines**. All 150 pairs retain their **entire** reference set
(`n_benign_references` ≥ 2, asserted in tests), with per-member distances, so
an annotator sees every reference and the averaged contrast. Substituting a
single reference would silently change the quantity being judged.

### How human judgments map onto those semantics

This is the mapping the task requires be documented explicitly:

| human judgment | compared against | reasoning |
|---|---|---|
| "member X differs on dimension *d*" | `component_lep[d] − component_benign[d] > θ_d` | the human judges observable difference; the pipeline judges mean-excess over the reference set against its internal spread |
| "all members agree on *d*" | dimension *d* negative | agreement is the pipeline's negative |
| "the references disagree with each other on *d*" | **excluded, not negative** | the packet's `reference_set_usable` question exists for this; an unstable control makes the comparison unusable, and `θ_d` (the max benign-to-benign distance) will already be large, mechanically suppressing positives. Counting these as false positives would penalise the pipeline for correctly treating a noisy control as noisy. |
| "cannot tell / not enough evidence" | excluded from precision and recall | mapped to `unavailable`; never counted as a negative |

Annotators are shown a **symmetric group** — one target plus its references,
shuffled per annotator, with the true role written only to a confidential key
file — so they cannot know which member is perturbed.

### Pair classes

Every pair has exactly one `class`; classes are mutually exclusive and their
counts sum to 150. Required strata present: zero-threshold positives,
nonzero-threshold positives, M1 negatives, and frozen/v6 disagreements.

---

## 5. Reproducing

```bash
# 1. frame + sample (writes frame_units.jsonl, sample_manifest.jsonl,
#    SAMPLING_DESIGN.json)
python3.11 analysis/measurement_validation/scripts/sample_validation.py

# 2. matched pairs (writes pair_manifest.jsonl, PAIR_DESIGN.json)
python3.11 analysis/measurement_validation/scripts/build_pairs.py

# 3. blinded annotation packets (+ the confidential token key file)
python3.11 analysis/measurement_validation/scripts/make_annotation_materials.py

# 4. reports
python3.11 analysis/measurement_validation/scripts/report.py

# 5. integrity tests
python3.11 -m pytest analysis/measurement_validation/tests/ -q
```

All four scripts are read-only with respect to existing artifacts; they write
only under `analysis/measurement_validation/` and its `.cache/`.

---

## 6. Limitations

1. **Component B does not generalise.** Every component-B rate is conditional
   on an outcome-selected predicate. It characterises failure modes; it does
   not estimate population prevalence.
2. **Traces cluster within fixtures.** Multiple rows share a fixture, so rows
   are not independent. Population estimates use a fixture-clustered variance
   (see `reports/` and `README.md` §Statistical treatment), not
   n-independence.
3. **The frame is the comparability-gated subset.** Findings apply to the
   16,773 comparable rows, not to the 17,937 staged LEP traces. The 1,164
   excluded rows are a systematically different population (they failed a
   structural gate) and are not represented in the sample.
4. **`one_to_many` is not comparable** to the other propagation modes
   (see §3).
5. **All-medium coverage is not guaranteed per cell.** Some
   `(family × topology × LEP × threshold-regime)` cells hold a single unit.
   Per-cell rates are reported only where n supports them; `insufficiently
   sampled` is marked otherwise.
6. **Annotator-facing packets were not validated by a human.** The blinding
   and completeness checks are automated and verified by test, but no human
   has yet confirmed the packets are answerable as written. See
   `VALIDATION_STATUS.md`.
