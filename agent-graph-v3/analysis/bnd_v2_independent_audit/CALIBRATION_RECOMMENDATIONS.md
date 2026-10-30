# Minimal benign-control calibration design

**Recommendation: first correct and freeze the observation model; then use 5–6 independent benign repetitions per cell to evaluate a predeclared score on held-out executions and held-out fixture groups.** No executions were generated for this audit.

## Preconditions

The critical findings are at `baseline_deviation_v2.py::_verify_recovery`, `_checkpoints`, `_injection_boundary`, and the imported `_emitted_text`. The major extraction/availability findings are detailed in the companion report. These are prerequisites rather than threshold-tuning preferences:

1. Exclude all injected origin content/spans, including later origins and origin text inside repeated-role prefixes. Prove input-isolation invariance with source-only mutations.
2. Bind financial facts to the correct year/entity and unit; stop scoring document identifiers and derived ratios as source facts. Extract final numeric answers with explicit scope. Preserve unknown values honestly.
3. Distinguish code target mention from defect stance and repair; scope research verdicts to assertion clauses. Canonicalize irrelevant report paths in actions as well as outcomes.
4. Define fixed observable feature sets and missingness rules, consistent across comparisons. Decide how abstentions affect eligibility without using the tested run to select a more favorable baseline threshold.
5. Specify separate whole-trajectory, intermediate-state and final-output estimands. Use role plus iteration/semantic state and dependency-aware ordering. Define a peak statistic on a consistent component scale, or calibrate explicitly separate stage scores. Final actions must not silently mean cumulative history if the intended metric is final deliverable deviation.
6. Rename/retire `verified_recovery` until it requires actual observed adoption and subsequent correction on a provenance path. Calibrating ordinary benign distance does not validate recovery attribution. Correct the 83/86 reporting error before freezing documentation.

The 722 successful ground-truth-mutation checks and exact legacy arithmetic can be retained. They do not excuse the failed isolation/semantic checks.

## Repetition allocation

Prefer **six runs: three references, one calibration pseudo-treated benign, two test pseudo-treated benign runs per cell**. Five runs can use three references, one calibration pseudo-treated run and one test pseudo-treated run. The roles must be assigned by a fixed seed before examining outcomes. Repetitions require independent random seeds or independent model sampling, with fixture, model/version, temperature, tools, topology and execution policy held fixed.

Using three references preserves the current deployed score definition and permits comparison with the frozen result. Do not silently calibrate with two references and deploy with three, or fit using six reference runs and evaluate a three-reference score. If the intended deployed design instead requires five or six references, obtaining held-out tests requires additional executions; 5–6 total repetitions cannot supply both that reference bank and independent evaluation.

Predeclare eligible cells and structural-comparability rules. Report missing executions and eligibility losses separately. Reusing the existing three controls as references is acceptable for a prospective test of a frozen, already developed metric, but the existing dataset has been used extensively to inspect extraction and thresholds. New fixture groups provide stronger evidence against development overfitting.

## Leakage-safe split and fitting

Split **fixture groups** into development/calibration/test groups, keeping every topology and variant of the same fixture together where feasible. The existing proposal's “cell-level” split is better than splitting individual pairs, but it can still place the same document/problem in training and test under different topologies. Shared source families or near-duplicate problems should also be grouped when known.

On development fixtures, choose extraction rules and a small, predeclared threshold family. On calibration fixtures, score the held-out benign run exactly as a LEP would be scored, using its three disjoint reference runs. Fit a pooled or stratified cutoff to these **run-level scores**, with task family/topology strata specified in advance. On test fixtures, freeze everything and evaluate the reserved pseudo-treated runs, using only that fixture's designated reference bank. No test run enters either its reference bank or the fitted threshold distribution.

Reasonable small candidate families are `D_run−D_benign`, `D_run−max(pairwise benign)`, or a fixed robustly normalized score. Select one before the final evaluation. Evaluate the existing max-pair rule as an exploratory comparator. The calibrated null must match the actual score, reference count, availability policy and structural gate. Do not fit a percentile of the 10 or 15 correlated pairwise distances from five or six runs and call it a run-level false-positive rate.

For intermediate peaks, construct the same matched checkpoint statistic on held-out benign runs. Predefine a pseudo-boundary/eligible-checkpoint schedule independently of the observed values, matched to the analysis being calibrated. Calibrate the maximum over checkpoints directly to account for multiplicity. Final scores and any combined “peak OR final” flag need their own prespecified calibration. The existing maximum over heterogeneous benign pair/stage distances is not a substitute.

## Evaluation and uncertainty

Report the empirical false-alarm fraction among evaluable held-out benign runs, coverage/abstention, and uncertainty. Cluster uncertainty by fixture or the highest shared-data group; repeated runs and shared references within a cell are not independent trials. Also report per-family/topology results, using partial pooling or broad strata if sample sizes are small, rather than unsupported per-cell rates.

Five or six runs per cell do **not** permit estimating a 5% per-cell tail. With one held-out run per cell, precision comes from the number of independently held-out fixture groups, not from counting all pairs as extra observations. As a rough planning calculation under independent Bernoulli sampling, a 5% rate requires approximately 1,825 test observations for a 95% interval with ±1 percentage point half-width (`1.96² × .05 × .95 / .01²`); clustering increases the requirement. With zero observed false alarms, the approximate one-sided 95% upper bound is `3/n` for independent trials. These are planning approximations, not confidence guarantees for this clustered dataset.

A finite-sample rank-based/conformal option requires exchangeable held-out calibration scores and a correctly chosen order statistic; per-cell five-run samples cannot support a 5% cutoff. If pooling across cells, state the exchangeability assumptions and assess stratified miscalibration. Do not claim distribution-free conditional coverage across heterogeneous tasks.

## Separate validation for recovery and interpretation

Benign-control calibration assesses false alarms for **behavioral distance**, not sensitivity to injected influence or correctness of recovery. Recovery needs a separate, blinded semantic sample with event-level evidence: recorded original/mutated boundary, descendant agent adoption, a later correction of that assertion, and a terminal persistence check. Include resistant runs, missing observations, independent branches, repeated roles, multiple origins, partial correction and relapse. Report adjudicator agreement and uncertainty, keeping BND flags hidden during annotation where practical.

After those steps, defensible claims are empirical held-out false-alarm rates for a frozen scoring procedure and descriptive behavioral differences alongside a separate attribution measure. A calibrated deviation remains neither proof of causal propagation nor proof that the final task answer is correct.
