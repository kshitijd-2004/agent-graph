# AgentProp - corrected downstream-propagation analysis (BND V4.1)

READ-ONLY re-analysis of the frozen `bnd_v41_cw_swe200_provisional` population. No traces, benchmark code, frozen artifacts, or Slurm jobs (3600260/3627515/3627516) were modified. All new outputs live in this directory only.

## Metrics (four independent measurements, N = 10057 comparable)

| Metric | Definition | Positive | Denominator | Rate |
|---|---|---:|---:|---:|
| M1 whole-trace deviation | existing 4-dim rule, unchanged | 2322 | 10057 | 23.09% |
| M2 cross-agent downstream deviation | stage-gated deviation at a NON-origin agent role; origin & later-origin (M2O) excluded; ID authored-origin preserved | 1654 | all 10057 / evaluable 8560 | 16.45% / 19.32% |
| M3 dependency-supported propagation | M2 AND delivery-verified AND content-reach (verified=in own downstream output; plausible=input/lineage/DAG) | 843 (verified 172 + plausible 671) | 1495 | 8.38% |
| M4 ground-truth downstream task failure | M3 AND final deliverable is a ground-truth failure | 565 | 10057 | 5.62% |

Denominators are separated: evaluable 8560, undetermined (never negative) 1497, delivery-verified 9083, delivery-unverified 974.

## Comparison to prior figures

| Figure | Prior | Corrected | Delta |
|---|---:|---:|---|
| Whole-trace deviation | 23.09% (2,322) | 23.09% (2322) | unchanged (M1 rule preserved) |
| Cross-agent downstream deviation | 20.73% (2085, same-agent-inclusive) | 16.45% all / 19.32% evaluable (1654) | requires the deviation to sit on a DIFFERENT agent role: excludes origin checkpoints, M2O later-injection-origins, and later-turns-of-the-origin-role (same-agent continuation = local effect, not cross-agent). ID authored-origin preserved. Undetermined (1497) excluded from the numerator, never counted negative |
| Dependency-supported propagation | 3.60% (Tier D) | 8.38% (843: verified 172 + plausible 671) | M3 now grades IPI/TRC through *transformed* handoff content; DAG-exposure-only rows are reported as unsupported, not counted |

**IPI/TRC transformed-handoff recovery.** Prior analysis left IPI/TRC cross-role deviations "plausible but unverified" because it required verbatim payload copying of the origin agent's tool result - which by design never reaches another agent's output. This analysis instead probes the *content the mutation introduced* (numeric values substituted/added by TRC, the injected instruction text in IPI) and asks whether it appears in a downstream agent's OWN authored output or recorded input - paraphrased, summarized, or substituted; no verbatim requirement. Result: of 580 delivery-verified IPI and 542 TRC cross-agent M2 positives, **258 IPI (44.5%) and 212 TRC (39.1%)** recovered positive downstream reach evidence (verified: 87 IPI / 82 TRC; plausible: 171 IPI / 130 TRC). The remainder have only structural DAG exposure and stay unsupported.

## Benign baseline (the null hypothesis for task failure)

The same frozen extractor + grader applied to the **benign** reference traces (no LEP) yields a task-failure baseline of **47.5%** (2301/4845), by family: code_review 70.6%, research_synthesis 59.5%, financial_analysis 10.8%. Consequence: a "downstream task failure" is NOT a rare propagation signal - it is the modal outcome on these difficult tasks even *without* any LEP. M4 must therefore be read against this per-family baseline, not as absolute causation.

## Statistical (clustered) CIs

Executions share benign reference sets (up to ~8 LEPs per set), so the valid unit is the **reference-set / configuration cluster** (1615 total), not the individual execution. Cluster-binomial 95% CIs:
- M1: 942/1615 clusters show a positive = 58.3% (95% CI 55.9%-60.7%)
- M2: 796/1615 clusters show a positive = 49.3% (95% CI 46.9%-51.7%)
- M3: 479/1615 clusters show a positive = 29.7% (95% CI 27.5%-31.9%)

Execution-level Wilson intervals are reported in `summary.json` but flagged **anti-conservative** (they ignore shared-reference correlation).

## Breakdowns (M2 cross-agent positives / comparable denominator)

By LEP: {"LEP_HANDOFF_CORRUPTION": {"M2": 210, "M2_den": 2529}, "LEP_INDIRECT_PROMPT_INJECTION": {"M2": 637, "M2_den": 2509}, "LEP_INPUT_DISREGARD": {"M2": 191, "M2_den": 2526}, "LEP_TOOL_RESULT_CORRUPTION": {"M2": 616, "M2_den": 2493}}
By topology: {"branch_and_verify": {"M2": 767, "M2_den": 5253}, "coordinator_workers": {"M2": 368, "M2_den": 2152}, "review_loop": {"M2": 519, "M2_den": 2652}}
By mode: {"many_to_one": {"M2": 449, "M2_den": 2609}, "one_to_many": {"M2": 201, "M2_den": 1016}, "single_origin": {"M2": 1004, "M2_den": 6432}}
By family: {"code_review": {"M2": 578, "M2_den": 3850}, "financial_analysis": {"M2": 266, "M2_den": 3372}, "research_synthesis": {"M2": 810, "M2_den": 2835}}

M3 by LEP: {"LEP_HANDOFF_CORRUPTION": {"plausible": 190, "verified": 0}, "LEP_INDIRECT_PROMPT_INJECTION": {"plausible": 171, "verified": 87}, "LEP_INPUT_DISREGARD": {"plausible": 180, "verified": 3}, "LEP_TOOL_RESULT_CORRUPTION": {"plausible": 130, "verified": 82}}
M4 by family: {"code_review": 147, "financial_analysis": 27, "research_synthesis": 391}

## Strongest defensible claim

> Across 10,057 comparable standard executions, **1654 (19.32% of the 8560 downstream-evaluable)** show a *cross-agent* downstream deviation - a stage-gated deviation recorded at an agent role that is NOT an injection origin (origin-agent checkpoints and later-turn-of-origin checkpoints excluded; M2O later-injection-origins excluded; INPUT_DISREGARD authored-origin semantics preserved). Of those, **843** are *dependency-supported* (verified content reach 172 + plausible 671): delivery is byte-verified AND the mutation's introduced content is recorded reaching a downstream agent - in that agent's own authored output (verified) or a recorded input/lineage path (plausible). **565 of the 843** dependency-supported rows end in a ground-truth downstream task failure - but this must be read against the benign (no-LEP) task-failure baseline of 47.5%; attribution of a failure to the LEP is strongest for the 172 *verified* rows (specific introduced content in the downstream agent's own output) and remains association-only for plausible rows. The 652 delivery-verified M2-positive rows whose only evidence is structural DAG exposure are reported as unsupported, never as propagation.

## Remaining limitations

- **DAG reachability != consumption.** Rows whose only link to a downstream agent is structural DAG exposure (652 delivery-verified M2 positives, `structurally_exposed_only`) are reported as UNSUPPORTED and never counted as dependency-supported. "plausible" additionally includes `content_reach_input_only` and `lineage_downstream_role`: these show the content was *available* to (or recorded as consumed by) a downstream agent, not that it *drove* that agent's deviation. Only `verified` (introduced content in the downstream agent's own authored output) is content-level evidence of influence - and even that is correlation of content, not a counterfactual proof. No counterfactual (LEP-not-delivered) control exists in the population, so a false-positive rate cannot be measured.
- **Base difficulty.** Benign task-failure baseline is 47.5% overall (code_review 70.6%, research 59.5%); M4 counts failures without per-family null adjustment, so part of M4 reflects task difficulty, not propagation. (An adjusted LEP-minus-baseline failure rate is a follow-up analysis, not implemented here to avoid inventing a statistic.)
- **code_review grading coverage.** The frozen stance extractor reports many issues `unavailable`/`mentioned_no_stance`, so some code-review rows are `not_gradeable` (M4 under-counts code-review failures accordingly).
- **Omission-type mutations** (HC/ID truncation, TRC `partial_omission`) add no new tokens, so they can never be `verified` by content-reach; they degrade to at most `plausible`. This is an honest floor, not a miss.
- **Clustering is coarse.** The CI treats a whole reference set as one Bernoulli trial; within-set correlation across the ~8 LEP executions is further (and favorably) shrunk, so cluster CIs are themselves approximate. No multiple-comparisons correction is applied across the per-LEP / per-family subgroup tables.
- **Scope.** Standard executions only; memory poisoning and `memory_enabled` excluded; `competitive_intelligence` has 0 comparable rows. The three regeneration jobs are unchanged.
