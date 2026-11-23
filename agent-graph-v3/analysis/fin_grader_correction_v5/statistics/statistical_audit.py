#!/usr/bin/env python3.11
"""Track A1 - reproducible statistical audit of the financial-grader acceptance
gates (FINAL_PROTOCOL.md v1 / gate_power.json predecessor).

Read-only over the dataset; writes only ``statistical_audit.json`` +
``statistical_audit.md`` next to this file.

Corrections vs the previous ``gate_power.json``:
  C-1  Denominators. The previous global-gate cutoffs (26 / 96 / 61) were all
       computed against n=775. G1-G3 are *rate* gates whose denominators are
       defined by the gate (grader-correct count, grader-incorrect count,
       reference-determinate count). We PRE-REGISTER the fixed-denominator
       form (all rates over N=775) as the gating statistic, and the
       conditional form as a diagnostic. Both cutoffs are reported.
  C-2  "Power at the acceptance threshold" was mislabeled. For a
       certification gate (accept iff one-sided 95% UB <= thr) the quantity
       P(reject | true rate = thr) is ~0.95-0.99 BY CONSTRUCTION (a
       threshold-level extractor cannot be certified) - it is not power in
       the classical sense. This audit reports, per gate, P(pass | p_true)
       for p_true in {0, .5thr, thr, 1.5thr, 2thr, 3thr} with the cutoff
       fixed by the pre-registered threshold, and labels the columns
       unambiguously.
  C-3  Rare-class feasibility. The previous min_n values (73) were computed
       with a mismatched two-sided interval. Recomputed here with the
       explicitly selected one-sided Wilson method; the achievable/
       not-certifiable status of the relaxed 15% rare-class gates is
       determined at the ACTUAL sealed n (44 / 34 / 20).
  C-4  No double counting: G1/G2/G3 error sets are disjoint by construction
       (a row can only be grader-correct, grader-incorrect, or
       grader-ungradeable); G5 (3-class agreement) overlaps G1-G3 and is
       reported separately, never summed with them.
"""
import json, math
from math import comb, lgamma
from pathlib import Path
import sys

HERE = Path(__file__).parent
N_SEALED = 775            # sealed acceptance population (unique executions)
ALPHA = 0.05
Z = 1.96

# ---------------------------------------------------------------- intervals
def wilson_ub(k, n, z=Z):
    """One-sided 95% Wilson upper bound of a binomial proportion (k/n)."""
    if n == 0:
        return None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    m = (z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)) / den
    return min(1.0, c + m)


def wilson_lb(k, n, z=Z):
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    m = (z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)) / den
    return max(0.0, c - m)


def cp_ub(k, n, alpha=ALPHA):
    """Clopper-Pearson exact one-sided upper bound (cross-check)."""
    if n == 0:
        return None
    if k == n:
        return 1.0
    return 1.0 - (alpha ** (1.0 / n)) if k == 0 else _cp_ub_general(k, n, alpha)


def _cp_ub_general(k, n, alpha):
    # inexact-but-stable: binary search on p for P(X >= k) = alpha
    lo, hi = k / n, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        # P(X >= k) for Bin(n, mid) via log-space binomial CDF
        p_ge = _binom_cdf_ge(k, n, mid)
        if p_ge > alpha:
            lo = mid
        else:
            hi = mid
    return hi


def _logbinom(n, k):
    return lgamma(n + 1) - lgamma(k + 1) - lgamma(n - k + 1)


def _binom_cdf_ge(k, n, p):
    """P(X >= k) for Bin(n, p), exact in log-space; p in [0,1)."""
    if p >= 1.0:
        return 1.0 if k <= n else 0.0
    if p <= 0.0:
        return 1.0 if k == 0 else 0.0
    lp = math.log(p)
    l1mp = math.log1p(-p)
    total = 0.0
    for i in range(k, n + 1):
        total += math.exp(_logbinom(n, i) + i * lp + (n - i) * l1mp)
    return min(1.0, total)


def kmax(n, thr, method='wilson'):
    """Max observed errors k (out of n) whose one-sided 95% UB <= thr."""
    fn = wilson_ub if method == 'wilson' else cp_ub
    best = -1
    for k in range(n + 1):
        ub = fn(k, n)
        if ub is not None and ub <= thr:
            best = k
    return best


def binom_cdf(kmax, n, p_true):
    """P(X <= kmax) under Bin(n, p_true), exact (log-space)."""
    if p_true <= 0:
        return 1.0 if kmax >= 0 else 0.0
    if p_true >= 1:
        return 0.0 if kmax < n else 1.0
    c = 0.0
    for i in range(0, kmax + 1):
        c += math.exp(_logbinom(n, i) + i * math.log(p_true)
                      + (n - i) * math.log(1 - p_true))
    return min(1.0, c)


# ---------------------------------------------------------------- gates
def build_gates():
    gates = {}

    # G1 false-correct: corrected grader says CORRECT but reference says not.
    #    Gating statistic (fixed denominator): FP_correct / 775 <= 0.05
    g1 = {
        'gate': 'G1_false_correct',
        'numerator': 'rows grader-correct & reference-not-correct (FP_correct)',
        'denominator_gating': 'N_sealed = 775 (fixed, pre-registered)',
        'denominator_diagnostic': 'n_grader_correct (conditional FP rate)',
        'threshold': 0.05,
        'test': 'one-sided 95% Wilson UB(FP/775) <= 0.05',
        'kmax_fixed_775': kmax(775, 0.05),
        'kmax_diagnostic': {},
        'pass_profile_fixed_denominator': {},
    }
    # diagnostic cutoffs for plausible achieved denominators of grader-correct
    for n_ach in (350, 400, 450, 500, 565):
        g1['kmax_diagnostic'][n_ach] = kmax(n_ach, 0.05)
    for pt in (0.0, 0.02, 0.05, 0.075, 0.10, 0.15):
        km = g1['kmax_fixed_775']
        g1['pass_profile_fixed_denominator'][f'{pt:.3f}'] = {
            'P_pass_given_true_rate': round(binom_cdf(km, 775, pt), 4),
            'P_fail_given_true_rate': round(1 - binom_cdf(km, 775, pt), 4),
        }
    gates['G1'] = g1

    # G2 false-incorrect: grader-INCORRECT & reference-correct (or reference-
    # ungradeable-determinate-missed). Rate over 775 <= 0.15.
    g2 = {
        'gate': 'G2_false_incorrect',
        'numerator': 'rows grader-incorrect & reference-not-incorrect (FN_incorrect)',
        'denominator_gating': 'N_sealed = 775 (fixed, pre-registered)',
        'denominator_diagnostic': 'n_grader_incorrect (conditional FN rate)',
        'threshold': 0.15,
        'test': 'one-sided 95% Wilson UB(FN/775) <= 0.15',
        'kmax_fixed_775': kmax(775, 0.15),
        'pass_profile_fixed_denominator': {},
    }
    km = g2['kmax_fixed_775']
    for pt in (0.0, 0.05, 0.10, 0.15, 0.225, 0.30, 0.45):
        g2['pass_profile_fixed_denominator'][f'{pt:.3f}'] = {
            'P_pass_given_true_rate': round(binom_cdf(km, 775, pt), 4),
            'P_fail_given_true_rate': round(1 - binom_cdf(km, 775, pt), 4),
        }
    gates['G2'] = g2

    # G3 false-ungradeable: reference determinate & grader ungradeable.
    g3 = {
        'gate': 'G3_false_ungradeable',
        'numerator': 'rows reference-determinate & grader-ungreadeable (false-NG)',
        'denominator_gating': 'N_sealed = 775 (fixed, pre-registered)',
        'denominator_diagnostic': 'n_reference_determinate (conditional false-NG rate)',
        'threshold': 0.10,
        'test': 'one-sided 95% Wilson UB(falseNG/775) <= 0.10',
        'kmax_fixed_775': kmax(775, 0.10),
        'pass_profile_fixed_denominator': {},
    }
    km = g3['kmax_fixed_775']
    for pt in (0.0, 0.05, 0.10, 0.15, 0.20, 0.30):
        g3['pass_profile_fixed_denominator'][f'{pt:.3f}'] = {
            'P_pass_given_true_rate': round(binom_cdf(km, 775, pt), 4),
            'P_fail_given_true_rate': round(1 - binom_cdf(km, 775, pt), 4),
        }
    gates['G3'] = g3

    # G4 appropriate abstention >= 90%: among rows the reference marks
    # should-abstain (ambiguous/omitted), the grader abstains. Lower-bound test.
    g4 = {
        'gate': 'G4_appropriate_abstention',
        'numerator': 'rows reference-should-abstain & grader-abstain',
        'denominator_gating': 'n_reference_should_abstain (ENDGENOUS; min-n guard n>=30)',
        'threshold': 0.90,
        'test': 'one-sided 95% Wilson LB(abstain/n_ref_abstain) >= 0.90; if '
                'n_ref_abstain < 30 -> report descriptive only (NOT certifiable)',
        'note': 'n_ref_abstain is unknown until annotation; sealed-set class '
                'ceiling for omission is 20 (flag_tool_only + G_NO_NUMBER), so '
                'the min-n guard will likely bind: pre-registered as '
                'descriptive-only unless reference should-abstain count >= 30.',
        'max_errors_for_lb90_by_n': {},
    }
    for n_ach in (30, 40, 50, 75, 105):
        # smallest k_hits such that one-sided 95% Wilson LB(k_hits/n) >= 0.90
        min_hits = None
        for k_hit in range(n_ach + 1):
            lb = wilson_lb(k_hit, n_ach)
            if lb is not None and lb >= 0.90:
                min_hits = k_hit
                break
        g4['max_errors_for_lb90_by_n'][n_ach] = {
            'n': n_ach,
            'min_hits_to_pass': min_hits,
            'max_miss_to_pass': (n_ach - min_hits) if min_hits is not None else None,
        }
    gates['G4'] = g4

    # G5 gradeability (3-class) accuracy >= 90%: agreement over 775.
    g5 = {
        'gate': 'G5_gradeability_accuracy',
        'numerator': 'rows where grader 3-class verdict == reference verdict',
        'denominator_gating': 'N_sealed = 775 (fixed)',
        'threshold': 0.90,
        'test': 'one-sided 95% Wilson LB(agree/775) >= 0.90  '
                '(equiv. UB(disagree/775) <= 0.10)',
        'kmax_disagree_775': kmax(775, 0.10),
        'note': 'G5 error set OVERLAPS G1-G3 (a false-correct row also breaks '
                '3-class agreement). G5 is reported separately and is NEVER '
                'summed with G1-G3.',
    }
    gates['G5'] = g5

    # G6 transition matrix (frozen x corrected x reference) - diagnostic, no gate.
    gates['G6'] = {
        'gate': 'G6_transition_matrix',
        'role': 'diagnostic only (no threshold); required in the acceptance report',
        'rows': 'frozen task_grade x corrected verdict x reference verdict',
    }
    return gates


def build_rare_class_gates():
    """Relaxed one-sided 95% UB(error-rate) <= 0.15 gates for the three
    underpowered rare classes, evaluated at the ACTUAL sealed n. Achievability
    is computed from zero-error up; if even 0 errors cannot certify 15%, the
    gate is NOT CERTIFIABLE at this sample size and is reported as such."""
    out = {}
    for cls, n in (('sign', 44), ('trc_adopt', 34), ('omission', 20)):
        ub0 = wilson_ub(0, n)
        feasible0 = ub0 is not None and ub0 <= 0.15
        # max observed errors still certifying <=15%
        k = 0
        maxk = None
        for kk in range(n + 1):
            ub = wilson_ub(kk, n)
            if ub is not None and ub <= 0.15:
                maxk = kk
            else:
                break
        out[cls] = {
            'n_sealed': n,
            'relaxed_threshold': 0.15,
            'interval_method': 'one-sided 95% Wilson',
            'ub_at_0_errors': round(ub0, 4),
            'max_observed_errors_to_certify_15pct': maxk,
            'certifiable_at_relaxed_gate': (maxk is not None),
            'if_not_certifiable': ('report as NOT CERTIFIABLE, descriptive only; '
                                   'do NOT relax further' if maxk is None else ''),
        }
    return out


def main():
    gates = build_gates()
    rare = build_rare_class_gates()

    audit = {
        'meta': {
            'interval_method': 'one-sided 95% Wilson (z=1.96); Clopper-Pearson '
                               'exact used as cross-check only',
            'N_sealed': N_SEALED,
            'corrections_vs_previous': [
                'C-1 denominators made explicit (fixed 775 gating vs conditional diagnostic)',
                'C-2 power relabeled as P(pass|true rate) with cutoff fixed by threshold; '
                'the prior "power at threshold ~97-98%" was the gate\'s own strictness '
                'at the boundary, not classical power',
                'C-3 rare-class feasibility recomputed with the selected one-sided Wilson '
                'at actual sealed n; prior min_n=73 used a mismatched two-sided interval',
                'C-4 overlap policy: G5 disjoint-in-reporting from G1-G3; no summed errors',
            ],
            'cp_crosscheck': {
                'g1_kmax_775_wilson': gates['G1']['kmax_fixed_775'],
                'g1_kmax_775_cp': kmax(775, 0.05, 'cp'),
                'g2_kmax_775_wilson': gates['G2']['kmax_fixed_775'],
                'g2_kmax_775_cp': kmax(775, 0.15, 'cp'),
                'g3_kmax_775_wilson': gates['G3']['kmax_fixed_775'],
                'g3_kmax_775_cp': kmax(775, 0.10, 'cp'),
            },
        },
        'global_gates': gates,
        'rare_class_gates_option_A': rare,
    }
    out = HERE / 'statistical_audit.json'
    out.write_text(json.dumps(audit, indent=1))
    print(json.dumps({k: audit['meta']['cp_crosscheck'][k]
                      for k in audit['meta']['cp_crosscheck']}, indent=1))
    print('G1 kmax(775,5%):', gates['G1']['kmax_fixed_775'])
    print('G2 kmax(775,15%):', gates['G2']['kmax_fixed_775'])
    print('G3 kmax(775,10%):', gates['G3']['kmax_fixed_775'])
    print('G5 kmax_disagree:', gates['G5']['kmax_disagree_775'])
    print('rare:', json.dumps(rare, indent=1))
    print('wrote', out)


if __name__ == '__main__':
    main()
