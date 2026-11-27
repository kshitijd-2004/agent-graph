#!/usr/bin/env python3.11
"""V4.4 M2 FINE threshold-sensitivity sweep (cached checkpoint cache only).

Grid: q in {0.95,0.96,0.97,0.98,0.99}  x  alpha in
{0.05,0.075,0.10,0.125,0.15,0.175,0.20,0.25}  = 40 operating points.

Per operating point it reports:
  * M2 downstream incidence (rate + per-dimension + per-scope);
  * execution-level benign control firing (B2B row-OR: LEP-free per-key
    benign observations vs the SAME fixture-disjoint threshold / tail the
    LEP faces) with a FIXTURE-CLUSTER bootstrap CI;
  * perturbed-minus-benign rate difference (M2 rate - B2B row-OR rate);
  * dependency-supported propagation (M3: reach in verified|plausible AND
    delivery_verified) and its verified/plausible split;
  * matched task degradation (M4: matched 'degraded' over the M3
    denominator, from the FROZEN v6 grader ledger);
  * per-dimension contribution.

It then (a) identifies the Pareto frontier over the two surfaces that
matter here — (incidence, B2B-row-OR, delta) — and (b) answers the two
investigation questions: which dimensions drive the M2-F (q98, a=.25)
increase over M2-A (q98, a=.05), and which previously-saturated discrete
channels become functional as alpha rises (a channel is functional at
alpha iff some LEP target mass k has null-tail P(k>=k) <= alpha; we
report the channels that are OFF at a=.05 and ON at a=.25, and their
per-fam/dim tail structure).

No extractor change, no new statistic, no reference/label change, no
raw-trace re-read, no LLM: this moves only the decision point (q, alpha)
on the already-cached checkpoint distances + calibrated nulls.  M1-C is
kept as the selected M1 point (untouched here); M2 is NOT frozen.

Anchors: (q98, a=.05) must reproduce the cached production gate M2-A
(2,312 / 16,767); (q98, a=.25) must reproduce the coarse sweep's M2-F
(4,627).  Deterministic (seeded cluster bootstrap, PYTHONHASHSEED=0).
"""
import json
import math
import os
import random
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import m44  # noqa: E402

OUT = os.path.join(HERE, 'out_v44')
DIMS = m44.DIMS
CONT = ('assertions', 'actions')
DISC = ('decisions', 'outcomes')
MIN_POOL = m44.MIN_POOL          # 30
ALPHA_GRID = [0.05, 0.075, 0.10, 0.125, 0.15, 0.175, 0.20, 0.25]
Q_GRID = [0.95, 0.96, 0.97, 0.98, 0.99]
SEED_CI = 20270399
N_BOOT = 4000

# frozen M4 classes (the M4 join never changes with (q, alpha))
M4_BY = {rr['trace_id']: rr.get('m4_class')
         for rr in m44._jsonl(m44.V6_LEDGER)}


def load_rows():
    """Deduped checkpoint rows (last-wins per trace_id), the same set the
    production gate and the coarse sweep operate on (16,767)."""
    rows = {}
    for l in open(os.path.join(OUT, 'checkpoint_pairs_v44.jsonl')):
        r = json.loads(l)
        rows[r['trace_id']] = r
    return list(rows.values())


def build_pools(rows):
    """Continuous pools (fixture-tagged individual benign-pair obs) + the
    LEP-free discrete 3-pair disagree-mass nulls, built ONCE."""
    fam_pools = {f: {s: {d: [] for d in DIMS}
                     for s in ('inter', 'final')}
                 for f in m44.FAMS}
    disc = {}
    disc_raw = {}
    for r in rows:
        fam = r.get('fam')
        if fam not in m44.FAMS or not r.get('usable') or r.get('error'):
            continue
        fx = r.get('fixture') or m44.fixture_of(r['trace_id'])
        for u in r['usable']:
            scope = 'final' if u['is_final'] else 'inter'
            for d, obs in (u.get('ben_by_key') or {}).items():
                obs = [v for v in (obs or []) if v is not None]
                for v in obs:
                    fam_pools[fam][scope][d].append((fx, v))
                if d in DISC and obs:
                    kb = float(sum(1 for v in obs if v > 0))
                    cnt = disc.setdefault((fam, scope, d), Counter())
                    cnt[kb] += 1
                    disc_raw.setdefault((fam, scope, d), []).append(
                        (fx, kb))
    for f in fam_pools:
        for s in fam_pools[f]:
            for d in fam_pools[f][s]:
                fam_pools[f][s][d].sort(key=lambda p: p[1])
    return fam_pools, disc, disc_raw


def make_thr(fam_pools):
    """Per-q fixture-disjoint threshold table, cached lazily per (q,...)."""
    cache = {}

    def thr(q, fam, scope, dim, fx):
        key = (q, fam, scope, dim, fx)
        if key in cache:
            return cache[key]
        vals = [v for (f2, v) in fam_pools[fam][scope][dim] if f2 != fx]
        res = None
        if len(vals) >= MIN_POOL:
            vals.sort()
            res = vals[min(len(vals) - 1, int(q * len(vals)))]
        cache[key] = res
        return res

    return thr


def disc_tail(disc, fam, scope, dim, k):
    c = disc.get((fam, scope, dim))
    if not c or sum(c.values()) < MIN_POOL:
        return None
    tot = sum(c.values())
    e = sum(n for kk, n in c.items() if kk >= float(k))
    return e / tot


def cluster_ci(fix, rate, seed=SEED_CI, n_iter=N_BOOT):
    """Fixture-cluster bootstrap CI of an execution-level rate (resample
    whole fixtures; deterministic seed)."""
    keys = sorted(fix)
    if len(keys) < 5:
        return [rate, rate, rate]
    rng = random.Random(seed)
    ests = []
    for _ in range(n_iter):
        pool = []
        for _i in range(len(keys)):
            pool.extend(fix[rng.choice(keys)])
        ests.append(sum(pool) / len(pool))
    ests.sort()
    return [ests[int(0.025 * (n_iter - 1))], rate,
            ests[int(0.975 * (n_iter - 1))]]


def run_point(rows, thr, disc, q, alpha):
    """One (q, alpha) M2 gate over the cached checkpoint rows."""
    n = 0
    m2pos = 0
    dim_pos = Counter()          # dim -> rows firing it (contribution)
    scope_fire = Counter()
    inter_f = 0
    final_f = 0
    b2b_rows = 0
    b2b_fired = 0
    b2b_fix = {}
    pos_tids = []
    for r in rows:
        fam = r.get('fam')
        if fam not in m44.FAMS or r.get('error') or not r.get('usable'):
            continue
        n += 1
        fx = r.get('fixture') or m44.fixture_of(r['trace_id'])
        usable = [u for u in r['usable'] if u.get('is_cross_agent')]
        fired = []
        any_b2b = False
        for u in usable:
            scope = 'final' if u['is_final'] else 'inter'
            for d in CONT:
                v = (u.get('comp_mean') or {}).get(d)
                if v is None:
                    continue
                t = thr(q, fam, scope, d, fx)
                if t is None:
                    continue
                if v > t:
                    fired.append((u.get('key'), d))
            for d in DISC:
                kk = (u.get('comp_k') or {}).get(d)
                if kk is None or kk == 0:
                    continue
                pnull = disc_tail(disc, fam, scope, d, kk)
                if pnull is None:
                    continue
                if pnull <= alpha:
                    fired.append((u.get('key'), d + ':k' + str(kk)))
            # B2B: LEP-free per-key benign observations vs the same null
            for d in CONT:
                for x in [v for v in ((u.get('ben_by_key') or {}).get(d)
                                      or []) if v is not None]:
                    t = thr(q, fam, scope, d, fx)
                    if t is not None and x > t:
                        any_b2b = True
            for d in DISC:
                obs = [v for v in ((u.get('ben_by_key') or {}).get(d)
                                   or []) if v is not None]
                if obs:
                    kb = sum(1 for v in obs if v > 0)
                    pb = disc_tail(disc, fam, scope, d, kb)
                    if kb > 0 and pb is not None and pb <= alpha:
                        any_b2b = True
        is_pos = bool(fired)
        m2pos += int(is_pos)
        if is_pos:
            for _k, d in fired:
                dim_pos[d.split(':')[0]] += 1
            if any(k != 'final' for k, _d in fired):
                inter_f += 1
            if any(k == 'final' for k, _d in fired):
                final_f += 1
            pos_tids.append(r['trace_id'])
        b2b_rows += 1
        b2b_fired += int(any_b2b)
        b2b_fix.setdefault(fx, []).append(1.0 if any_b2b else 0.0)

    m2set = set(pos_tids)
    reach_by = {r['trace_id']: r.get('m3_reach_class') for r in rows}
    deliv_by = {r['trace_id']: r.get('delivery_verified') for r in rows}
    m3 = [t for t in m2set
          if reach_by.get(t) in ('verified', 'plausible')
          and deliv_by.get(t)]
    m3v = sum(1 for t in m3 if reach_by.get(t) == 'verified')
    m4 = sum(1 for t in m3 if (M4_BY.get(t) or 'none') == 'degraded')
    # M2 positives that are NOT dependency-supported (structural graph
    # exposure only: reach not verified/plausible) — the "do not assume
    # all additional detections are genuine propagation" accounting.
    geo = sum(1 for t in m2set
              if reach_by.get(t) in ('not_downstream', 'unsupported'))
    reach_cls = Counter(reach_by.get(t) for t in m2set)

    m2_rate = m2pos / n if n else None
    b2b_rate = b2b_fired / b2b_rows if b2b_rows else None
    return {
        'q': q, 'alpha': alpha,
        'n_rows': n,
        'm2_positive': m2pos, 'm2_rate': m2_rate,
        'by_dim_rows': dict(dim_pos),
        'inter_fired': inter_f, 'final_fired': final_f,
        'b2b_rows': b2b_rows, 'b2b_fired': b2b_fired,
        'b2b_rate': b2b_rate,
        'b2b_cluster_ci95': cluster_ci(b2b_fix, b2b_rate),
        'delta_m2_minus_b2b': (m2_rate - b2b_rate)
        if (m2_rate is not None and b2b_rate is not None) else None,
        'm3_supported': len(m3), 'm3_verified': m3v,
        'm3_plausible': len(m3) - m3v,
        'm3_share_of_m2': (len(m3) / m2pos) if m2pos else None,
        'm4_matched': m4, 'm4_denominator': len(m3),
        'm4_rate': (m4 / len(m3)) if m3 else None,
        'graph_exposure_only': geo,
        'reach_classes_over_m2': dict(reach_cls),
    }


def channel_functional_scan(rows, disc, alpha_on, alpha_off):
    """Which (fam, scope, dim) DISCRETE channels are functional at
    alpha_on but not at alpha_off: a channel is functional at alpha iff
    some LEP target mass k>0 observed in the cache has null-tail
    P(k>=k) <= alpha.  This is the direct answer to 'do previously
    saturated channels become functional'."""
    target_k = {}   # (fam,scope,dim) -> set of observed LEP k>0
    for r in rows:
        fam = r.get('fam')
        if fam not in m44.FAMS or not r.get('usable') or r.get('error'):
            continue
        for u in r['usable']:
            scope = 'final' if u['is_final'] else 'inter'
            for d in DISC:
                kk = (u.get('comp_k') or {}).get(d)
                if kk and kk > 0:
                    target_k.setdefault((fam, scope, d), set()).add(kk)
    out = {}
    for key, ks in target_k.items():
        fam, scope, dim = key
        tails = {}
        for k in sorted(ks):
            tails[int(k)] = disc_tail(disc, fam, scope, dim, int(k))
        on = any(t is not None and t <= alpha_on for t in tails.values())
        off = any(t is not None and t <= alpha_off for t in tails.values())
        if on and not off:
            out['%s/%s/%s' % (fam, scope, dim)] = {
                'target_mass': sorted(int(k) for k in ks),
                'tails': tails,
                'on_at': alpha_on, 'off_at': alpha_off}
    return out, target_k


def channel_activation(rows, disc):
    """Per (fam, scope, dim) DISCRETE channel: the LEP target-mass
    histogram and, per mass, the alpha at which its null-tail P(k>=k)
    drops to/below the threshold (the smallest grid alpha at which that
    mass fires) + the number of cross-agent rows carrying that mass.
    A mass whose activation alpha is above the a=.05 baseline is a
    previously-saturated channel that only becomes functional when the
    decision point moves up — the exact 'saturated channel' the
    investigation flagged.  Deterministic; cached only."""
    mass_rows = {}
    for r in rows:
        fam = r.get('fam')
        if fam not in m44.FAMS or not r.get('usable') or r.get('error'):
            continue
        for u in [u for u in r['usable'] if u.get('is_cross_agent')]:
            scope = 'final' if u['is_final'] else 'inter'
            for d in DISC:
                kk = (u.get('comp_k') or {}).get(d)
                if kk and kk > 0:
                    key = '%s/%s/%s' % (fam, scope, d)
                    mass_rows.setdefault(key, Counter())[int(kk)] += 1
    out = {}
    for key, hist in mass_rows.items():
        fam, scope, dim = key.split('/')
        per_mass = {}
        for k in sorted(hist):
            tail = disc_tail(disc, fam, scope, dim, k)
            act = min((a for a in ALPHA_GRID
                       if tail is not None and tail <= a),
                      default=None)
            per_mass[k] = {'n_rows': int(hist[k]),
                           'tail': round(tail, 4) if tail is not None
                           else None,
                           'activates_at_alpha': act,
                           'previously_saturated': (act is not None
                                                     and act > 0.05)}
        out[key] = per_mass
    return out


def main():
    rows = load_rows()
    fam_pools, disc, _raw = build_pools(rows)
    thr = make_thr(fam_pools)

    grid = {}
    for q in Q_GRID:
        for a in ALPHA_GRID:
            key = 'q%02d_a%.3f' % (int(round(q * 100)), a)
            grid[key] = run_point(rows, thr, disc, q, a)

    # anchors
    base = grid['q98_a0.050']
    ffine = grid['q98_a0.250']
    assert base['m2_positive'] == 2312, \
        ('M2-A anchor drift: %d' % base['m2_positive'])
    assert ffine['m2_positive'] == 4627, \
        ('M2-F anchor drift: %d' % ffine['m2_positive'])

    # per-dimension driver of the M2-F increase over M2-A
    dim_delta = {}
    for d in DIMS:
        dim_delta[d] = {
            'm2a': base['by_dim_rows'].get(d, 0),
            'm2f': ffine['by_dim_rows'].get(d, 0),
            'delta': ffine['by_dim_rows'].get(d, 0)
                    - base['by_dim_rows'].get(d, 0)}

    # genuine-vs-exposure split at the two anchors (the M2-F increase
    # must be attributed, not assumed genuine)
    anchor_split = {
        'm2a_q98_a05': {'m2': base['m2_positive'],
                        'm3_supported': base['m3_supported'],
                        'm3_share': base['m3_share_of_m2'],
                        'graph_exposure_only': base['graph_exposure_only'],
                        'reach': base['reach_classes_over_m2']},
        'm2f_q98_a25': {'m2': ffine['m2_positive'],
                        'm3_supported': ffine['m3_supported'],
                        'm3_share': ffine['m3_share_of_m2'],
                        'graph_exposure_only': ffine['graph_exposure_only'],
                        'reach': ffine['reach_classes_over_m2']},
        'new_detections_m3_supported_gain': ffine['m3_supported'] - base['m3_supported'],
        'new_detections_geo_gain': ffine['graph_exposure_only'] - base['graph_exposure_only'],
        'note': ('the M2-F increase over M2-A, decomposed by '
                 'dependency-supported reach (M3) vs structural graph '
                 'exposure only; a large geo_gain means the extra '
                 'detections are NOT observed propagation')}

    # which discrete channels become functional a=.05 -> a=.25
    newly_func, target_k = channel_functional_scan(
        rows, disc, 0.25, 0.05)

    # Pareto frontier over the DEDUPED operating points: several (q, a)
    # grid points map to the same operating point because the discrete
    # nulls are step functions of alpha and the quantile pool is a
    # step function in q.  A distinct point P is dominated by Q iff
    # Q.m2_rate >= P.m2_rate and Q.b2b_rate <= P.b2b_rate and strictly
    # better on one.  Each frontier entry records the (q, alpha) grid
    # cells that share it.
    distinct = {}
    for k, e in grid.items():
        key = (round(e['m2_rate'], 6), round(e['b2b_rate'], 6))
        distinct.setdefault(key, []).append(k)
    dpts = {}
    for key, cells in distinct.items():
        rep_cell = sorted(cells)[0]
        e = grid[rep_cell]
        dpts[key] = {'m2_rate': e['m2_rate'], 'b2b_rate': e['b2b_rate'],
                     'delta': e['delta_m2_minus_b2b'],
                     'cells': sorted(cells)}
    pareto_keys = []
    for key, p in dpts.items():
        dom = any((g['m2_rate'] >= p['m2_rate'] and g['b2b_rate']
                    <= p['b2b_rate']
                    and (g['m2_rate'] > p['m2_rate']
                         or g['b2b_rate'] < p['b2b_rate']))
                   for g in dpts.values() if g is not p)
        if not dom:
            pareto_keys.append(key)
    pareto = []
    for key in sorted(pareto_keys, key=lambda k: (k[1], -k[0])):
        p = dpts[key]
        pareto.append({'m2_rate': p['m2_rate'], 'b2b_rate': p['b2b_rate'],
                       'delta_m2_minus_b2b': p['delta'],
                       'n_grid_cells': len(p['cells']),
                       'grid_cells': p['cells']})

    # knees: on the deduped m2_rate-vs-b2b_rate frontier, the single-step
    # deltas between consecutive frontier points (b2b ascending).  A knee
    # is a step where delta per unit of b2b increase is large (cheap
    # specificity for sensitivity) or turns negative (past the knee, extra
    # b2b buys less/no separation).
    # knees are DATA-DRIVEN (no invented threshold): the sensitivity-
    # specificity knee is where the perturbed-minus-benign gap (delta)
    # peaks along the frontier and where the gap STOPS growing.  For each
    # frontier step we record the delta gain per unit b2b cost and flag
    # (a) the delta-maximizing operating point and (b) the step after
    # which additional b2b no longer grows the gap (diminishing
    # separation).
    ordered = [p for p in pareto]
    ordered.sort(key=lambda p: (p['b2b_rate'], -p['m2_rate']))
    knees = []
    for i in range(len(ordered) - 1):
        a, b = ordered[i], ordered[i + 1]
        db2b = b['b2b_rate'] - a['b2b_rate']
        dm2 = b['m2_rate'] - a['m2_rate']
        ddelta = b['delta_m2_minus_b2b'] - a['delta_m2_minus_b2b']
        knees.append({
            'from': a['grid_cells'], 'to': b['grid_cells'],
            'm2_gain': round(dm2, 6), 'b2b_cost': round(db2b, 6),
            'delta_gain': round(ddelta, 6),
            'delta_per_b2b_unit': (round(ddelta / db2b, 4)
                                   if db2b else None),
            'gap_stops_growing': (ddelta <= 0.0)})
    deltas = {p['grid_cells'][0]: p['delta_m2_minus_b2b'] for p in ordered}
    peak_cell = max(deltas, key=lambda c: deltas[c])
    peak_p = [p for p in ordered if peak_cell in p['grid_cells']][0]
    knee_point = {
        'max_delta_operating_point': peak_p,
        'delta_at_peak': peak_p['delta_m2_minus_b2b'],
        'first_step_gap_stops_growing': next(
            (k for k in knees if k['gap_stops_growing']), None)}
    meaningful_knees = [k for k in knees if k['gap_stops_growing']]

    rep = {
        'note': ('fine M2 threshold sweep on the cached checkpoint cache '
                 '(q x alpha, 5x8=40 points).  M2-A(q98,a.05)=2312 and '
                 'M2-F(q98,a.25)=4627 anchors verified.  M1-C kept as the '
                 'selected M1 point (untouched); M2 NOT frozen.  No '
                 'extractor/statistic/reference/label change, no trace '
                 're-read.'),
        'grid': grid,
        'anchors': {'M2_A_q98_a05': base['m2_positive'],
                    'M2_F_q98_a25': ffine['m2_positive']},
        'dim_driver_m2f_minus_m2a': dim_delta,
        'anchor_split_genuine_vs_exposure': anchor_split,
        'newly_functional_discrete_channels': newly_func,
        'channel_activation': channel_activation(rows, disc),
        'discrete_target_masses': {'%s/%s/%s' % k: sorted(int(v)
                              for v in s)
                                   for k, s in sorted(target_k.items())},
        'pareto_frontier': pareto,
        'knees': knees,
        'knee_point': knee_point,
        'n_fixtures_in_b2b': None,   # filled below
    }
    # report the distinct fixture count used by the cluster bootstrap
    fxset = set()
    for r in rows:
        if r.get('usable') and not r.get('error') and r.get('fam') in m44.FAMS:
            fxset.add(r.get('fixture') or m44.fixture_of(r['trace_id']))
    rep['n_fixtures_in_b2b'] = len(fxset)

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, 'sweep_m2_fine_v44.json'), 'w') as fh:
        json.dump(rep, fh, indent=1, sort_keys=True)

    # printed table
    print('%-10s  M2+   rate    b2b  rate   CI[lo,hi]     delta  M3 (v/p)  M4  [dim rows]'
)
    for k in sorted(grid, key=lambda x: (grid[x]['q'], grid[x]['alpha'])):
        e = grid[k]
        lo, mid, hi = e['b2b_cluster_ci95']
        dm = '/'.join('%s%d' % (d, e['by_dim_rows'].get(d, 0))
                      for d in DIMS if e['by_dim_rows'].get(d))
        print('%-10s  M2+%5d (%6.4f)  b2b %5d (%6.4f)  [%.3f,%.3f]  '
              '%+.4f  M3 %4d (v%3d/p%3d) M4 %4d/%4d  [%s]'
              % (k, e['m2_positive'], e['m2_rate'], e['b2b_fired'],
                 e['b2b_rate'], lo, hi, e['delta_m2_minus_b2b'],
                 e['m3_supported'], e['m3_verified'], e['m3_plausible'],
                 e['m4_matched'], e['m4_denominator'], dm))
    print('\nPareto frontier:', pareto)
    print('M2-F minus M2-A dim driver:', dim_delta)
    print('newly-functional discrete channels (off@.05 -> on@.25):')
    for k, v in newly_func.items():
        print('  %-30s mass=%s tails=%s' % (k, v['target_mass'],
                                            {kk: round(ttv, 4)
                                             for kk, ttv in
                                             v['tails'].items()}))
    print('knees:', knees)
    print('wrote', os.path.join(OUT, 'sweep_m2_fine_v44.json'))


if __name__ == '__main__':
    if 'PYTHONHASHSEED' not in os.environ:
        os.environ['PYTHONHASHSEED'] = '0'
        os.execve(sys.executable, [sys.executable] + sys.argv,
                  os.environ)
    main()
