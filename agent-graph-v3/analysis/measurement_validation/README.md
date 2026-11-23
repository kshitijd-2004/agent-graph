# Independent human-grounded validation of the BND measurement pipeline

This directory is a **validation framework** for the existing BND
(Behavioural Non-Deviation) measurement pipeline. It does not modify the
pipeline, the traces, the manifests, or any existing result. It builds the
samples, the blinded annotation materials, and the evaluation machinery that
a two-annotator human study will populate — and it establishes, from the
artifacts that already exist, which parts of the pipeline can be trusted,
which cannot, and what is required to know which.

**Nothing here is a claim that the BND pipeline is valid.** The primary output
is empirical evidence about whether the four dimensions capture behavioural
difference, and the evidence that answers that question (human labels) is
produced in Phase B. The framework is complete and runnable end-to-end up to
the point where human judgment is required; it hard-stops there rather than
substituting a model-generated label.

---

## What the pipeline measures, in one paragraph

For each perturbed (LEP) execution, four behavioural dimensions are measured
independently against a set of benign reference executions of the same
fixture:

* **assertions** — the factual claims the agents expressed,
* **decisions** — the final answer / verdict / conclusion,
* **actions** — the observable tool behaviour,
* **outcomes** — the delivered output.

Each dimension *d* has a distance `D_d`, a **benign-calibrated threshold**
θ_d = the maximum benign-to-benign distance across the reference set, and a
divergence indicator that is positive iff
`D_d(LEP) − D_benign(d) > θ_d` and the dimension is evaluable. The overall
divergence indicator is the **logical union** of the four per-dimension
indicators.

There is **no weighted composite**. A composite was considered and
deliberately abandoned; it is not reintroduced anywhere in this framework, and
a test asserts that no `weight`/`composite` key leaks into the union output.
The union is only as good as its components, and several components have known
coverage problems (see "Structural findings below").

Downstream, four propagation stages are defined on these rows:

* **M1** — whole-trace union positive.
* **M2** — cross-agent (origin-corrected) downstream deviation.
* **M3** — dependency-supported downstream reach (`verified` / `plausible`).
* **M4** — task failure. The **primary** metric is **M4-matched**: M3-supported
  ∧ LEP task grade `failure` ∧ at least one compatible benign reference graded
  `correct`. The historical M4 (without the matched clause) is retained for
  comparison only. M4-matched is **perturbation-associated** degradation, not
  a causal claim: the benign references are separate executions, not
  counterfactuals of the same run.

Historical figures re-verified against `analysis/measurement_v6/out/summary_v6.json`:
frozen M1 = **4,150/16,773 (24.7 %)**, v6 M1 = **5,037/16,773 (30.0 %)**,
M4 historical = **954**, M4-matched = **421**. All four are confirmed in
`tests/test_sampling.py`.

---

## The three phases

```
Phase A   Automated preparation            (this directory, complete)
          frame, sample, pairs, blinded packets, extraction comparison,
          GT-containment probes, statistics, reports.
Phase B   Human annotation                 (PENDING — two annotators)
          annotators A and B label independently; disagreements adjudicated;
          labels frozen at annotation_templates/ADJUDICATED.jsonl.
Phase C   Final evaluation                 (implemented, gated on Phase B)
          compare extractors against adjudicated labels; validate union and
          per-dimension indicators; threshold sensitivity; M1–M4 evidence.
```

Phase C will **not** run until Phase B supplies labels:
`scripts/evaluate.py --stage full` raises `HumanLabelsMissing` if
`ADJUDICATED.jsonl` is absent or empty. It never fabricates a reference label
from v6 or an LLM.

---

## How to run it (Phase A)

All commands run from `~/agent-graph/agent-graph-v3` and write only under
`analysis/measurement_validation/` (and its `.cache/`).

```bash
# 0. (idempotent) build the corpus index — ~2 min, one-time
python3.11 analysis/measurement_validation/scripts/build_index.py

# 1. build the population frame + stratified/representative sample
python3.11 analysis/measurement_validation/scripts/sample_validation.py

# 2. build the matched-pair comparison set
python3.11 analysis/measurement_validation/scripts/build_pairs.py

# 3. generate blinded annotation packets + the confidential token key file
python3.11 analysis/measurement_validation/scripts/make_annotation_materials.py

# 4. run BOTH extractors on the sample, grade, probe GT containment, report
python3.11 analysis/measurement_validation/scripts/evaluate.py --stage extractor_only

# 5. regenerate all reports
python3.11 analysis/measurement_validation/scripts/report.py

# 6. run every integrity test
python3.11 -m pytest analysis/measurement_validation/tests/ -q
```

After two annotators have filled the response templates and an adjudicator has
resolved disagreements into `ADJUDICATED.jsonl`:

```bash
python3.11 analysis/measurement_validation/scripts/evaluate.py --stage full
python3.11 analysis/measurement_validation/scripts/report.py
```

---

## Layout

```
README.md  SAMPLING.md  ANNOTATION_GUIDE.md  VALIDATION_STATUS.md   <- this doc
sample_manifest.jsonl    <- 300 units (A representative + B targeted)
pair_manifest.jsonl      <- 150 matched comparisons
SAMPLING_DESIGN.json  PAIR_DESIGN.json   <- machine-readable design + seeds
annotation_templates/
    extraction_packet_annotator_{A,B}.jsonl   blinded, 300 items
    pair_packet_annotator_{A,B}.jsonl         blinded, 150 groups
    responses_extraction_annotator_{A,B}.jsonl  empty templates
    responses_pairs_annotator_{A,B}.jsonl        empty templates
    ADJUDICATED.jsonl                          <- Phase B output (not yet present)
scripts/                <- everything above, plus
    common.py  frame_population.py  sample_validation.py  build_pairs.py
    extractors.py  graders.py  views.py  make_annotation_materials.py
    evaluate.py  stats_v1.py  report.py
tests/                  <- 140+ tests; blinding, containment, stats, sampling
reports/                <- generated tables
.cache/                 <- trace index, frame, extraction evidence, key file
```

---

## Structural findings the framework surfaces

These are properties of the pipeline and corpus discovered during construction,
reported as evidence rather than silently absorbed:

1. **Extraction coverage is family-dependent and asymmetric.** For FinQA, the
   frozen extractor records the final answer in a different shape than v6, so
   "the answer" is not always comparable across the two extractors without the
   per-family normalisation in `views.py`. The framework computes agreement
   only over keys both extractors actually populated, and reports the
   never-populated share separately.
2. **`one_to_many` is not comparable** to the other propagation modes under
   frozen V4.1 (all such traces `comparable=False`). The sample retains these
   units for propagation-mode machinery, but per-mode `one_to_many` rates are
   never pooled against the other modes.
3. **Ground-truth containment.** Two things are kept distinct throughout:
   *GT-value fields* (`value`, `tolerance`, `pattern`, `source_text`,
   `value_strings`, `truth`, `gold`, `expected`, `grading`,
   `answer_calculation`) disclose the expected answer and must never reach an
   extractor; *task-side fields* (`label`, `unit`, ids) say what to extract
   and are legitimately read — the frozen extractor reads
   `required_facts['answer'].unit`. The measurement feed is
   `strip_gt_values` (remove GT-value fields, keep task-side), which is
   verified both faithful and contained: it reproduces the frozen extractor
   on 16,773/16,773 rows and never trips v6's guard (0/16,773). An aggressive
   key-removal form (`strip_gt_hardened`) is kept only as an audit primitive —
   it over-removes (deleting the task-side `unit`) and would collapse frozen
   financial coverage 1,719→426. A canary differential leak probe runs on
   every evaluation and is vacuous-detecting.
4. **v6's own `strip_gt` has a real (non-answer) gap.** It leaves 8 of its
   11 declared GT keys reachable at the spec root; pinned in
   `test_containment_corpus.py` so a future v6 fix regenerates this report
   instead of silently changing it.
4. **The two components support different claims.** Component A (representative)
   yields population estimates; Component B (targeted) characterises failure
   modes only. Pooling them is a known-bad move, encoded as
   `population_estimable`.

See `VALIDATION_STATUS.md` for the full accounting of what is verified by
execution, by inspection, inferred, or not yet validated.

---

## Statistical treatment (in brief)

* Population estimates use the design weights of Component A and a
  **fixture-clustered** variance: rows that share a fixture are not
  independent, so a cluster bootstrap (not a naive n) is the interval.
* Binary rates carry Wilson intervals; small-n rates carry
  Clopper–Pearson; weighted rates carry cluster-bootstrap intervals.
* Agreement uses raw agreement plus chance-corrected κ (Cohen) and Krippendorff
  α with ordinal weighting for numeric fields.
* No numeric precision/recall targets are asserted as scientific standards.
  Any threshold mentioned is a *proposed engineering target* and is labelled as
  such, with the reasoning.

Details and the exact estimators: `scripts/stats_v1.py`,
`reports/`.

---

## What this is *not*

* Not a re-implementation of the pipeline. The frozen module
  (`benchmark/baseline_deviation_v41.py`, SHA-256
  `75f1e77f…`) and the v6 layer are read in place, never modified.
* Not a claim of validity. A passing test suite proves the *machinery* is
  correct; it says nothing about whether BND measures behavioural difference.
* Not a causal analysis. M4-matched is association.
