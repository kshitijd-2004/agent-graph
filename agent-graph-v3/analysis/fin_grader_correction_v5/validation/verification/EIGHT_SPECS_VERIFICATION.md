# Verification of the 8 Unresolved (Tier-2) Financial Specifications

Phase-3 instruction: verify each candidate against the original question and source
documents before any specification change. All evidence below is from
`analysis/bnd_v41_cw_swe200_provisional/specroot/<fid>/{manifest.json,documents/,notes/}`.
No repository file was modified; artifacts live in `/tmp/agrade_audit2/validation_dataset/verification/`.

## Method (reproducible)
For each spec:
1. Read `required_facts.fact_1..4` (values + units) and `answer` (value, unit, label, value_strings).
2. Recompute the answer from the 4 facts under every plausible operation (±, ×, ÷ both orders, ×100, and unit-scaled ×1e3/1e6) — script `value_audit.py` (this session) does this for all 300 specs; `verification/value_audit.json` records each spec's matching operation.
3. Locate the governing table in `documents/report.md` (and `call_transcript.md` where the question originates there) and confirm which figure(s) the question asks for.
4. Judge dimension: does the declared `unit` describe the true dimension of the quantity the *question* requests?

## Verdicts

| Spec | Question (truncated) | Declared unit | Recomputed GT | Verdict | Evidence |
|---|---|---|---|---|---|
| `fin_finqa_029_v_2009` | average payment volume per transaction of visa inc? | USD millions | 48.0952 = 2727/56.7 | **UNIT DEFECT** | report.md:9 — table header "Payments Volume (billions) … Total Transactions (billions)"; Visa row "$2,727 \| 56.7". Answer is $48.09 **per transaction**; dividing by 1e6 (the frozen extractor does) yields 4.81e-05. |
| `fin_finqa_043_swks_2007` | net change in pension liability balance Sep 2006→Sep 2007 | USD thousands | 385.0 = 159+226; cross-check −599→−214 = +385 | **VALID — NO CHANGE** | report.md table "(in thousands) … Balance as of September 29, 2006 $(599) … Balance as of September 28, 2007 $(214)". A genuine signed currency delta in thousands. Earlier dimensional scan's "scaled pct_change" hit was coincidental (a÷b×100 ≈ 73.6 ≠ 385; the actual match is a simple sum). |
| `fin_finqa_050_hum_2016` | number of shares issued a dividend (2014), in millions | USD millions | 154.5454 = 170/1.1 | **UNIT DEFECT** | report.md Dividends table "2014 \| $1.10 \| $170" ("Total Amount (in millions)"). 170 M USD ÷ $1.10/share = 154.545 **million shares**, not USD. |
| `fin_finqa_051_dre_2008` | net income per common share ("in 2007") | USD thousands | 1.5633 = 217,692/139,255 | **UNIT DEFECT** | report.md: "$217,692" and "139,255" (thousands). EPS = 1.5633 **USD per share**; declaring thousands makes the extractor read 0.00156. (Minor label bug: the values come from the *2008* column; the year wording is off, no value impact.) |
| `fin_finqa_116_stt_2007` | number of outstanding shares based on EPS (in millions) | USD millions | 331.2253 = 838/2.53 | **UNIT DEFECT** | report.md: "Net income as reported $838", "Basic—as reported $2.53"; doc text: "Basic earnings per share is calculated by dividing net income … by the weighted-average number of common shares outstanding". Answer is 331.23 **million shares**. |
| `fin_finqa_210_hii_2017` | return on total assets during 2014 | USD millions | 0.0542 = 338/6239 | **UNIT DEFECT** | report.md: "Net earnings … 338", "Total assets … 6,239" (Dec 31 2014 column). ROA is **dimensionless** (0.0542) or 5.42% — never USD millions. |
| `fin_finqa_219_adi_2015` | **total** fair value of outstanding RSUs at Oct 31 2015 | USD thousands | GT 56.6926 = 2698/47.59 | **VALUE BUG (new)** | report.md RSU table: "2,698 \| $47.59". The question requests the **product** 2,698 × 47.59 = **$128,397.8 thousand** (≈$128.4 M). The spec's GT is the *division* (2698 ÷ 47.59 = 56.6926) — an operation error in spec generation. The declared unit ("USD thousands") is itself fine; `value` is wrong. Also: `call_transcript.md` mis-annotates the per-share figure as "(USD thousands)" — a generation artifact that will confuse annotators. |
| `fin_finqa_292_abmd_2007` | how much of total contractual commitments are current? | USD thousands | 0.5948 = 8381/14090 | **UNIT DEFECT** | report.md: "table (in thousands) … Total Obligations $14,090 \| $8,381 (Less than 1 Year) …". Answer is the **fraction** 0.5948 (or 59.48%); declaring thousands makes the extractor read 0.5948e-3. |

## Consequences for the correction manifest

1. **35 confirmed unit defects** = 29 label-unambiguous (ratio/portion/margin/leverage… in currency units) + 6 of the 8 verified here (`029, 050, 051, 116, 210, 292`). All six have `value_strings` that equal the dimensionless/count figure, confirming the intended value and that only `unit` is wrong.
2. **1 confirmed value bug** (`fin_finqa_219_adi_2015`): correct GT is 128,397.8 (USD thousands) or 128.40 (USD millions). This is **not** a unit fix; it requires an `answer.value` + `value_strings` correction, which changes the grading target of a fixture mid-flight. Options: (a) correct value+unit in the versioned spec revision and treat affected executions (112-tier2 subset) as re-specified; (b) exclude this fixture from the corrected grading population and report it as a spec defect. **Recommend (b)** — an answer-value change retroactively alters what the agent was asked to compute, which is a task-definition change, not a grader fix.
3. **2 remain valid, no change** (`043`, and by extension any similar "difference in … in thousands/millions" specs, e.g. `fin_finqa_180_fis_2016` whose GT −1.0 M is a genuine currency delta).
4. **20 negative-GT specs**: verified earlier from labels — 18 are signed change/difference/drop questions (sign is the intended reading); 2 (`097_aptv`, `104_alle`) are genuine negative returns. No spec change; the sign handling belongs to the extractor.

## Full-population value-consistency audit (this session)

`value_audit.py` re-derives every spec's `answer.value` from its 4 facts: **300/300** finq specs have a value consistent with *some* 2-fact operation under clean unit factors (0 unresolved). The 2 multi-fact fixtures (`financial_clean`, `financial_version_conflict`) have no answer key (32 executions, excluded as `ungradeable_by_design`). `fin_finqa_219` passes this audit (its value is a valid fact-operation) yet is still a semantic bug — the audit is necessary but not sufficient; the 8 document checks above close the gap for the flagged tier. **Caveat: the audit cannot detect operation-choice bugs outside the flagged 37+8 set; no evidence of such bugs elsewhere was found.**
