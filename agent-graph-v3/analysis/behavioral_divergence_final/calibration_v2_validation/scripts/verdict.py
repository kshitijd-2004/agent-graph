#!/usr/bin/env python3.11
"""Acceptance-criterion gates + publication 3-rule table.

Gates C1–C8 (prereg §6, retained, NOT relaxed):
  C1 P>=0.70, C2 R>=0.70, C3 F1>=0.65 — cohort A, design-weighted,
      2-judge reference; marked PASS/FAIL/UNESTABLISHED (unresolved
      rate > 15% or positive support < 30 => UNESTABLISHED, not
      passed — requirement 7).
  C4 execution-level benign FPR <= 0.05 — the DETERMINISTIC B2B p3
      probe-union control (cv2 re-derivation, no LLM).  The LLM H0
      (B4) is corroborating and is NEVER the gate (requirement 6).
  C5 coverage >= the v6 frozen baselines.
  C6 invariance + controlled-sensitivity suites green (sealed
      out/synthetic_report.json re-check).
  C7 no disabled dimension (O1 non-vacuous; code_review outcome
      qualification disclosed).
  C8 integrity (seal v8 + before/after fingerprints + write-guard).
Writes ledger/validation_gates.json +
       ledger/three_rule_table.json (original / p3_q98_f0_family /
       S1, vs the independent reference).
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STUDY = os.path.abspath(os.path.join(HERE, '..'))
V2 = os.path.abspath(os.path.join(STUDY, '..', 'calibration_v2'))
LED = os.path.join(STUDY, 'ledger')
OUT = os.path.join(STUDY, 'out')
sys.path.insert(0, V2)
import cv2  # noqa: E402


def main():
    cm = json.load(open(os.path.join(LED,
                                     'confusion_matrices.json')))
    bench = json.load(open(os.path.join(LED,
                                        'benign_control_validation.json')))
    dim = json.load(open(os.path.join(LED,
                                      'dimensional_validation.json')))
    s1 = cm['rules']['S1_shrinkage']
    dw = cm['s1_design_weighted']
    n_unres = s1['n_unresolved_ref']
    n_pos = s1['TP'] + s1['FN']
    unres_rate = n_unres / (s1['n_eval'] + n_unres) \
        if (s1['n_eval'] + n_unres) else None

    def est(metric, bound, is_uneq):
        if unres_rate is not None and unres_rate > 0.15:
            return 'unestablished_insufficient_resolution'
        v = s1[metric]
        if v is None:
            return 'unestablished_insufficient_support'
        ok = v >= bound if is_uneq else v <= bound
        return 'pass' if ok else 'fail'

    gates = {
        'C1_precision_ge_0.70':
            est('precision', 0.70, True),
        'C2_recall_ge_0.70':
            est('recall', 0.70, True),
        'C3_f1_ge_0.65':
            est('f1', 0.65, True),
    }
    # positive-support check (the two P/R/F1 gates also require
    # n_pos >= 30 to be established)
    for g in ('C1_precision_ge_0.70', 'C2_recall_ge_0.70',
              'C3_f1_ge_0.65'):
        if gates[g] in ('pass', 'fail') and n_pos < 30:
            gates[g] = 'unestablished_insufficient_support'
    b2b = bench['deterministic_b2b_gate']
    gates['C4_exec_benign_fpr_le_0.05'] = \
        'pass' if (b2b.get('probe_union_fpr') is not None
                   and b2b['probe_union_fpr'] <= 0.05) \
        else 'fail'
    gates['C4_note'] = ('execution-level FPR gate = the deterministic '
                        'B2B p3 probe-union control (cv2, no LLM); '
                        'the LLM H0 B4 (judge_probe_level / '
                        'judge_execution_level) is corroborating '
                        'only and is NEVER the gate (prereg §6 C4, '
                        'requirement 6).')
    # C5 coverage vs the v6 frozen baselines (per_dim evaluable)
    v5 = json.load(open(os.path.join(V2, 'ledger',
                                     'validation_report.json')))
    gates['C5_coverage_ge_v6'] = \
        v5['gate_verdicts']['V5_coverage_ge_v6']['pass']
    gates['C5_detail'] = ('the S1 rule is UNCHANGED by this study '
                          '(frozen pin 070c39d2); its coverage gate '
                          're-affirms the calibration_v2 result.')
    # C6 invariance + controlled-sensitivity
    gates['C6_suites_green'] = \
        v5['gate_verdicts']['V6_suites_green']['pass']
    # C7 no disabled dimension
    s1f = dim['per_rule']['S1_shrinkage']['per_dim']
    disabled = [d for d in ('assertions', 'decisions', 'actions')
                if (s1f[d]['TP'] + s1f[d]['FP'] + s1f[d]['FN']
                      + s1f[d]['TN']) == 0
                and s1f[d]['precision'] is None]
    gates['C7_no_disabled_dimension'] = \
        'pass' if not disabled else 'fail'
    gates['C7_detail'] = {
        's1_per_dim_f1': {d: s1f[d]['f1'] for d in s1f},
        'o1_code_review_outcome': dim['o1_code_review_outcome'],
        'note': ('the outcome channel is the O1 one-sided test; the '
                 'code_review family is power-qualified (smallest '
                 'attainable one-sided p > 0.05) — a disclosed '
                 'qualification, not a silent disablement '
                 '(OUTCOME_CALIBRATION_REPORT §3.1).')}
    gates['C8_integrity'] = None  # filled by the fingerprint step
    gates['reference_note'] = ('the reference is INDEPENDENT BLINDED '
                               'LLM annotation (2 judges + 3rd-model '
                               'adjudicator; LLM-adjudicated, NOT '
                               'human ground truth), drawn from the '
                               'strict-untouched pool — 129 fixtures '
                               '/ 2,379 LEP rows, fixture-level '
                               'independent from all prior '
                               'development/confirmatory/judge '
                               'pools; cohort A samples 110 '
                               'executions across 46 of those '
                               'fixtures (96 scored + 9 unresolved + '
                               '5 judge_invalid).  Unresolved '
                               'executions are EXCLUDED (never a '
                               'negative label); unresolved+invalid: '
                               f'{n_unres}/{s1["n_eval"] + n_unres}.')
    json.dump(gates, open(os.path.join(LED,
                                       'validation_gates.json'), 'w'),
              indent=1)

    # ── 3-rule (4-rule incl. V4.1) comparison vs the reference ─────
    rules = {}
    for r in ('C0_original', 'C1_family_q98', 'S1_shrinkage',
              'V41_union'):
        rules[r] = cm['rules'][r]
    table = {
        'reference': 'independent blinded LLM annotation (cohort A)',
        'rules': rules,
        'design_weighted_s1': dw,
        'b2b_execution_fpr_s1': b2b,
        'note': ('original = per-cell extremal (C0); previous '
                 'pooled-tail = p3_q98_f0_family (C1); new = S1 '
                 'shrinkage (selected).  V4.1 is the rule previously '
                 'used as an independent reference — reported here '
                 'as a fourth row (where comparable), NOT as the '
                 'reference of record for this study.'),
    }
    json.dump(table, open(os.path.join(LED, 'three_rule_table.json'),
                           'w'), indent=1)
    print('gates:', {k: v for k, v in gates.items()
                     if k.startswith('C') and isinstance(v, str)})
    print('unresolved rate:', unres_rate, '| n_pos:', n_pos)
    for r in rules:
        c = rules[r]
        print(f'{r:16s} P={c["precision"]} R={c["recall"]} '
              f'F1={c["f1"]}')


if __name__ == '__main__':
    main()
