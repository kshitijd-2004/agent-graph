# v7 DESIGN — what changed, what did not, and why

## Invariants (requirement 3, preserved)

- Four independent dimensions: **assertions, decisions, actions,
  outcomes**. Each has its own extractor, distance, and
  benign-calibrated threshold `theta_d`.
- Overall divergence = **logical union** of the four binary
  indicators. **No weighted composite was introduced**; the weighted
  per-dimension composite that the frozen code computes is carried
  through as a diagnostic only, never as the divergence signal.
- Frozen (`benchmark/baseline_deviation_v41.py`) and v6
  (`analysis/measurement_v6/`) implementations are imported **in
  place, read-only**. The v7 layer imports them by explicit file
  path so a same-named validation module cannot shadow them
  (`base.import_pipelines`).
- Ground-truth containment reuses the validation framework's verified
  `strip_gt_values` feed, which removes every GT-value field at every
  depth but preserves the task-side `label`/`unit` keys (the
  over-aggressive removal of `unit` collapsed frozen financial
  coverage 1,719 -> 426; that defect is NOT reintroduced). A
  boundary assertion (`assert_gt_free`) raises `GTLeakError` and
  voids the run if any GT-value field is reachable at the extractor
  feed; the canary probe distinguishes a real leak from a VACUOUS
  spec (no GT fields to canary) and reports the vacuous case.

## Deterministic extractor changes (requirement 5)

The v7 extractors (`extractors/{research,swe,financial}_v7.py` +
`measure.py`) correct documented defect classes on top of the v6
feature bundle:

**Assertions**
- Negation / polarity inversions: a verdict clause immediately
  preceded by a negator, or containing a modal denial, is not read
  as a support ("cannot say claim 1 is supported" is NOT an
  endorsement; `research_v7` checks active-contradict vs support
  polarity with `_clause_has_active_contradict`).
- Hedging / uncertainty: a hedge in the verdict sentence downgrades
  an otherwise-definitive call to `ambiguous` rather than a
  definitive verdict (no uncertainty->definitive promotion).
- Collective verdicts + restated claim bodies: the verdict is
  taken from the claim's own body across restatements; a bare
  nominal mention with no stance-bearing phrase is `unavailable`,
  never a stance.
- Numerical punctuation: scale-word and punctuation normalization
  (`financial_v7`) keeps `1,234`-style operands parseable and binds
  years vs entity identifiers vs computed answers separately.
- Missing vs unavailable: `absent` means "discussed and not the
  stated fact"; `unavailable` means "could not be determined" — the
  two states are kept distinct and both are NON-positives.

**Decisions (final-response-only semantics preserved)**
- The decision scope is the FINAL response, not the whole trace;
  intermediate assertions stay in the assertion scope.
- `swe_v7.map_grade_vocab` maps the grader's vocabulary onto the
  artifact vocabulary through an explicit enum so a vocabulary
  mismatch produces a recorded mismatch, never a silent zero count.
- Missing decision / ambiguous decision / extractor failure are
  distinct recorded states.

**Actions**
- The historical total-variation distance over the tool-call
  counter is the BASELINE. Separate, independently-evaluated
  diagnostic measurements (`distances/action_diagnostics.py`):
  absolute tool-call-count difference, retry multiplicity, tool-call
  ordering, observable failures (only where an error field is
  actually populated — missing error fields are NOT fabricated).
  None of these is combined into a weighted score; each is reported
  on its own.

**Outcomes (family-specific final-output extraction)**
- FinQA: years and entity identifiers are not treated as numeric
  answers; percentages/units/operands/computed answers are handled
  and ambiguous answers stay ambiguous; extraction is separated
  from correctness grading.
- SciFact: actual final claim verdicts with collective-verdict and
  negation handling; no constant outcome distance.
- SWE-bench: issue identification is distinguished from path
  mention; issue-level stances are preserved; file-path overlap
  does NOT count as defect identification.

## Partitions (requirement 4)

`partitioning.py` splits the combined fixture-reference graph
(300 traces + 150 pairs) by FIXTURE at seed
`agentprop-v7-partition-20261123`, 0.4 held-out: 169 dev / 112
held-out fixtures (176 / 124 sample units; 93 / 57 pairs).
Benign and LEP executions of a fixture stay together; family,
topology, LEP, and divergence-class coverage are reported per
partition in `out/partition_report.md`. Inclusion probabilities and
design weights are UNCHANGED; a design-weighted estimate within a
partition is a conditional estimate, not a population estimate.
Held-out labels/predictions/disagreements were NOT inspected during
extractor development: the extraction/defect work used only
`analysis/measurement_v6/FINDINGS.md` and the frozen-audit defect
lists, and the dev/held-out split was frozen before any v7
evaluation. v6 IS disclosed as an influence (v7 builds on the v6
correction layer), so the pipeline is not a completely blind
candidate against v6.

## Judge infrastructure (requirements 7, 8, 9)

- Two independent frontier-model families: Judge A =
  `claude-opus-5-5`; Judge B = `claude-sonnet-5-5` (a distinct
  second model). The brief preferred GPT-6 as B; the relay catalog
  is Claude-only and gpt-*/o* identifiers return model_not_found
  (probed 2026-11-23), so judge_b is a DISTINCT same-relay model and
  the substitution is recorded in `out/judge_config_report.json`
  with a `substitution_note`: these are two-model, same-relay
  agreement numbers, NOT two-vendor agreement. Setting
  `OPENAI_API_KEY` restores the gpt config automatically.
- No hardcoded credentials: providers are discovered from the
  environment (`providers.discover_configured`); a judge that is
  not configured is not silently replaced by a same-family model.
- Blinding: packets carry task_prompt + execution excerpt only; a
  forbidden-token scan over every packet (`verify_blinding`) must be
  clean (it is: `judge_blinding_check.json`, 0 hits).
- Deterministic inference: temperature 0.0, max_tokens 4096;
  prompt version + SHA-256, input/output hashes, timestamps, token
  usage, retry counts, and failure records are written per call.
- Caching: successful responses are cached by (judge, item,
  prompt-version) key; repeated runs never re-issue a paid call.
  Judge outputs are NEVER overwritten; the adjudicated file is a
  merge that preserves every original row, and the existing human
  `ADJUDICATED.jsonl` was not touched.
- Adjudication: a third model (`claude-opus-4-8`) sees the
  original evidence, the rubric, and the two candidate labels
  ANONYMIZED (candidate-1 / candidate-2); it must give an
  evidence-based explanation and MAY leave fields genuinely
  ambiguous (unresolved_fields). An adjudicated verdict is labeled
  AI adjudication, not human ground truth.

## Reference sets (requirements 11, 12)

`evaluation/build_ai_reference.py` produces, per extraction item:
`ai_agreed` (both judges state+value match), `ai_adjudicated`
(third model resolved it), `ai_unresolved` (genuinely ambiguous —
excluded, never a negative), or `judge_invalid` (both unusable —
excluded). The per-dimension + union pair reference is built the
same way over the 150 matched groups, keeping the pipeline's
multi-reference semantics (mean over references; theta_d = max
benign-to-benign). `run_reference_eval.py` scores frozen / v6 / v7
against these, per family and per dimension (assertion scope AND
decision scope), with Wilson intervals and a fixture-cluster
bootstrap for the design-weighted component-A accuracy.
`threshold_sensitivity.py` runs the named theta variants
(unfireable / floored 0.01/0.05/0.1/0.25) WITHOUT changing
`theta_d = 0` in any primary result.

## Human audit (requirement 10)

`audit_package.py` emits `out/human_audit_package.json` — ~60 items
mixing a probability-sampled AI-agreement slice, AI disagreements,
zero-threshold positives, frozen/v6/v7 disagreements, difficult
numerics, ambiguous verdicts, and SWE-bench issue-identification
cases. Every `human_label` is `null`: **the audit has not been
performed and no human labels are fabricated.** The final report
distinguishes (1) AI-judge agreement, (2) AI-adjudicated reference
performance, (3) human-audited performance (pending).