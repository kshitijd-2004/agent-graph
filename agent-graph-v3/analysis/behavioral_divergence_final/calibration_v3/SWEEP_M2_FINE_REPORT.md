# V4.4 fine M2 threshold sweep (q × α, 5×8 = 40 points) — report

Cached checkpoint cache only (`checkpoint_pairs_v44.jsonl` + the calibrated
LEP-free nulls in `m44.py`). No extractor / representation / reference /
label change, no new statistic, no trace re-read, no LLM. M1-C kept as the
selected M1 point (untouched); **M2 not frozen.**

Anchors verified: **q98,α=.05 = M2-A = 2,312/16,767** and
**q98,α=.25 = M2-F = 4,627/16,767** both reproduce the cached values exactly.

## Structure: the grid collapses to 14 operating points

The discrete nulls are step functions of α (the LEP-free disagree-mass pool
is {0,1,2,3}-valued) and the q-quantile pool is a step in q. So several
(q,α) cells map to the *same* operating point:

- α ∈ {.05,.075} and α ∈ {.10,.125} and α ∈ {.15,.175,.20,.25} are three
  distinct mass-regimes (α=.15/.175/.20/.25 all sit between the same two
  tails and are identical; α=.10/.125 identical; α=.05/.075 identical).
- Within a mass-regime, q99 → q98 → q97 → q96 → q95 monotonically adds
  detections.

The 14 distinct points **all sit on the Pareto frontier** (none dominates
another in (M2-rate, B2B-rate)); they form a clean sensitivity↔specificity
chain.

## Distinct operating points (sensitivity ↑ / benign-control ↑)

| op | q, α | M2+ | M2 rate | B2B row-OR (cluster CI 95%) | Δ(M2−B2B) | M3 (v/p) | M3 share | M4 |
|---|---|---|---|---|---|---|---|---|
| O1 | q99, .05 | 2,043 | 12.18% | 5.35% [4.4, 6.3] | +6.8 | 694 (167/527) | 33.9% | 254 |
| O2 | q98, .05 **(M2-A)** | 2,312 | 13.79% | 6.75% [5.8, 7.8] | +7.0 | 781 (178/603) | 33.8% | 267 |
| O3 | q99, .10 | 3,879 | 23.13% | 7.00% [6.0, 8.1] | **+16.1** | 896 (182/714) | 23.1% | 286 |
| O4 | q98, .10 | 4,123 | 24.59% | 8.30% [7.2, 9.4] | **+16.3** | 978 (192/786) | 23.7% | 299 |
| O5 | q99, .15 | 4,342 | 25.90% | 10.26% [9.0, 11.5] | +15.6 | 943 (187/756) | 21.7% | 309 |
| O6 | q99, .175–.25 | 4,400 | 26.24% | 10.57% [9.3, 11.9] | +15.7 | 949 (187/762) | 21.6% | 309 |
| O7 | q98, .15 | 4,575 | 27.29% | 11.53% [10.3, 12.9] | +15.8 | 1,023 (197/826) | 22.4% | 320 |
| O8 | q98, .175–.25 **(M2-F)** | 4,627 | 27.60% | 11.84% [10.5, 13.2] | +15.8 | 1,029 (197/832) | 22.2% | 320 |
| O9 | q97, .15 | 4,755 | 28.36% | 15.27% [13.8, 16.8] | +13.1 | 1,074 | 22.6% | 325 |
| O10 | q97, .175–.25 | 4,807 | 28.67% | 15.58% | +13.1 | 1,080 | 22.5% | 325 |
| O11 | q96, .15 | 4,897 | 29.21% | 17.04% | +12.2 | 1,095 | 22.4% | 327 |
| O12 | q96, .175–.25 | 4,947 | 29.50% | 17.30% | +12.2 | 1,101 | 22.3% | 327 |
| O13 | q95, .15 | 5,143 | 30.67% | 20.87% | +9.8 | 1,120 | 21.8% | 329 |
| O14 | q95, .175–.25 | 5,193 | 30.97% | 21.14% | +9.8 | 1,126 | 21.7% | 329 |

Per-dimension contributions at M2-A vs M2-F: **outcomes 1,600 → 4,038
(Δ+2,438)**, **decisions 1,573 → 2,266 (Δ+693)**; assertions (774) and
actions (681) unchanged (they are the continuous, q-fixed channels).

## Knees (data-driven: where the Δ gap peaks / stops growing)

- **Δ peaks at O4 (q98,α=.10) = +16.3pp, then declines.** The big
  sensitivity jump (O2 → O3/O4) costs almost no benign control (Δ goes
  7.0 → 16.3 while B2B moves 6.75% → 7.0–8.3%). **O4 (q98, α=.10) is the
  sensitivity-specificity knee**: the largest Δ for a 0.6–1.6pp increase
  in the benign control over M2-A.
- **Beyond O4/O8 the gap stops growing and falls** (O9 q97 and O11–O14
  q96/q95 have Δ 13.1 → 9.8pp while B2B climbs 15–21%): past q98, lowering
  q buys raw M2+ but the perturbed-minus-benign separation *erodes* — the
  extra detections are mostly benign.
- Within O4→O8 (α=.10→.25 at q98) Δ is flat (+15.8 vs +16.3) while B2B
  rises 8.3% → 11.8%: **raising α past .10 at q98 costs specificity for
  ~zero Δ gain.**

## Why M2-F's increase is large — and why it is NOT mostly genuine

M2-F (q98,α=.25) adds 2,315 detections over M2-A, driven **entirely by the
two discrete final-scope channels**: outcomes (+2,438 dim-rows) and
decisions (+693). Assertions and actions contribute zero of the increase.

Genuine-vs-exposure split of that increase (the "do not assume all
detections are genuine propagation" check):

| | M2-A (q98,.05) | M2-F (q98,.25) | Δ |
|---|---|---|---|
| M2+ | 2,312 | 4,627 | +2,315 |
| M3 dependency-supported (verified\|plausible + delivery) | 781 (33.8%) | 1,029 (22.2%) | **+248** |
| graph-exposure-only | 1,531 | 3,598 | **+2,067** |

**Of the +2,315 extra detections, only +248 (11%) are dependency-supported
(M3); +2,067 (89%) are structural graph-exposure-only.** So M2-F's larger
perturbed-vs-benign gap is *not* 89% genuine propagation — it is the
discrete outcome/decision channels firing on more LEP rows whose downstream
role was structurally exposed, with most not carrying verified/plausible
reach. M3-share falls 33.8% → 22.2% as α rises: the sensitivity gains
dilute the dependency-supported fraction.

## Which previously-saturated channels become functional

Per-channel activation (mass → null tail → the α at which it fires):

| channel | fires at α | previously-saturated mass |
|---|---|---|
| research/final/decisions mass-3 (1,196 rows) | .05 (tail .0037) | no (already live) |
| research/final/outcomes mass-3 (1,223 rows) | .05 (tail .0036) | no |
| financial/final/dec\|out mass-3 | .05 (tail .0254) | no |
| **research/final/decisions mass-1,2 (240+181 rows)** | **α=.15** | **yes** |
| **research/final/outcomes mass-2 (242 rows)** | **α=.15** | **yes** |
| **research/final/outcomes mass-1 (303 rows)** | **α=.175** | **yes** |
| **code_review/final/outcomes mass-3 (1,893 rows)** | **α=.10** | **yes** |
| **code_review/final/decisions mass-3 (272 rows)** | **α=.15** | **yes** |

So the "saturated channels become functional" mechanism is: at α=.05 the
mass-1/2 (single/double-disagree) mass of the code_review and research
final decision/outcome channels has tail >.05 (0.46–0.63) and **never
fires**; as α crosses .10–.175 these previously-dead masses switch on.
The dominant new source is **code_review/final/outcomes mass-3 (1,893
rows, tail .0764, live from α=.10)** plus the research decision/outcome
mass-1/2 turning on at α=.15–.175. This is exactly the 48/51
outcome-positive dimensional miss the V4.3 investigation attributed to
outcome representation — now the channel fires, but the honest read is
that most of its extra positives are graph-exposure, not verified
propagation.

## Note on M2-F's larger Δ (the user's premise)

The premise that "M2-F shows a substantially larger perturbed-vs-benign
separation" is only true at α∈[.10,.15] (Δ≈16pp); **at M2-F's own α=.25 the
Δ (+15.8pp) is *below* the α=.10–.15 peak (+16.3pp)** — M2-F sits on the
flat/declining part of the Δ curve, and the further increase to
5,143–5,193 (q95) drops Δ to 9.8pp. So M2-F is not the Δ-maximizing
point; **O4 (q98, α=.10) is**, and O4 keeps the same Δ while halving the
B2B cost of M2-F (8.3% vs 11.8%).
