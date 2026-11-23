#!/usr/bin/env python3
"""
Regression tests for measurement v6.

Every test corresponds to a CONFIRMED defect from the independent audit, or to
a methodology-preservation invariant from the correction brief.  Run:

    python3.11 analysis/measurement_v6/test_regression_v6.py

Exit code 0 = all pass.  No network, no GPU, no Slurm, read-only w.r.t. the
repository (this file only reads its own package).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from extract_code import extract_issue_stances                      # noqa: E402
from extract_core import GroundTruthLeak, strip_gt, assert_gt_free_spec  # noqa: E402
from extract_financial import extract_facts, extract_final_answer    # noqa: E402
from extract_research import extract_claim_verdicts                 # noqa: E402
from graders import (canonical_verdict, grade_code, grade_financial,  # noqa: E402
                     grade_research)
from distances import map_distance, outcome_distance_code, total_variation  # noqa: E402
from m4_matched import (DEGRADED, IMPROVED, MIXED_UNAVAILABLE,       # noqa: E402
                        PERSISTENT_CORRECT, PERSISTENT_FAILURE,
                        classify_matched, m4_historical, m4_matched,
                        normalise_grade)
from union import benign_thresholds, per_dimension, union             # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    if not cond:
        print('  FAIL %-58s %s' % (name, detail))


# ── D1: modal denial must not become SUPPORTED ───────────────────────────────
def test_research_modal_denial():
    spec = {'required_facts': {'claim_1_verdict': {}}}
    o, _ = extract_claim_verdicts('I cannot say Claim 1 is supported.', spec)
    f = o['claim_1_verdict']
    check('D1 cannot-say is not SUPPORTED', f.value != 'SUPPORTED', repr(f))
    check('D1 cannot-say abstains', f.state == 'unavailable', repr(f))


# ── D2: hedged/inconclusive verdict takes the operative (last) polarity ─────
def test_research_hedge():
    spec = {'required_facts': {'claim_1_verdict': {}}}
    o, _ = extract_claim_verdicts(
        'Claim 1 is supported only in one study; the evidence is inconclusive.',
        spec)
    check('D2 hedge resolves to NOT_ENOUGH',
          o['claim_1_verdict'].value == 'NOT_ENOUGH', repr(o['claim_1_verdict']))


# ── D3: restated claim body does not hide the verdict ───────────────────────
def test_research_restated_body():
    spec = {'required_facts': {'claim_1_verdict': {}}}
    o, _ = extract_claim_verdicts(
        'Claim 1: the intervention reduces mortality. This is supported.', spec)
    check('D3 restated body -> SUPPORTED',
          o['claim_1_verdict'].value == 'SUPPORTED', repr(o['claim_1_verdict']))


# ── research: negation flips SUPPORT, no cross-claim bleed ──────────────────
def test_research_negation_and_bleed():
    spec = {'required_facts': {'claim_1_verdict': {}, 'claim_2_verdict': {}}}
    o, _ = extract_claim_verdicts('Claim 1 is not supported by the evidence.',
                                  spec)
    check('R1 negated SUPPORT -> CONTRADICTED',
          o['claim_1_verdict'].value == 'CONTRADICTED', repr(o['claim_1_verdict']))
    o2, _ = extract_claim_verdicts(
        'Claim 1 is supported. Claim 2 is contradicted.', spec)
    check('R2 claim1 not bled by claim2',
          o2['claim_1_verdict'].value == 'SUPPORTED', repr(o2['claim_1_verdict']))
    check('R2 claim2 correct',
          o2['claim_2_verdict'].value == 'CONTRADICTED', repr(o2['claim_2_verdict']))
    # R3: collective verdict.  Real trace res_scifact_280_1058_572 answers
    # "Both claims are marked as 'NOT ENOUGH INFO'" instead of naming each
    # claim; v6 first returned `absent`.  Found during case-study validation.
    o3, _ = extract_claim_verdicts(
        "Both claims are marked as 'NOT ENOUGH INFO' due to insufficient "
        "information in the provided papers.", spec)
    check('R3 collective NEI applies to all claims',
          o3['claim_1_verdict'].value == 'NOT_ENOUGH'
          and o3['claim_2_verdict'].value == 'NOT_ENOUGH', str(o3))
    o4, _ = extract_claim_verdicts('All claims are supported by the evidence.',
                                   spec)
    check('R4 collective SUPPORTED applies to all',
          o4['claim_1_verdict'].value == 'SUPPORTED'
          and o4['claim_2_verdict'].value == 'SUPPORTED', str(o4))
    # R5: a collective phrase must not override an explicit per-claim verdict
    o5, _ = extract_claim_verdicts(
        'Claim 1 is supported. Both claims were considered.', spec)
    check('R5 per-claim wins over collective',
          o5['claim_1_verdict'].value == 'SUPPORTED', str(o5))


# ── D4: bare nominal "the patch" is NOT a repair proposal ───────────────────
def test_code_repair_false_positive():
    spec = {'required_issues': [{'id': 'issue_1', 'function': 'apow',
                                 'location': 'sympy/printing/str.py'}]}
    bad = ['apow is mentioned in the patch.',
           'The patch does not touch apow.',
           'This modification in apow causes the bug.',
           'A change in apow breaks the output.']
    for t in bad:
        o, _ = extract_issue_stances(t, spec)
        v = o['issue_1'].value
        check('D4 no repair_proposed: %s' % t[:34], v != 'repair_proposed',
              'got %r' % v)


# ── D5: explicit defect identification survives anchor-only localization ────
def test_code_explicit_identification():
    spec = {'required_issues': [{'id': 'issue_1', 'function': 'apow',
                                 'location': 'sympy/printing/str.py'}]}
    for t in ['The bug is in apow. The function needs a fix.',
              'The root cause is in apow.',
              'This modification in apow causes the bug.']:
        o, _ = extract_issue_stances(t, spec)
        check('D5 asserted: %s' % t[:40], o['issue_1'].value == 'asserted',
              'got %r' % (o['issue_1'].value,))


# ── code: denial is preserved ───────────────────────────────────────────────
def test_code_denial():
    spec = {'required_issues': [{'id': 'issue_1', 'function': 'apow'}]}
    for t in ['There is no bug in apow; it behaves correctly.',
              'The bug in apow was already fixed upstream.']:
        o, _ = extract_issue_stances(t, spec)
        check('C1 denied: %s' % t[:40], o['issue_1'].value == 'denied',
              'got %r' % (o['issue_1'].value,))


# ── D7: trailing sentence period no longer kills a numeric fact ─────────────
def test_financial_period():
    spec = {'required_facts': {'revenue': {'label': 'total revenue'}}}
    for t in ['Total revenue was 1,234.5.',
              'Total revenue was 1,234.5',
              'The total revenue equals 1234.5']:
        o, _ = extract_facts(t, spec)
        check('D7 period-safe: %s' % t[:34], o['revenue'].value == 1234.5,
              'got %r' % (o['revenue'],))


# ── D8: final answer found via cue and via single-candidate fallback ────────
def test_financial_final_answer():
    spec = {}
    cases = [('The answer is 0.42.', 0.42),
             ('Therefore the ratio is 1.51.', 1.51),
             ('Final answer: 1234.5', 1234.5),
             ('The result is 88.2', 88.2)]
    for t, exp in cases:
        f, _ = extract_final_answer(t, spec)
        check('D8 cue answer %r' % exp,
              f.state == 'observed' and abs(f.value - exp) < 1e-9, repr(f))
    f2, _ = extract_final_answer('I computed several values: 1, 2, 3.', spec)
    check('D8 ambiguous when many numbers', f2.state == 'ambiguous', repr(f2))


# ── D9: label-not-discussed and label-without-value are distinguishable ─────
def test_financial_missing_vs_omitted():
    spec = {'required_facts': {'revenue': {'label': 'total revenue'}}}
    o, _ = extract_facts('Nothing relevant here at all.', spec)
    check('D9 omitted -> absent', o['revenue'].state == 'absent',
          repr(o['revenue']))
    o2, _ = extract_facts('Total revenue figures are discussed but unclear.',
                          spec)
    check('D9 discussed-no-value -> unavailable',
          o2['revenue'].state == 'unavailable', repr(o2['revenue']))


# ── financial: label/value binding must not cross-bind ─────────────────────
def test_financial_binding():
    spec = {'required_facts': {'revenue': {'label': 'total revenue'},
                               'net_income': {'label': 'net income'}}}
    for t in ['Total revenue was 1,234.5. Net income was 88.2.',
              'Net income was 88.2. Total revenue was 1,234.5.',
              'Total revenue was 1,234.5 and net income was 88.2.']:
        o, _ = extract_facts(t, spec)
        ok = (o['revenue'].value == 1234.5 and o['net_income'].value == 88.2)
        check('F1 binding: %s' % t[:38], ok,
              '%r / %r' % (o['revenue'], o['net_income']))


# ── GT separation: an extraction spec carrying truth must raise ────────────
def test_gt_separation():
    leaked = {'required_facts': {'claim_1_verdict': {'truth': 'SUPPORTED'}}}
    raised = False
    try:
        assert_gt_free_spec(leaked, 'test')
    except GroundTruthLeak:
        raised = True
    check('GT1 truth-bearing spec rejected', raised)
    clean = strip_gt(leaked)
    check('GT2 strip_gt removes truth',
          'truth' not in clean['required_facts']['claim_1_verdict'], str(clean))
    assert_gt_free_spec(clean, 'test')   # must not raise
    check('GT3 stripped spec accepted', True)

    # GT4: the per-fact `pattern` in specroot embeds the ground-truth literal
    # ("...\\s?(658(?:\\.0+)?)").  strip_gt MUST remove it or extraction
    # becomes a lookup.  This was found during validation, not in the audit.
    embedded = {'required_facts': {'fact_1': {
        'label': 'printing papers (2005)',
        'value': 658.0, 'tolerance': 0.01,
        'pattern': r'(?i)\b(?:printing|papers)[^\n]{0,160}?\s?(658(?:\.0+)?)'}}}
    raised = False
    try:
        assert_gt_free_spec(embedded, 'test')
    except GroundTruthLeak:
        raised = True
    check('GT4 GT-embedding pattern rejected', raised)
    c2 = strip_gt(embedded)
    ent = c2['required_facts']['fact_1']
    check('GT5 pattern stripped', 'pattern' not in ent, str(ent))
    check('GT6 value stripped', 'value' not in ent, str(ent))
    check('GT7 label survives', ent.get('label') == 'printing papers (2005)',
          str(ent))
    # the answer fact entry must be removed entirely from the extraction view
    withans = {'required_facts': {'answer': {'label': 'q?', 'value': 12.27},
                                  'fact_1': {'label': 'x'}}}
    c3 = strip_gt(withans)
    check('GT8 answer fact entry removed',
          'answer' not in c3['required_facts'], str(c3['required_facts']))


# ── SciFact NEI vs NOT_ENOUGH canonical mapping ────────────────────────────
def test_nei_mapping():
    for form in ('NEI', 'nei', 'INSUFFICIENT', 'Not Enough Information',
                 'not_enough_information', 'not enough information',
                 'inconclusive', 'NOT_ENOUGH'):
        check('G1 NEI form %r' % form,
              canonical_verdict(form) == 'NOT_ENOUGH', repr(form))
    check('G2 SUPPORTED maps', canonical_verdict('SUPPORTED') == 'SUPPORTED')
    check('G3 CONTRADICTED maps',
          canonical_verdict('CONTRADICTED') == 'CONTRADICTED')


def test_research_grading_nei():
    spec = {'required_facts': {'claim_1_verdict': {'truth': 'NEI'}}}
    feats, _ = extract_claim_verdicts(
        'The evidence for Claim 1 is insufficient.', strip_gt(spec))
    # grader reads the FULL spec; the extractor above read the stripped one
    g, det = grade_research(feats, spec)
    check('G4 agent INSUFFICIENT vs truth NEI -> correct', g == 'correct',
          str(det))


# ── outcomes: correctness not presence; code on identification not paths ───
def test_outcome_semantics():
    class F:
        state = 'observed'
        value = 0.30
    spec = {'answer': {'value': 0.30, 'tolerance': 0.0}}
    g, _ = grade_financial(F(), spec)
    check('O1 correct value -> correct', g == 'correct', g)
    class F2:
        state = 'observed'
        value = 0.99
    g2, _ = grade_financial(F2(), spec)
    check('O2 present but wrong -> incorrect', g2 == 'incorrect', g2)
    class F3:
        state = 'unavailable'
        value = None
    g3, _ = grade_financial(F3(), spec)
    check('O3 absent answer -> ungradeable', g3 == 'ungradeable', g3)

    # code: both correct -> 0 distance regardless of file sets
    d = outcome_distance_code('correct', 'correct', 1, 1, 1)
    check('O4 code correct/correct -> 0', d == 0.0, repr(d))
    d2 = outcome_distance_code('correct', 'incorrect', 1, 0, 1)
    check('O5 code correct/incorrect -> 1', d2 == 1.0, repr(d2))


def test_code_grade():
    spec = {'required_issues': [{'id': 'i1'}]}
    class F:
        state = 'observed'
        value = 'asserted'
    g, _ = grade_code({'i1': F()}, spec)
    check('O6 asserted -> correct', g == 'correct', g)
    class F2:
        state = 'observed'
        value = 'denied'
    g2, _ = grade_code({'i1': F2()}, spec)
    check('O7 denied -> incorrect', g2 == 'incorrect', g2)


# ── distances: missing is None, never 0 ────────────────────────────────────
def test_distance_missing_not_zero():
    class A:
        state = 'unavailable'
        value = None
    class B:
        state = 'observed'
        value = 1.0
    d, det = map_distance({'k': A()}, {'k': B()})
    check('M1 unavailable key -> None (not 0)', d is None, repr(d))
    check('M2 reason recorded', det['k']['reason'] == 'not_comparable', str(det))
    tv = total_variation({}, {})
    check('M3 empty actions -> None', tv is None, repr(tv))


# ── union: threshold from benign pairs, union of indicators ────────────────
def test_union_semantics():
    benign_pairs = [{'assertions': 0.0, 'decisions': 0.0,
                     'actions': 0.0, 'outcomes': 0.0},
                    {'assertions': 0.0, 'decisions': 0.0,
                     'actions': 0.1, 'outcomes': 0.0}]
    thr = benign_thresholds(benign_pairs)
    check('U1 threshold = max benign pair (actions)', thr['actions'] == 0.1,
          repr(thr))
    check('U2 zero threshold preserved (assertions)',
          thr['assertions'] == 0.0, repr(thr))

    lep = {'assertions': 0.5, 'decisions': 0.5, 'actions': 0.05,
           'outcomes': 0.0}
    benign = [{'assertions': 0.0, 'decisions': 0.0, 'actions': 0.0,
               'outcomes': 0.0}]
    pd = per_dimension(lep, benign, thr)
    u = union(pd)
    check('U3 union positive via assertions', u['divergent'], str(u))
    check('U4 actions below threshold -> not triggered',
          not pd['actions']['divergent'], str(pd['actions']))
    check('U5 triggered list is the union',
          set(u['triggered_dimensions']) == {'assertions', 'decisions'},
          str(u['triggered_dimensions']))
    check('U6 zero-threshold positives reported',
          set(u['zero_threshold_triggered']) == {'assertions', 'decisions'},
          str(u['zero_threshold_triggered']))


def test_union_no_composite():
    """The methodology has no weights: union() must expose no weighted field."""
    benign_pairs = [{'assertions': 0.2, 'decisions': 0.1,
                     'actions': 0.3, 'outcomes': 0.0}]
    thr = benign_thresholds(benign_pairs)
    pd = per_dimension({'assertions': 0.5, 'decisions': 0.1, 'actions': 0.3,
                        'outcomes': 0.0}, benign_pairs, thr)
    u = union(pd)
    check('U7 no composite/weight key in union',
          not any('composite' in k or 'weight' in k for k in u), str(u))
    check('U8 union is boolean of indicators',
          u['divergent'] == any(pd[d]['divergent'] for d in pd), str(u))


def test_union_missing_dimension_not_zero():
    benign_pairs = [{'assertions': 0.0, 'decisions': 0.0,
                     'actions': 0.0, 'outcomes': 0.0}]
    thr = benign_thresholds(benign_pairs)
    pd = per_dimension({'assertions': None, 'decisions': 0.0, 'actions': 0.0,
                        'outcomes': 0.0}, benign_pairs, thr)
    u = union(pd)
    check('U9 missing dimension is not-evaluable',
          not pd['assertions']['evaluable'], str(pd['assertions']))
    check('U10 missing dimension not counted as divergent',
          not pd['assertions']['divergent'], str(pd['assertions']))
    check('U11 missing listed in unknown', 'assertions' in u['unknown_dimensions'],
          str(u))


# ── M4-matched taxonomy ────────────────────────────────────────────────────
def test_m4_matched():
    c = classify_matched('failure', ['correct', 'failure', 'failure'], True)
    check('P1 failure + one benign correct -> degraded', c['class'] == DEGRADED,
          str(c))
    check('P2 m4_matched true', m4_matched('failure',
                                           ['correct', 'failure'], True))
    c2 = classify_matched('failure', ['failure', 'failure'], True)
    check('P3 failure + all benign failure -> persistent_failure',
          c2['class'] == PERSISTENT_FAILURE, str(c2))
    check('P4 m4_matched false for persistent failure',
          not m4_matched('failure', ['failure'], True))
    c3 = classify_matched('correct', ['correct'], True)
    check('P5 correct + benign correct -> persistent_correct',
          c3['class'] == PERSISTENT_CORRECT, str(c3))
    c4 = classify_matched('correct', ['failure'], True)
    check('P6 correct + benign failure -> improved', c4['class'] == IMPROVED,
          str(c4))
    c5 = classify_matched('failure', ['ungradeable', 'ungradeable'], True)
    check('P7 no gradeable benign -> mixed_unavailable',
          c5['class'] == MIXED_UNAVAILABLE, str(c5))
    c6 = classify_matched('failure', ['correct'], False)
    check('P8 not M3-supported -> excluded', c6['class'] == 'not_m3_supported',
          str(c6))
    # P9: graders.py emits 'incorrect' while the frozen artifacts use
    # 'failure'.  Without normalisation M4 silently reported 0 on the full
    # corpus because every M3 row fell through to mixed_or_unavailable.
    c7 = classify_matched('incorrect', ['correct', 'incorrect'], True)
    check('P9 incorrect == failure -> degraded', c7['class'] == DEGRADED,
          str(c7))
    check('P10 m4_matched accepts incorrect vocabulary',
          m4_matched('incorrect', ['correct'], True))
    check('P11 normalise_grade maps both',
          normalise_grade('incorrect') == 'failure'
          and normalise_grade('failure') == 'failure'
          and normalise_grade('correct') == 'correct'
          and normalise_grade('weird') == 'ungradeable')
    check('P12 m4_historical accepts incorrect',
          m4_historical('incorrect', True))


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for t in tests:
        try:
            t()
        except Exception as exc:                      # noqa: BLE001
            FAIL.append(t.__name__)
            print('  ERROR %s: %r' % (t.__name__, exc))
    total = len(PASS) + len(FAIL)
    print()
    print('=' * 66)
    print('v6 REGRESSION: %d/%d checks pass' % (len(PASS), total))
    if FAIL:
        print('FAILURES:')
        for f in FAIL:
            print('   -', f)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
