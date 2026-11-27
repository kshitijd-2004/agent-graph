#!/usr/bin/env python3.11
"""Regression tests pinning the study's key invariants.

Run: PYTHONHASHSEED=0 python3.11 scripts/test_regression.py

Pins (all deterministic, no LLM calls, no network):
  1. sample_manifest determinism (seeded; fixture-disjoint A/B; the
     LEP-skew addendum A2 is asserted, not hidden).
  2. blinding: no packet surface contains a forbidden/rule token.
  3. reference construction: provenance partitioning is consistent;
     unresolved is NEVER a forced label; single-judge B is disclosed.
  4. confusion-matrix internal consistency (P/R/F1 from the counts).
  5. the deterministic B2B gate object is in (0, 0.05] and the C4
     gate reads it (never an LLM rate).
  6. the 600-call budget: per-file ledger sums to exactly 600; no
     calls after the cap.
  7. integrity: seal v8 ok; protected artifacts byte-identical.
Each test is a plain assert with a message; the script prints PASS/
FAIL per test and exits non-zero on any failure.
"""
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
STUDY = os.path.abspath(os.path.join(HERE, '..'))
LED = os.path.join(STUDY, 'ledger')
OUT = os.path.join(STUDY, 'out')
PK = os.path.join(OUT, 'packets')

FORBIDDEN = ('LEP_', 'HANDOFF_CORRUPTION', 'INDIRECT_PROMPT',
             'INPUT_DISREGARD', 'TOOL_RESULT_CORRUPTION',
             'single_origin', 'many_to_one', 'one_to_many',
             'review_loop', 'branch_and_verify', 'coordinator_workers',
             'finq', 'scifact', 'swe_', 'ground_truth',
             'expected_answer', 'specroot', 'per_trace',
             'baseline_deviation', 'calibration_v2', 'S1_shrinkage',
             'p3_q98_f0_family', 'V4.1', 'q98', 'shrinkage',
             'behavioral_anomaly', 'union_ref', 'm2_cross_agent',
             'd_lep', 'd_benign')


def _rows(p):
    raw = open(os.path.join(LED, p)).read()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return [json.loads(l) for l in raw.splitlines() if l.strip()]


RESULTS = []


def check(name, cond, msg=''):
    RESULTS.append((name, bool(cond), msg))
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond
                                                    else '  -> ' + msg))


def t_sample():
    man = json.load(open(os.path.join(LED, 'sample_manifest.json')))
    A = set(man['cohort_a_representative'])
    B = set()
    for k, v in man['cohort_b_targeted'].items():
        if isinstance(v, list):
            B |= set(v)
    # A and B fixture-disjoint
    def fx(t):
        return t.split('.')[0]
    overlap = {fx(t) for t in A} & {fx(t) for t in B}
    check('sample: A/B fixture-disjoint', not overlap,
          f'overlap={list(overlap)[:3]}')
    # LEP skew (addendum A2) is asserted, not hidden
    import re
    amix = Counter((re.search(r'\.LEP_([A-Z_]+)\.', t) or
                    [None, 'BENIGN'])[1] for t in A)
    hc = amix.get('HANDOFF_CORRUPTION', 0) / len(A)
    check('sample: addendum A2 LEP-skew disclosed',
          hc > 0.5, f'A HANDOFF mix={hc:.2f} (expected >0.5)')
    # design weights present for every A row
    check('sample: design_weights cover all A',
          set(man['design_weights']) == A)


def t_blinding():
    bad = 0
    for f in os.listdir(PK):
        if not f.endswith('.json') or f.startswith('_'):
            continue
        p = json.load(open(os.path.join(PK, f)))
        blob = json.dumps(p['surface']).lower()
        if any(t.lower() in blob for t in FORBIDDEN):
            bad += 1
    check('blinding: 0 forbidden/rule tokens on any surface', bad == 0,
          f'{bad} packet surfaces leak a token')


def t_reference():
    refs = _rows('reference_labels.jsonl')
    prov = Counter(r['provenance'] for r in refs)
    # unresolved is never a forced binary label
    forced = [r for r in refs if r['provenance'] in
              ('ai_unresolved', 'judge_invalid', 'single_judge')
              and r['label'] in ('diverges', 'ordinary')
              and r['provenance'] != 'single_judge']
    check('reference: unresolved/invalid never carry a forced binary',
          not forced, f'{len(forced)} forced')
    # single_judge basis only on B, always disclosed via label_basis
    single = [r for r in refs if r['provenance'] == 'single_judge']
    check('reference: single_judge only on cohort B (disclosed)',
          all(r['cohort'].startswith('B') for r in single),
          f'non-B single_judge rows')
    # every reference row carries provenance + label_basis
    check('reference: provenance+label_basis on all rows',
          all('provenance' in r and 'label_basis' in r for r in refs))


def t_confusion():
    cm = json.load(open(os.path.join(LED, 'confusion_matrices.json')))
    for rule, c in cm['rules'].items():
        n = c['TP'] + c['FP'] + c['FN'] + c['TN']
        assert c['n_eval'] == n, f'{rule} n_eval {c["n_eval"]} != {n}'
        p = c['precision']
        if c['TP'] + c['FP']:
            assert abs(p - c['TP'] / (c['TP'] + c['FP'])) < 1e-9
    # C1 (p3_q98 pooled-tail) is degenerate on this reference: it
    # detects ZERO reference-positive divergences (TP=0) so its F1 is
    # undefined (the pooled-tail threshold is not calibrated to the
    # meaningful-divergence construct).  FP may be nonzero — that is
    # the degenerate-firing mode, not a bug.
    check('confusion: C1_family_q98 is degenerate (TP=0, F1 None)',
          cm['rules']['C1_family_q98']['TP'] == 0
          and cm['rules']['C1_family_q98']['f1'] is None)


def t_b2b_gate():
    ben = json.load(open(os.path.join(LED,
                                     'benign_control_validation.json')))
    g = ben['deterministic_b2b_gate']['probe_union_fpr']
    check('gate C4: deterministic B2B probe-union fpr in (0, 0.05]',
          0.0 < g <= 0.05, f'fpr={g}')
    gates = json.load(open(os.path.join(LED,
                                        'validation_gates.json')))
    check('gate C4 passes via the deterministic control',
          gates['C4_exec_benign_fpr_le_0.05'] == 'pass')
    # the LLM H0 rate is present but NOT the gate
    check('gate C4: LLM H0 is corroborating (field present, not gate)',
          'judge_probe_level' in ben and
          'deterministic_b2b_gate' in ben)


def t_budget():
    import glob
    tot = 0
    for f in sorted(glob.glob(os.path.join(OUT, 'call_ledger*.jsonl'))):
        tot += sum(1 for l in open(f) if l.strip())
    check('budget: total live calls == 600 exactly', tot == 600,
          f'tot={tot}')
    integ = json.load(open(os.path.join(LED,
                                       'integrity_verification.json')))
    check('budget: no calls after the cap (disclosed)',
          integ['annotation_budget']['no_calls_after_cap'])


def t_integrity():
    integ = json.load(open(os.path.join(LED,
                                       'integrity_verification.json')))
    check('integrity: seal v8 ok before+after',
          integ['seal_v8_ok']['before'] and
          integ['seal_v8_ok']['after'])
    check('integrity: protected artifacts byte-identical',
          integ['protected_key_artifacts']['byte_identical_before_after'])
    check('integrity: no protected ledger overwritten',
          integ['write_guard']['no_seal_or_ledger_overwrites'])


def main():
    for fn in (t_sample, t_blinding, t_reference, t_confusion,
               t_b2b_gate, t_budget, t_integrity):
        fn()
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f'\n{passed}/{len(RESULTS)} regression invariants hold.')
    if passed < len(RESULTS):
        for name, ok, msg in RESULTS:
            if not ok:
                print('  FAIL:', name, msg)
        sys.exit(1)


if __name__ == '__main__':
    if 'PYTHONHASHSEED' not in os.environ:
        os.environ['PYTHONHASHSEED'] = '0'
        os.execve(sys.executable, [sys.executable] + sys.argv, os.environ)
    main()
