# AgentProp - corrected downstream-propagation analysis (BND V4.1)

READ-ONLY re-analysis of the frozen `bnd_v41_cw_swe200_provisional` population. No traces, benchmark code, frozen artifacts, or Slurm jobs (3600260/3627515/3627516) were modified. All new outputs live in this directory only.

## Metrics (four independent measurements, N = 12891 comparable)

| Metric | Definition | Positive | Denominator | Rate |
|---|---|---:|---:|---:|
| M1 whole-trace deviation | existing 4-dim rule, unchanged | 3156 | 12891 | 24.48% |
| M2 cross-agent downstream deviation | stage-gated deviation at a NON-origin agent role; origin & later-origin (M2O) excluded; ID authored-origin preserved | 2242 | all 12891 / evaluable 10911 | 17.39% / 20.55% |
| M3 dependency-supported propagation | M2 AND delivery-verified AND content-reach (verified=in own downstream output; plausible=input/lineage/DAG) | 1184 (verified 293 + plausible 891) | 2084 | 9.18% |
| M4 ground-truth downstream task failure | M3 AND final deliverable is a ground-truth failure | 842 | 12891 | 6.53% |

Denominators are separated: evaluable 10911, undetermined (never negative) 1980, delivery-verified 11936, delivery-unverified 955.

## Comparison to prior figures

Prior = the original 3-source population analysis (results/), where applicable. Corrected = this expanded 4-source run.

| Figure | Prior | Corrected | Delta |
|---|---:|---:|---|
| Whole-trace deviation | 23.09% (2,322) | 24.48% (3156) | population grew; rate stable |
| Cross-agent downstream deviation | 20.73% (2,085, same-agent-inclusive) | 17.39% all / 20.55% evaluable (2242) | requires the deviation to sit on a DIFFERENT agent role: excludes origin checkpoints, M2O later-injection-origins, and later-turns-of-the-origin-role (same-agent continuation = local effect, not cross-agent). ID authored-origin preserved. Undetermined (1980) excluded from the numerator, never counted negative. (same-agent-inclusive count here = 2798, for contrast) |
| Dependency-supported propagation | 3.60% (Tier D) | 9.18% (1184: verified 293 + plausible 891) | M3 now grades IPI/TRC through *transformed* handoff content; DAG-exposure-only rows are reported as unsupported, not counted |

## Population expansion (v1 3-source -> v2 4-source, +bvrl-regen)

| Quantity | v1 (3-source) | v2 (+bvrl) |
|---|---:|---:|
| Comparable executions | 10057 | 12891 |
| M2 cross-agent downstream deviation | 1654 | 2242 |
| M3 dependency-supported (verified+plausible) | 843 | 1184 |
| M4 ground-truth downstream task failure | 565 | 842 |
| Evaluable / undetermined | 8560 / 1497 | 10911 / 1980 |

The bvrl-regen (job 3600260) supplied new benign reps that raised bv/rl cells to >=3 same-shape references, lifting the comparable population. The CW one_to_many / single-origin RELAY-skeleton incomparables remain (targeted by pending jobs 3627515/3627516).

**IPI/TRC transformed-handoff recovery.** Prior analysis left IPI/TRC cross-role deviations "plausible but unverified" because it required verbatim payload copying of the origin agent's tool result - which by design never reaches another agent's output. This analysis instead probes the *content the mutation introduced* (numeric values substituted/added by TRC, the injected instruction text in IPI) and asks whether it appears in a downstream agent's OWN authored output or recorded input - paraphrased, summarized, or substituted; no verbatim requirement. Result: of 815 delivery-verified IPI and 814 TRC cross-agent M2 positives, **391 IPI (48.0%) and 338 TRC (41.5%)** recovered positive downstream reach evidence (verified: 150 IPI / 140 TRC; plausible: 241 IPI / 198 TRC). The remainder have only structural DAG exposure and stay unsupported.

## Benign baseline (the null hypothesis for task failure)

The same frozen extractor + grader applied to the **benign** reference traces (no LEP) yields a task-failure baseline of **47.5%** (2964/6243), by family: code_review 70.9%, research_synthesis 61.4%, financial_analysis 11.7%. Consequence: a "downstream task failure" is NOT a rare propagation signal - it is the modal outcome on these difficult tasks even *without* any LEP. M4 must therefore be read against this per-family baseline, not as absolute causation.

## Statistical (clustered) CIs

Executions share benign reference sets (up to ~8 LEPs per set), so the valid unit is the **reference-set / configuration cluster** (2081 total), not the individual execution. Cluster-binomial 95% CIs:
- M1: 1271/2081 clusters show a positive = 61.1% (95% CI 59.0%-63.1%)
- M2: 1056/2081 clusters show a positive = 50.7% (95% CI 48.6%-52.9%)
- M3: 655/2081 clusters show a positive = 31.5% (95% CI 29.5%-33.5%)

Execution-level Wilson intervals are reported in `summary.json` but flagged **anti-conservative** (they ignore shared-reference correlation).

## Breakdowns (M2 cross-agent positives / comparable denominator)

By LEP: {"LEP_HANDOFF_CORRUPTION": {"M2": 259, "M2_den": 3234}, "LEP_INDIRECT_PROMPT_INJECTION": {"M2": 868, "M2_den": 3221}, "LEP_INPUT_DISREGARD": {"M2": 226, "M2_den": 3229}, "LEP_TOOL_RESULT_CORRUPTION": {"M2": 889, "M2_den": 3207}}
By topology: {"branch_and_verify": {"M2": 1181, "M2_den": 7169}, "coordinator_workers": {"M2": 368, "M2_den": 2152}, "review_loop": {"M2": 693, "M2_den": 3570}}
By mode: {"many_to_one": {"M2": 682, "M2_den": 3573}, "one_to_many": {"M2": 201, "M2_den": 1016}, "single_origin": {"M2": 1359, "M2_den": 8302}}
By family: {"code_review": {"M2": 607, "M2_den": 4107}, "financial_analysis": {"M2": 350, "M2_den": 4409}, "research_synthesis": {"M2": 1285, "M2_den": 4375}}

M3 by LEP: {"LEP_HANDOFF_CORRUPTION": {"plausible": 238, "verified": 0}, "LEP_INDIRECT_PROMPT_INJECTION": {"plausible": 241, "verified": 150}, "LEP_INPUT_DISREGARD": {"plausible": 214, "verified": 3}, "LEP_TOOL_RESULT_CORRUPTION": {"plausible": 198, "verified": 140}}
M4 by family: {"code_review": 156, "financial_analysis": 43, "research_synthesis": 643}

## Strongest defensible claim

> Across 10,057 comparable standard executions, **2242 (20.55% of the 10911 downstream-evaluable)** show a *cross-agent* downstream deviation - a stage-gated deviation recorded at an agent role that is NOT an injection origin (origin-agent checkpoints and later-turn-of-origin checkpoints excluded; M2O later-injection-origins excluded; INPUT_DISREGARD authored-origin semantics preserved). Of those, **1184** are *dependency-supported* (verified content reach 293 + plausible 891): delivery is byte-verified AND the mutation's introduced content is recorded reaching a downstream agent - in that agent's own authored output (verified) or a recorded input/lineage path (plausible). **842 of the 1184** dependency-supported rows end in a ground-truth downstream task failure - but this must be read against the benign (no-LEP) task-failure baseline of 47.5%; attribution of a failure to the LEP is strongest for the 293 *verified* rows (specific introduced content in the downstream agent's own output) and remains association-only for plausible rows. The 900 delivery-verified M2-positive rows whose only evidence is structural DAG exposure are reported as unsupported, never as propagation.

## Remaining limitations

- **DAG reachability != consumption.** Rows whose only link to a downstream agent is structural DAG exposure (652 delivery-verified M2 positives, `structurally_exposed_only`) are reported as UNSUPPORTED and never counted as dependency-supported. "plausible" additionally includes `content_reach_input_only` and `lineage_downstream_role`: these show the content was *available* to (or recorded as consumed by) a downstream agent, not that it *drove* that agent's deviation. Only `verified` (introduced content in the downstream agent's own authored output) is content-level evidence of influence - and even that is correlation of content, not a counterfactual proof. No counterfactual (LEP-not-delivered) control exists in the population, so a false-positive rate cannot be measured.
- **Base difficulty.** Benign task-failure baseline is 47.5% overall (code_review 70.9%, research 61.4%); M4 counts failures without per-family null adjustment, so part of M4 reflects task difficulty, not propagation. (An adjusted LEP-minus-baseline failure rate is a follow-up analysis, not implemented here to avoid inventing a statistic.)
- **code_review grading coverage.** The frozen stance extractor reports many issues `unavailable`/`mentioned_no_stance`, so some code-review rows are `not_gradeable` (M4 under-counts code-review failures accordingly).
- **Omission-type mutations** (HC/ID truncation, TRC `partial_omission`) add no new tokens, so they can never be `verified` by content-reach; they degrade to at most `plausible`. This is an honest floor, not a miss.
- **Clustering is coarse.** The CI treats a whole reference set as one Bernoulli trial; within-set correlation across the ~8 LEP executions is further (and favorably) shrunk, so cluster CIs are themselves approximate. No multiple-comparisons correction is applied across the per-LEP / per-family subgroup tables.
- **Scope.** Standard executions only; memory poisoning and `memory_enabled` excluded; `competitive_intelligence` has 0 comparable rows. The three regeneration jobs are unchanged.
