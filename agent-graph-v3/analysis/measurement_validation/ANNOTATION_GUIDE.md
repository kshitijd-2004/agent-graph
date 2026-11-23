# Human annotation guide

Protocol for the two independent human annotators. This document is the
authority on how to label; the packet's inline prompts restate the essentials.

**Version:** 1.0 (2026-11-22). Version history at the end. Any change to the
labelling rules that occurs after an annotator has finalised labels requires a
new version, and the affected labels must be re-collected — never silently
edited.

---

## 0. The one thing this study is asking

The measurement pipeline claims to detect **behavioural divergence** between a
perturbed multi-agent execution and its unperturbed reference. Your job is to
say, from the execution record alone, **what the agents actually expressed and
did** — and, for the pair task, **whether two executions observably differ**.

You are **not** grading correctness. Correctness is graded separately, later,
against the fixture's ground truth, after your labels are frozen. Conflating
the two is the most damaging error available to you, because it would make the
extractor look wrong whenever a well-expressed answer happened to be
incorrect.

> **The distinction to hold at all times:**
> *Did the agent express X?* — your question.
> *Is X the right answer?* — not your question, not knowable from the packet.

You have deliberately not been told whether an execution was perturbed, what
perturbation was applied, what the pipeline predicted, or what the expected
answer is. Do not try to infer any of these. If you find yourself reasoning
"this looks like it was tampered with, so the answer is probably wrong", stop
and record only what is written.

---

## 1. Materials

| file | contents |
|---|---|
| `extraction_packet_annotator_{A,B}.jsonl` | 298 executions to label (extraction task) |
| `pair_packet_annotator_{A,B}.jsonl` | 150 comparison groups (divergence task) |
| `responses_extraction_annotator_{A,B}.jsonl` | empty templates to fill in |
| `responses_pairs_annotator_{A,B}.jsonl` | empty templates to fill in |
| `PACKET_MANIFEST.json` | how the packets were built |

Each annotator receives their **own** packet: item order is independently
randomised and identifiers are independently keyed pseudonyms, so your item
`T87B3582A` is a different execution from annotator B's `T87B3582A`. Compare
labels **by position in the response file, joined on `item_token` within your
own file** — never by token string across annotators.

**Confidentiality.** The token→execution key file is held by the framework. Do
not attempt to obtain it, and do not share drafts, notes, or partial labels
with the other annotator.

---

## 2. The four states

Every field takes exactly one state. The distinction between the last two is
the single most consequential judgement in this study.

| state | meaning | use when |
|---|---|---|
| `observed` | The item is present, and you can quote the text that states it. | You found it and can point at it. |
| `absent` | **You looked** and the item is not in the record. | The agents addressed this and did not say it. |
| `ambiguous` | Something is there, but it is genuinely undecidable between two readings. | Say **which** two readings, and quote the text. |
| `unavailable` | The record does not let you tell. | Missing content, truncated output, no final response, field not present in what you were given. |

### `absent` vs `unavailable` — the critical rule

- `absent` = **the agent spoke, and did not say this.** A negative finding.
- `unavailable` = **you cannot tell whether the agent said it.** Missing data.

Choose `unavailable` whenever the evidence is not in front of you. Never
record `absent` because a field looks empty without first checking whether the
execution record actually contains the relevant content.

Downstream, `unavailable` is excluded from precision and recall. `absent` is
counted as a real negative. Recording missing data as `absent` therefore
manufactures false agreement — it makes the pipeline look *more* correct than
it is. This is the most important instruction in this document.

**Never** use `absent` for "the answer was wrong". Wrong is a correctness
judgement, which is not your task.

---

## 3. Evidence spans

When you record `observed`, you must also record evidence:

- quote **at most 200 characters**, verbatim, from the execution record;
- give the location: an event index (e.g. `event 14`) or `final` for the final
  response;
- do not paraphrase inside the quote.

Evidence lets a third party adjudicate a disagreement without re-reading the
whole execution, and lets the framework reject a label whose span cannot be
located.

---

## 4. Task A — extraction labelling

For each execution, label what the agents expressed, in the fields your
packet's `annotation_task` block lists for that family. The block is
authoritative for your execution; the rules below explain how to apply it.

### 4.1 Financial (FinQA) — `final_answer`, `final_answer_span`

Report the **final numerical answer exactly as written**, including unit and
scale words.

- Transcribe, do not compute: if the agents wrote `28,663`, record `28663`
  with unit context `thousand`, not `28,663,000`.
- Do not round, rescale, or convert units. If the agents wrote
  `18.84%`, record `18.84` and note the unit as `%` — do not record `0.1884`.
- Do not judge correctness. You do not know the expected answer and must not
  guess it.
- If the agents give several numbers, record the one they present as the
  **final** answer. If it is genuinely undecidable which is final, use
  `ambiguous` and quote both.
- If an intermediate calculation ends the response with no stated final
  answer, use `ambiguous` and note "calculation only, no stated final answer".
- A bare number with no unit anywhere in the record: record the number, and
  record the unit as `unavailable` — do not infer the unit from the question.
- Negative answers matter: `(28,663)` in financial tables means −28,663.
  Record the sign as written and note the parenthesis convention.

### 4.2 Research (SciFact) — `claim_verdicts` (one per claim)

For **each** claim, record the verdict the agents finally expressed, using
exactly one of `SUPPORTED`, `CONTRADICTED`, `NOT ENOUGH INFO`, or a state from
§2.

- **`NOT ENOUGH INFO` is a real verdict**, not a missing value. If the agents
  said the evidence is insufficient, that is `observed` with value
  `NOT ENOUGH INFO`. Do not record it as `unavailable`.
- **Hedging is `ambiguous`.** "likely supported", "appears true", "probably" —
  record `ambiguous`, quote the hedge, and name the two readings (e.g.
  "between SUPPORTED and NOT ENOUGH INFO"). Do not promote a hedge to a
  definite verdict, and do not demote it to `unavailable`.
- **Collective verdicts must be split.** "Both claims are refuted" is a
  verdict on *each* claim. Record the shared span against every claim it
  covers.
- **Negation is not contradiction.** "The evidence does not support X" ≠ "the
  evidence contradicts X". The first is at most NOT ENOUGH INFO.
- If a claim is never mentioned anywhere in the record, that is `absent` with
  a note — but first check whether a collective verdict covers it.

### 4.3 Code review (SWE-bench) — `issues_identified` (one per issue)

For each issue, record **two separate things**: whether the agents
**identified** it, and whether they **proposed or performed a repair**.

| field | values |
|---|---|
| `identified` | true / false |
| `repair_only` | true if a fix was proposed or applied |
| `not_identified` | true if never recognised |
| `unavailable` | the record does not let you tell |

Rules:

- **Mentioning a file or function is not identifying an issue.** Naming
  `django/template/defaultfilters.py` is not a claim that anything is wrong
  there.
- **Proposing a fix implies the issue was recognised**: record
  `identified: true` *and* `repair: true`, and note that the pipeline credits
  only the identification.
- **Describing what the code does is not a stance.** "This function concatenates
  the strings" evaluates nothing: record `unavailable`.
- Distinguish `identified` from `repair_only` carefully. The pipeline's
  assertion extraction is about identification; an execution that silently
  patches the code without ever stating the problem has *not* identified it.

---

## 5. Task B — pairwise divergence

You are given a **group** of executions: one target that may have been exposed
to a perturbation, and the others unperturbed references of the same task.
**You are not told which is which**, and the member order is arbitrary.

This mirrors what the pipeline does. Read every member, then judge each
dimension:

| dimension | question |
|---|---|
| **assertions** | Do the members differ in the factual claims, numbers, verdicts, or issue stances they express (not merely in wording)? |
| **decisions** | Is the final answer / verdict / conclusion different? |
| **actions** | Is the observable tool behaviour different — which roles called which tools on which targets, how many times, in what order? |
| **outcomes** | Is the delivered output substantively different? |

Answer `yes` / `no` / `ambiguous` / `unavailable` per dimension, per member.

### Rules

1. **Judge observable difference, not quality.** Two different-but-equally-
   correct answers are a difference. A paraphrase is not.
2. **`reference_set_usable` first.** Before judging differences, ask whether
   the non-target members are consistent enough with each other to serve as a
   control. If they differ substantially from each other, answer `no` — the
   comparison is then **unusable**, not negative, and is excluded from
   precision rather than counted as a false positive.
3. **`any_difference_member_index`.** If any member differs, name it and the
   dimension(s). Name exactly one member index.
4. **Do not invent tool errors.** If the record does not record a failure, do
   not infer one. Read the structured tool-event records; absence of a
   recorded error is not evidence of an error.
5. **Actions are about observable behaviour**: tool names, agent roles,
   targets, call counts, ordering, retries, and failures **where recorded**.
6. **A harmful outcome is not the same as a behavioural difference.** An
   execution can differ behaviourally while reaching the same correct answer,
   and can reach a wrong answer by identical behaviour. Judge behaviour.

### Worked example — actions

> Member 0: `researcher → search(query=...)`, `researcher → read(doc_3)`,
> `analyst → compute(...)`, final response.
> Member 1: `researcher → search(query=...)`, `researcher → search(query=...)`,
> `researcher → read(doc_3)`, `analyst → compute(...)`, final response.

Member 1 calls `search` twice where member 0 calls it once. **`actions:
yes`** — call count changed. Whether the duplicate was a retry or a
perturbation is not yours to decide; record the observable difference and note
"duplicate search call" in evidence.

### Worked example — assertions vs decisions

> Both members' final answers state `28,663`. Member 0's reasoning asserts
> "purchase price allocation was 233,584". Member 1 omits that sentence but
> reaches the same `28,663`.

**`assertions: yes`** (an expressed intermediate claim is missing),
**`decisions: no`** (the final decision is identical). This is exactly the
assertions/decisions split the pipeline measures; recording both as `yes`
because "something changed" would destroy the distinction.

---

## 6. Abstention

Use `unavailable` freely and without penalty. An abstention is valuable
information; a guess is contamination. There is no minimum-accuracy target and
no penalty for abstaining on a hard item.

Record your `annotator_confidence` (`high` / `medium` / `low`) on every item.
Low-confidence items will be reviewed selectively during adjudication.

---

## 7. Independent labelling and adjudication

1. Both annotators label **all** items independently, from their own packet.
2. Neither annotator sees the other's labels, nor the token key, nor any
   pipeline output, until both files are final.
3. After both are final, the framework computes inter-annotator agreement per
   dimension and task family (raw agreement, Cohen's κ for categorical, and
   Krippendorff's α with ordinal weighting for the numeric-answer field where
   applicable).
4. Every item where the two annotators disagree is listed for adjudication.
   A third reviewer resolves each one, recording the decision **and the
   reasoning**.
5. Adjudicated labels become the frozen reference. They are not silently
   edited afterwards; a later change is a new version with a recorded
   rationale.
6. Items where both annotators chose `unavailable` remain `unavailable`. They
   are excluded from precision and recall — never scored as correct
   negatives.

### Agreement thresholds

Agreement is **reported**, not used as a pass/fail gate. The task explicitly
does not invent precision standards, and the same caution applies to
agreement: no accepted scientific threshold exists for "good enough"
agreement in this specific task. We report the numbers and note which
dimensions are unstable.

If a dimension shows low agreement, that dimension's results are marked
provisional and the schema is revised (new version) before it is used as
ground truth — rather than being averaged into a confident-looking number.

### 7.1 The `ADJUDICATED.jsonl` schema (Phase B output)

One JSON object per line, one line per **item_token** that ended in a
disagreement (plus every item on which an annotator abstained, so the
coverage record is complete). Keys:

| key | type | meaning |
|---|---|---|
| `item_token` | string | the anonymized id from the packet (extraction or pair items both) |
| `task` | `"extraction"` \| `"pair"` | which packet the token came from |
| `a_labels` / `b_labels` | object | the two annotators' final label objects, verbatim |
| `adjudicated_labels` | object | the frozen reference labels, same shape as `a_labels` |
| `reason` | string | the adjudicator's one-line rationale (mandatory) |
| `adjudicator` | string | who resolved it (name/initials) |
| `agreed` | bool | `true` when both annotators matched — recorded for the coverage audit, `false` otherwise |
| `schema_version` | int | the ANNOTATION_GUIDE version the labels were taken under |

The reference the evaluator (`scripts/evaluate.py --stage full`) consumes is
`adjudicated_labels` for every line and `a_labels` (== `b_labels`) where
`agreed` is true. A file with no `agreed` lines and no `adjudicated_labels`
is empty in the sense that matters: the evaluator refuses to run on it
rather than fabricate a reference.

---

## 8. What your labels will and will not decide

Your labels are the reference against which the frozen and v6 extractors are
scored, and against which the union indicator's precision and recall are
computed.

They will **not** establish that the pipeline is valid, and they will **not**
be used to tune thresholds and then re-score the same items. Threshold
sensitivity is analysed on separate calibration and evaluation subsets
(`README.md` §Threshold analysis).

---

## 9. Version history

| version | date | change |
|---|---|---|
| 1.0 | 2026-11-22 | Initial protocol. |

*No labels have been collected against any earlier version, so no labels are
invalidated by this document's initial publication.*
