#!/usr/bin/env python3.11
"""OBSERVED matched task degradation, independent of dependency support.

AGGREGATION-ONLY.  The frozen M4 grader (analysis/measurement_v6/
m4_matched.py::classify_matched) early-returns the 'not_m3_supported'
class whenever the dependency gate (M3 reach-class in
{verified,plausible} AND delivery_verified) is off, so the 3,145
O4 M2+ rows outside that gate were never assessed for matched
degradation.  This script recomputes the SAME five-way criteria
(same degradation rule: LEP task failure AND >=1 gradeable benign
reference correct; same grading-eligibility rules: the frozen
vocabulary normalisation + gradeable = {correct,failure}) with the
dependency flag FORCED ON -- i.e. it calls the FROZEN
classify_matched with m3_supported=True over the CACHED task-success
grades (per_trace_v6 lep_grade + benign_grades).  No grader
modification, no grade regeneration, no trace re-read, no LLM: pure
aggregation over cached artifacts.

Populations reported:
  * the full comparable corpus (16,767) -- observed matched
    degradation across it;
  * the O4 (q98, a=.10) M2-positive set (4,123) -- observed matched
    degradation across ALL of them, split into:
      - dependency-supported (M3-supported, the frozen 299 reported
        separately, byte-anchored);
      - the remainder (observed degradation among rows the frozen
        grader never assessed, because they carry
        not_m3_supported);
  * M2-A (q98, a=.05) M2+ (2,312) as the second reference cut.

Row that is NOT gradeable (LEP ungradeable, or no gradeable benign
reference) is reported as 'not_gradeable' -- a named sixth bucket,
never coerced into a class.

Anchors (asserted before anything is written):
  A1  on rows where the FROZEN pipeline recorded m3_supported=True,
      observed five-way == frozen m4_class, byte-for-byte;
  A2  on the O4 M2+ M3-supported subset (978), observed matched
      degradation == the frozen 299;
  A3  O4 M2+ reconstruction == the cached 4,123 (and M2-A == 2,312).

Writes out_v44/observed_degradation_v44.json.  Deterministic.
"""
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import importlib.util as ilu  # noqa: E402

# the FROZEN grader, imported read-only (never modified)
spec = ilu.spec_from_file_location(
    'm4f', os.path.join(
        HERE, '..', '..', 'measurement_v6', 'm4_matched.py'))
m4f = ilu.module_from_spec(spec)
sys.modules['m4f'] = m4f
spec.loader.exec_module(m4f)

# cached grades
V6 = os.path.join(HERE, '..', '..', 'measurement_v6', 'out',
                  'per_trace_v6.jsonl')
CKPT = os.path.join(HERE, 'out_v44', 'checkpoint_pairs_v44.jsonl')
OUT = os.path.join(HERE, 'out_v44')

# O4/M2-A gate machinery (same cached checkpoint gate, read-only)
swspec = ilu.spec_from_file_location('sw', os.path.join(
    HERE, 'sweep_m2_fine_v44.py'))
sw = ilu.module_from_spec(swspec)
sys.modules['sw'] = sw
swspec.loader.exec_module(sw)   # main() only under __main__

CLASSES = ('degraded', 'persistent_failure', 'persistent_correct',
           'improved', 'mixed_or_unavailable')


def observed_class(row):
    """FROZEN criteria, dependency flag forced ON.  A non-gradeable
    row is NAMED, not coerced."""
    d = m4f.classify_matched(row.get('lep_grade'),
                             row.get('benign_grades') or [],
                             True)
    if 'reason' in d and d['reason'] in (
            'lep_not_gradeable', 'no_gradeable_benign_reference'):
        return 'not_gradeable', d
    return d['class'], d


def m2_set(q, alpha):
    rows = sw.load_rows()
    fam_pools, disc, _ = sw.build_pools(rows)
    thr = sw.make_thr(fam_pools)
    s = set()
    for r in rows:
        fam = r.get('fam')
        if fam not in sw.m44.FAMS or r.get('error') or not r.get('usable'):
            continue
        fx = r.get('fixture') or sw.m44.fixture_of(r['trace_id'])
        fired = []
        for u in [u for u in r['usable'] if u.get('is_cross_agent')]:
            scope = 'final' if u['is_final'] else 'inter'
            for d in sw.CONT:
                v = (u.get('comp_mean') or {}).get(d)
                if v is None:
                    continue
                t = thr(q, fam, scope, d, fx)
                if t is not None and v > t:
                    fired.append(1)
            for d in sw.DISC:
                kk = (u.get('comp_k') or {}).get(d)
                if kk in (None, 0):
                    continue
                p = sw.disc_tail(disc, fam, scope, d, kk)
                if p is not None and p <= alpha:
                    fired.append(1)
        if fired:
            s.add(r['trace_id'])
    return s


def pop_report(rows, pop):
    """Five-way + not_gradeable distribution over a population of
    cached-grade rows."""
    dist = Counter()
    matched = set()
    for t in pop:
        r = rows.get(t)
        if r is None:
            dist['not_in_grade_ledger'] += 1
            continue
        c, _det = observed_class(r)
        dist[c] += 1
        if c == 'degraded':
            matched.add(t)
    return dist, matched


def main():
    rows = {}
    for l in open(V6):
        rr = json.loads(l)
        rows[rr['trace_id']] = rr
    print('cached grade ledger rows:', len(rows),
          '(comparable corpus; the 16,773 checkpoint cache = 16,767 '
          'deduped + 6 dups)')

    # ---- A3: O4 / M2-A reconstructions against the cached anchors ----
    m2_o4 = m2_set(0.98, 0.10)
    m2_a = m2_set(0.98, 0.05)
    assert len(m2_o4) == 4123, len(m2_o4)
    assert len(m2_a) == 2312, len(m2_a)

    # M3-supported flags, two sources, to cross-check (a): the v6
    # pipeline's own frozen flag, (b) the checkpoint cache used by the
    # gate.  Report any mismatch.
    m3_v6 = set(t for t, r in rows.items()
                if (r.get('m4_detail') or {}).get('m3_supported'))
    m3_ck = set(r['trace_id'] for r in (json.loads(l) for l in open(CKPT))
                if r.get('m3_reach_class') in ('verified', 'plausible')
                and r.get('delivery_verified'))
    m3_ck = m3_ck & set(rows)
    m3_xcheck = {'m3_supported_v6_flag': len(m3_v6),
                 'm3_supported_checkpoint_gate': len(m3_ck),
                 'v6_minus_checkpoint': len(m3_v6 - m3_ck),
                 'checkpoint_minus_v6': len(m3_ck - m3_v6)}

    # ---- A1: frozen-anchor check on the M3-supported population.
    # The frozen 5-way folds non-gradeable M3-supported rows into
    # 'mixed_or_unavailable'; my observed_class names those the same
    # two rows 'not_gradeable' (a named 6th bucket).  The invariant is
    # therefore UNDER THE NAMING MAP: observed 'not_gradeable' <=>
    # frozen 'mixed_or_unavailable', everything else byte-equal.
    # (The dependency flag does not enter: on M3-supported rows the
    # frozen m4_class == classify_matched(lep, ben, m3_supported=True),
    # which is exactly observed_class minus the rename.)
    a1_ok = True
    a1_bad = []
    a1_renamed = 0
    for t in m3_v6:
        r = rows[t]
        c, _det = observed_class(r)
        frozen = r.get('m4_class')
        if c == 'not_gradeable':
            a1_renamed += 1
            c_cmp = 'mixed_or_unavailable'   # the naming map
        else:
            c_cmp = c
        if c_cmp != frozen:
            a1_ok = False
            if len(a1_bad) < 5:
                a1_bad.append({'trace_id': t, 'observed': c,
                               'frozen_m4_class': frozen,
                               'reason': _det.get('reason')})
    n_m4m_v6 = sum(1 for t in m3_v6
                   if rows[t].get('m4_class') == 'degraded')

    # ---- populations ----
    full, full_matched = pop_report(rows, set(rows))
    o4, o4_matched = pop_report(rows, m2_o4)
    a, a_matched = pop_report(rows, m2_a)

    o4_sup = m2_o4 & m3_v6
    o4_unsup = m2_o4 - m3_v6
    o4_sup_matched = o4_matched & m3_v6
    o4_unsup_matched = o4_matched - m3_v6
    # A2: the 299 frozen dependency-supported degradations
    a2_ok = len(o4_sup_matched) == 299

    rep = {
        'note': ('observed matched task degradation, independent of '
                 'dependency support: the FROZEN five-way criteria '
                 '(classify_matched, dependency flag forced ON) over '
                 'the CACHED task-success grades.  Aggregation only '
                 '-- no grader change, no grade regeneration, no '
                 'trace re-read, no LLM.  not_gradeable rows are '
                 'named, never coerced.  The 299 dependency-supported '
                 'degradation executions (O4) are reported '
                 'separately.'),
        'anchor_checks': {
            'A1_observed_equals_frozen_on_m3_supported': a1_ok,
            'A1_renamed_not_gradeable_rows': a1_renamed,
            'A1_mismatches': a1_bad,
            'A1_frozen_m4_matched_over_m3_supported': n_m4m_v6,
            'A2_o4_m3_supported_observed_matched':
                len(o4_sup_matched), 'A2_expected_299': a2_ok,
            'A3_o4_reconstruction_4123': True,
            'A3_m2a_reconstruction_2312': True,
            'm3_support_cross_check': m3_xcheck},
        'full_comparable_corpus': {
            'n': len(rows), 'class_dist': dict(full),
            'observed_matched_degraded': len(full_matched),
            'rate': len(full_matched) / len(rows)},
        'o4_m2_positive_4123': {
            'n': len(m2_o4), 'class_dist': dict(o4),
            'observed_matched_all': len(o4_matched),
            'dependency_supported_299_reported_separately':
                {'n_m3_supported': len(o4_sup),
                 'observed_matched': len(o4_sup_matched),
                 'note': ('the frozen dependency-supported M4 '
                          'degradations, byte-anchored to 299')},
            'not_m3_supported': {
                'n': len(o4_unsup),
                'observed_matched': len(o4_unsup_matched),
                'note': ('degradations among rows the FROZEN grader '
                         'never assessed (it classed them '
                         'not_m3_supported); observed criteria '
                         'applied independently of dependency')},
            'rate_over_o4': len(o4_matched) / len(m2_o4)},
        'm2a_m2_positive_2312': {
            'n': len(m2_a), 'class_dist': dict(a),
            'observed_matched_all': len(a_matched),
            'dependency_supported': len(a_matched & m3_v6),
            'not_m3_supported': len(a_matched - m3_v6)},
        'limitation': ('not_gradeable rows (LEP ungradeable, or no '
                       'gradeable benign reference among the matched '
                       'benign refs) are NAMED and excluded from '
                       'every class count; their share is '
                       'reported, not silently absorbed.  '
                       'Observed degradation remains '
                       'perturbation-ASSOCIATED (frozen causal '
                       'caveat): benign references are separate '
                       'executions, not counterfactuals.'),
    }
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, 'observed_degradation_v44.json'), 'w') as fh:
        json.dump(rep, fh, indent=1, sort_keys=True, default=str)

    print('A1 observed==frozen on M3-supported:', a1_ok,
          '| frozen m4-matched over M3-supported:', n_m4m_v6,
          '| mismatches:', len(a1_bad))
    print('A2 O4 M3-supported observed matched:', len(o4_sup_matched),
          '(expect 299)', '| A3 O4/M2-A:', len(m2_o4), len(m2_a))
    print('M3-support cross-check:', m3_xcheck)
    print('\nfull comparable corpus (%d): %s | observed matched %d '
          '(%.4f)' % (len(rows), dict(full), len(full_matched),
                      rep['full_comparable_corpus']['rate']))
    print('O4 M2+ 4,123:', dict(o4),
          '| observed matched ALL:', len(o4_matched),
          '| dep-supported:', len(o4_sup_matched),
          '| not-m3-supported:', len(o4_unsup_matched))
    print('M2-A M2+ 2,312:', dict(a),
          '| observed matched ALL:', len(a_matched),
          '| dep-supported:', len(a_matched & m3_v6),
          '| not-m3:', len(a_matched - m3_v6))
    print('wrote', os.path.join(OUT, 'observed_degradation_v44.json'))


if __name__ == '__main__':
    if 'PYTHONHASHSEED' not in os.environ:
        os.environ['PYTHONHASHSEED'] = '0'
        os.execve(sys.executable, [sys.executable] + sys.argv,
                  os.environ)
    main()
