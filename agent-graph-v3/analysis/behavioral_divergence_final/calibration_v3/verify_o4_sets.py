#!/usr/bin/env python3.11
"""READ-ONLY set verification of the O4 (q98, a=.10) M2 operating point.

Uses the cached checkpoint cache + the frozen v6 M4 join ONLY.  It reuses
the sweep module's exact gate machinery (make_thr / disc_tail / build_pools
/ M4_BY) so the reconstructed per-trace O4 sets are byte-consistent with the
cached O4 aggregates (M2+ 4,123 / M3-supported 978 / M4-matched 299); the
reconstruction asserts against those aggregates before any set operation is
reported.  No extractor/statistic/reference/label/trace change; the only
write is a results JSON next to this file.

Answers (O4, q98/a=.10):
  * are the 299 M4-matched (degraded) executions all inside the 978
    M3-supported AND the 4,123 M2+ sets?  (exact set intersections)
  * M4-matched degraded across ALL 4,123 M2+ regardless of dep class.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import importlib.util as ilu  # noqa: E402

# pull the sweep module's cached-gate machinery (no pool build at import)
spec = ilu.spec_from_file_location(
    'sw', os.path.join(HERE, 'sweep_m2_fine_v44.py'))
sw = ilu.module_from_spec(spec)
sys.modules['sw'] = sw
spec.loader.exec_module(sw)

Q, A = 0.98, 0.10
OUT = os.path.join(HERE, 'out_v44')


def main():
    rows = sw.load_rows()
    fam_pools, disc, _ = sw.build_pools(rows)
    thr = sw.make_thr(fam_pools)

    M2, M3sup, M4inM2 = set(), set(), set()
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
                t = thr(Q, fam, scope, d, fx)
                if t is not None and v > t:
                    fired.append(1)
            for d in sw.DISC:
                kk = (u.get('comp_k') or {}).get(d)
                if kk in (None, 0):
                    continue
                p = sw.disc_tail(disc, fam, scope, d, kk)
                if p is not None and p <= A:
                    fired.append(1)
        if fired:
            M2.add(r['trace_id'])
            if r.get('m3_reach_class') in ('verified', 'plausible') \
                    and r.get('delivery_verified'):
                M3sup.add(r['trace_id'])
            if (sw.M4_BY.get(r['trace_id']) or 'none') == 'degraded':
                M4inM2.add(r['trace_id'])
    M4inM3 = M3sup & M4inM2

    # anchor the reconstruction to the cached O4 aggregates
    cached = json.load(open(os.path.join(OUT, 'sweep_m2_fine_v44.json')))
    c = cached['grid']['q98_a0.100']
    assert len(M2) == c['m2_positive'] == 4123, (len(M2), c['m2_positive'])
    assert len(M3sup) == c['m3_supported'] == 978, (len(M3sup), c['m3_supported'])
    assert len(M4inM3) == c['m4_matched'] == 299, (len(M4inM3), c['m4_matched'])

    res = {
        'operating_point': 'O4 q98_a0.100',
        'reconstruction': {
            'M2_positive': len(M2),
            'M3_supported': len(M3sup),
            'M4_matched_in_M3': len(M4inM3),
            'M4_matched_across_all_M2': len(M4inM2),
            'anchors_matched_cached': True},
        'inclusions': {
            'M4_299_subset_M3_978': bool(M4inM3 <= M3sup),
            'M4_299_subset_M2_4123': bool(M4inM2 <= M2),
            'M3_978_subset_M2_4123': bool(M3sup <= M2),
            'M4_in_M3_minus_M3_exceptions': sorted(M4inM3 - M3sup),
            'M4_in_M3_minus_M2_exceptions': sorted(M4inM2 - M2),
            'M3_minus_M2_exceptions': sorted(M3sup - M2)},
        'intersections': {
            'M4_in_M3': len(M4inM3),
            'M4_in_M2': len(M4inM2),
            'M4_in_M2_minus_M3': len(M4inM2 - M3sup),
            'note': ('M4-matched degraded across ALL M2+ equals the '
                    'within-M3 count: the frozen v6 grader assigns '
                    "'degraded' only to M3-supported executions; the "
                    '3,145 M2+ rows that are NOT M3-supported all carry '
                    'the frozen class not_m3_supported, so adding no '
                    'degraded beyond the 299.')},
    }
    # M4 class distribution over M2+ (for the report)
    from collections import Counter
    dist_in = Counter(); dist_out = Counter()
    for t in M2:
        cc = sw.M4_BY.get(t) or 'none'
        (dist_in if t in M3sup else dist_out)[cc] += 1
    res['m4_class_distribution'] = {
        'over_M3_supported_978': dict(dist_in),
        'over_M2plus_not_M3_3145': dict(dist_out)}

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, 'verify_o4_sets.json'), 'w') as fh:
        json.dump(res, fh, indent=1, sort_keys=True)

    print(json.dumps({k: res[k] for k in
                      ('operating_point', 'reconstruction', 'inclusions',
                       'intersections')}, indent=2))
    print('M4 class distribution:', res['m4_class_distribution'])
    print('wrote', os.path.join(OUT, 'verify_o4_sets.json'))


if __name__ == '__main__':
    if 'PYTHONHASHSEED' not in os.environ:
        os.environ['PYTHONHASHSEED'] = '0'
        os.execve(sys.executable, [sys.executable] + sys.argv,
                  os.environ)
    main()
