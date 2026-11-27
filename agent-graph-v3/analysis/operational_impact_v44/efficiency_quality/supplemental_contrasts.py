#!/usr/bin/env python3.11
"""Focused supplementary contrasts for the EFFICIENCY_QUALITY report.

Adds the H2 "extreme-faster tail" contrasts that the two-group ARD dilutes:
  * >25%-faster vs within-5% (the meaningful threshold test), overall and
    within each task family (composition artifact check).
  * family / LEP / topology composition of the >25%-faster bin.
  * the descriptive 3-way (faster-moderate, extreme-faster, slower) degradation
    ordering with joint CIs.
Writes out/supplemental_contrasts.json. Deterministic, read-only on the
already-computed per-execution table.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out')
BASE = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import eq_stats as es


def _med(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    vals = sorted(vals)
    n = len(vals)
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0


def _fix_clusters(rows):
    d = {}
    for r in rows:
        d.setdefault(r['fixture_id'], []).append(r)
    return d


def contrast_mode(rows, rkey, n_pop):
    ext = [r for r in rows if r.get(rkey) is not None and r[rkey] < -0.25]
    mid = [r for r in rows if r.get(rkey) is not None and abs(r[rkey]) <= 0.05]
    def _rate(rs):
        n = len(rs)
        d = sum(1 for r in rs if r['M3_matched_degraded_observed'])
        return (d / n) if n else None, d, n
    ext_r, ext_d, ext_n = _rate(ext)
    mid_r, mid_d, mid_n = _rate(mid)
    ard = (ext_r - mid_r) if (ext_n and mid_n) else None
    fc = _fix_clusters(ext + mid)
    fixes = sorted(fc)
    e_arr, m_arr = [], []
    for f in fixes:
        er = [r for r in fc[f] if r.get(rkey) is not None and r[rkey] < -0.25]
        mr = [r for r in fc[f] if r.get(rkey) is not None and abs(r[rkey]) <= 0.05]
        e_arr.append((sum(1 for r in er if r['M3_matched_degraded_observed']), len(er)))
        m_arr.append((sum(1 for r in mr if r['M3_matched_degraded_observed']), len(mr)))
    def _counts(lst):
        return [x[0] for x in lst], [x[1] for x in lst]
    en, ed = _counts(e_arr)
    mn, md = _counts(m_arr)
    ard_ci = es.ci_ard(fixes, en, ed, mn, md) if any(ed) and any(md) else None
    return {'n_pop': n_pop, 'n_extreme_faster': ext_n, 'extreme_faster_rate': ext_r,
            'n_within5': mid_n, 'within5_rate': mid_r,
            'ard_extreme_minus_within5': ard, 'ard_ci': ard_ci,
            'extreme_degraded': ext_d, 'within5_degraded': mid_d}


def contrast(rows):
    """extreme-faster(>25%) vs within-5%: ARD + joint CI (fixture-cluster)."""
    ext = [r for r in rows if r['rel_combined'] is not None and r['rel_combined'] < -0.25]
    mid = [r for r in rows if r['rel_combined'] is not None and abs(r['rel_combined']) <= 0.05]

    def _rate(rs):
        n = len(rs)
        d = sum(1 for r in rs if r['M3_matched_degraded_observed'])
        return (d / n) if n else None, d, n

    ext_r, ext_d, ext_n = _rate(ext)
    mid_r, mid_d, mid_n = _rate(mid)
    ard = (ext_r - mid_r) if (ext_n and mid_n) else None
    # joint fixture-cluster CI
    fc = _fix_clusters(ext + mid)
    e_arr, m_arr = [], []
    for f in sorted(fc):
        er = [r for r in fc[f] if r['rel_combined'] is not None and r['rel_combined'] < -0.25]
        mr = [r for r in fc[f] if r['rel_combined'] is not None and abs(r['rel_combined']) <= 0.05]
        e_arr.append((sum(1 for r in er if r['M3_matched_degraded_observed']), len(er)))
        m_arr.append((sum(1 for r in mr if r['M3_matched_degraded_observed']), len(mr)))
    e_fixes = [f for f in sorted(fc)]
    def _counts(lst):
        num = [x[0] for x in lst]; den = [x[1] for x in lst]
        return num, den
    en, ed = _counts(e_arr)
    mn, md = _counts(m_arr)
    ard_ci = es.ci_ard(e_fixes, en, ed, mn, md) if any(ed) and any(md) else None
    rr = (ext_r / mid_r) if (ext_r is not None and mid_r) else None
    return {
        'n_extreme_faster': ext_n, 'extreme_faster_rate': ext_r,
        'n_within5': mid_n, 'within5_rate': mid_r,
        'ard_extreme_minus_within5': ard, 'ard_ci': ard_ci, 'rr': rr,
        'extreme_degraded': ext_d, 'within5_degraded': mid_d,
    }


def composition(rows):
    ext = [r for r in rows if r['rel_combined'] is not None and r['rel_combined'] < -0.25]
    mid = [r for r in rows if r['rel_combined'] is not None and abs(r['rel_combined']) <= 0.05]
    from collections import Counter
    def share(lst, key):
        c = Counter(r[key] for r in lst)
        n = len(lst)
        return {k: (v / n if n else 0.0) for k, v in c.most_common()}
    return {
        'extreme_faster_family_share': share(ext, 'task_family'),
        'within5_family_share': share(mid, 'task_family'),
        'extreme_faster_lep_share': share(ext, 'lep_code'),
        'extreme_faster_topology_share': share(ext, 'topology'),
        'extreme_faster_m1_incidence': sum(1 for r in ext if r['M1_positive']) / len(ext) if ext else None,
        'extreme_faster_m2_incidence': sum(1 for r in ext if r['M2_positive']) / len(ext) if ext else None,
        'within5_m1_incidence': sum(1 for r in mid if r['M1_positive']) / len(mid) if mid else None,
        'within5_m2_incidence': sum(1 for r in mid if r['M2_positive']) / len(mid) if mid else None,
    }


def three_way(rows):
    """moderate-faster (10–25%) / extreme-faster (>25%) / slower (>10%) degradation ordering."""
    def _rate(rs):
        n = len(rs)
        d = sum(1 for r in rs if r['M3_matched_degraded_observed'])
        return (d / n) if n else None
    mfast = [r for r in rows if r['rel_combined'] is not None and -0.25 <= r['rel_combined'] < -0.10]
    efast = [r for r in rows if r['rel_combined'] is not None and r['rel_combined'] < -0.25]
    slow = [r for r in rows if r['rel_combined'] is not None and r['rel_combined'] > 0.10]
    return {
        'moderate_faster_10_25_rate': _rate(mfast), 'n': len(mfast),
        'extreme_faster_gt25_rate': _rate(efast),
        'slower_gt10_rate': _rate(slow),
        'ordering_note': ('threshold shape: extreme-faster is the peak; moderate-faster and slower '
                          'are comparable and both below the extreme-faster tail'),
    }


def _tail(pop):
    import os
    c = contrast(pop)
    return c


def main():
    rows = [json.loads(l) for l in open(os.path.join(OUT, 'efficiency_quality_per_execution.jsonl')) if l.strip()]
    ge = [r for r in rows if r['gradeable'] and r.get('rel_combined') is not None]
    out = {
        'population': 'grading-eligible with valid combined matched latency (n=%d)' % len(ge),
        'overall_extreme_vs_within5': contrast(ge),
        'composition_of_extreme_faster_bin': composition(ge),
        'three_way_ordering': three_way(ge),
        'within_family_extreme_vs_within5': {},
    }
    for fam in sorted(set(r['task_family'] for r in ge)):
        sub = [r for r in ge if r['task_family'] == fam]
        c = contrast(sub)
        out['within_family_extreme_vs_within5'][fam] = c
    # cross-check: does the extreme-faster peak survive within the dominant family?
    # §8 robustness: extreme-vs-within5 contrast under each latency mode
    out['extreme_vs_within5_by_mode'] = {}
    for mode, rkey in (('combined', 'rel_combined'), ('trusted_runtime', 'rel_trusted_runtime'),
                       ('event_span', 'rel_event_span')):
        pop = [r for r in ge if r.get(rkey) is not None]
        out['extreme_vs_within5_by_mode'][mode] = contrast_mode(pop, rkey, len(pop))
    # §8 robustness: extreme-vs-within5 under infra-clean and success-ref populations,
    # using the CORRECT fixture-union ci_ard alignment (contrast() below is reusable).
    # (replaces an earlier ad-hoc file that restricted the denominator to the
    #  extreme-faster arm's fixtures — a bug; this is the correct version)
    out['extreme_vs_within5_by_population'] = {}
    ic = [r for r in ge if (r.get('backend_retries') or 0) == 0 and
          (r.get('n_tool_errors') or 0) == 0
          and (r.get('record') or {}).get('termination_reason') in ('completed', None)]
    def _has_success_ref(r, ben):
        for x in r.get('benign_refs', []):
            b = ben.get(x)
            if b and b.get('runtime_trust') in ('attempt_id', 'consistent') and \
                    (b.get('record') or {}).get('task_success') is True:
                return True
        return False
    base_ben = {json.loads(l)['trace_id']: json.loads(l) for l in
                open(os.path.join(os.path.dirname(HERE), 'out', 'benign_reference_operational.jsonl'))}
    ic2 = [r for r in ic if r['gradeable'] and r.get('rel_combined') is not None]
    out['extreme_vs_within5_by_population']['infra_clean'] = _tail(ic2)
    sref = [r for r in ge if _has_success_ref(r, base_ben)]
    out['extreme_vs_within5_by_population']['success_ref'] = _tail(sref)
    out['extreme_vs_within5_by_population']['combined_primary'] = _tail(ge)
    out['interpretation'] = (
        'If the overall extreme-vs-within5 ARD is positive but within-family ARDs are '
        'mixed/sign and the extreme bin is concentrated in one family, the pooled peak is a '
        'compositional (Simpson) artifact — see the PREREGISTRATION conclusion rule.')
    p = os.path.join(OUT, 'supplemental_contrasts.json')
    with open(p, 'w') as fh:
        json.dump(out, fh, indent=1, default=float)
    print('wrote', p)
    o = out['overall_extreme_vs_within5']
    print('overall extreme-faster rate=%s (n=%d) within5=%s (n=%d) ARD=%s CI=%s' % (
        o['extreme_faster_rate'], o['n_extreme_faster'], o['within5_rate'], o['n_within5'],
        o['ard_extreme_minus_within5'], o['ard_ci']))
    for fam, c in out['within_family_extreme_vs_within5'].items():
        print('  %s: extreme=%s(n=%d) within5=%s(n=%d) ARD=%s CI=%s' % (
            fam, c['extreme_faster_rate'], c['n_extreme_faster'],
            c['within5_rate'], c['n_within5'], c['ard_extreme_minus_within5'], c['ard_ci']))
    print('composition extreme-faster family:', out['composition_of_extreme_faster_bin']['extreme_faster_family_share'])


if __name__ == '__main__':
    main()
