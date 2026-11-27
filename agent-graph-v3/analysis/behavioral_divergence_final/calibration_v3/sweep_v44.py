#!/usr/bin/env python3.11
"""V4.4 threshold-sensitivity sweep on CACHED artifacts (lightweight).

Scope (per the pre-freeze request): move the DECISION POINT only.  No
extractor change, no representation change, no new statistical
method, no reference-label change, no trace re-read:

  M1 candidates (round-2 dev P/R/F1 + production incidence +
  execution-level LEP-free benign probe-OR):
    * continuous dims (assertions/actions): the V4.4E anti-dilution
      self-cell form  (max_p T_p - B) > c*th_self   with c in
      {1.0, 0.75, 0.50, 0.25}  (c = 1.0 is the V4.4E baseline);
    * discrete dims (decisions/outcomes): the recorded-integer
      disagree-mass null at the LEP-free 3-pair family pool with
      alpha in {0.05, 0.10, 0.25}  (the same empirical one-sided
      tail test — a threshold knob, not a new method).

  M2 candidates (checkpoint cache, production incidence + B2B
  execution row-OR):
    * continuous channels: fixture-disjoint family q-quantile,
      q in {0.98, 0.95, 0.90, 0.80}  (q98 = the baseline gate);
    * discrete channels: recorded-integer null, alpha as above.

The execution-level benign FPR for each M1 candidate is the LEP-FREE
in-cell probe row-OR under the SAME decision procedure (the 4th
exchangeable benign; at c = 1.0 the continuous part is structurally
0-firing and is verified, not assumed).  Consistency anchors:
candidate M1-A (c=1, alpha=0.05) must reproduce the frozen V44E dev
numbers (P 0.812 / R 0.667 / F1 0.732, TP56/FP13/FN28) and M2
baseline (q98/alpha=0.05) must reproduce the cached production gate
(2,312 positives).  V44E is preserved as the baseline; this sweep
REPORTS candidates, it does not select or freeze one.

Writes out_v44/sweep_v44.json.  Deterministic; PYTHONHASHSEED pinned.
"""
import bisect
import json
import math
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import m44  # noqa: E402
from m44 import DIMS, MIN_POOL  # noqa: E402
CONT = ('assertions', 'actions')
DISC = ('decisions', 'outcomes')

OUT = os.path.join(HERE, 'out_v44')

# ── cached inputs ─────────────────────────────────────────────────────────
led = m44.load_ledger()                       # V4.4 production ledger
comparable = [r for r in led.values() if r.get('comparable')]
refs = None


def load_refs():
    global refs
    if refs is not None:
        return refs
    import importlib.util as ilu
    spec = ilu.spec_from_file_location(
        'm1g43', os.path.join(
            m44.FINAL, 'calibration_v2', 'scripts', 'm1_gate_v43.py'))
    g = ilu.module_from_spec(spec)
    sys.modules['m1g43'] = g
    sys.path.insert(0, os.path.join(m44.FINAL, 'calibration_v2'))
    spec.loader.exec_module(g)
    refs = g.load_round2_refs()
    return refs


DISC_POOLS = m44.discrete_support_pools(led)


def disc_tail(pool, k, alpha):
    P = pool
    if len(P) < MIN_POOL:
        return None
    e = sum(1 for v in P if v >= float(k))
    return e / len(P) <= alpha and k > 0


# ── M1 candidate gate (self-cell c-shrink + discrete alpha) ─────────────
def m1_gates(row, c, alpha):
    fam = row.get('task_family') or 'unknown'
    fires = {}
    for d in DIMS:
        s = m44.cell_stats(row, d)
        if d in CONT:
            if s['T_max'] is None or s['B'] is None or s['th'] is None:
                fires[d] = None
            else:
                fires[d] = bool((s['T_max'] - s['B']) > c * s['th'])
        else:
            p = disc_tail((DISC_POOLS.get((fam, d)) or []), s['k'], alpha)
            fires[d] = p
    pos = [d for d in DIMS if fires.get(d) is True]
    ev = [d for d in DIMS if fires.get(d) is not None]
    return {'fires': fires, 'union': bool(pos) if ev else None}


def dev_confusion(c, alpha, refs):
    TP = FP = FN = TN = 0
    for t, ref in refs.items():
        if ref.get('label') is None:
            continue
        row = led.get(t)
        if row is None or not row.get('comparable'):
            continue
        g = m1_gates(row, c, alpha)
        pred = g['union'] is True
        pos = ref['label'] == 'diverges'
        if pos and pred:
            TP += 1
        elif pos:
            FN += 1
        elif pred:
            FP += 1
        else:
            TN += 1
    P = TP / (TP + FP) if TP + FP else None
    R = TP / (TP + FN) if TP + FN else None
    F = (2 * P * R / (P + R)) if (P and R and P + R) else None
    return {'TP': TP, 'FP': FP, 'FN': FN, 'TN': TN, 'P': P, 'R': R, 'F1': F}


# ── execution-level LEP-free benign probe OR (same decision procedure) ───
def probe_or_fpr(c, alpha):
    """The 4th-exchangeable-benign object: per in-cell probe execution
    (mean/max of the 2 in-cell pairs NOT involving it), continuous
    fires iff (probe_stat - B) > c*th_self; the discrete dims fire on
    the cell's LEP-free 3-pair disagree mass vs the same family tail.
    Row-OR over dims; fixture-cluster CI; the structural 0-firing
    property at c = 1 is verified on the continuous channel."""
    n_cells = 0
    fired = 0
    fix = {}
    cont_viol_c1 = 0
    for row in comparable:
        n_cells += 1
        fx = m44.fixture_of(row['trace_id'])
        fam = row.get('task_family') or 'unknown'
        rowf = False
        for d in DIMS:
            s = m44.cell_stats(row, d)
            if d in CONT:
                if s['th'] is None:
                    continue
                for pr in m44.probe_stats(row, d):
                    if pr['T'] is None:
                        continue
                    if c == 1.0 and (pr['T_max'] - pr['B']) > s['th']:
                        cont_viol_c1 += 1
                    if (pr['T_max'] - pr['B']) > c * s['th']:
                        rowf = True
            else:
                ben = row.get('pair_benign') or []
                vals = [((p.get('components') or {}).get(d))
                        for p in ben[:3]]
                if any(v is None for v in vals):
                    continue
                k3 = sum(1 for v in vals if v > 0)
                if disc_tail((DISC_POOLS.get((fam, d)) or []),
                             k3, alpha):
                    rowf = True
        fired += int(rowf)
        fix.setdefault(fx, []).append(1.0 if rowf else 0.0)
    rate = fired / n_cells if n_cells else None
    ci = m44.fixture_cluster_ci(fix) if fix else [None, None, rate]
    return {'rate': rate, 'n_cells': n_cells,
            'wilson95': m44.wilson(fired, n_cells) if n_cells else None,
            'fixture_cluster_bootstrap95': ci, 'n_fixtures': len(fix),
            'c1_structural_violations': cont_viol_c1}


# ── M2 candidates on the checkpoint cache ───────────────────────────────
def build_ckpt_pools():
    """Pools built from DEDUPE rows (last-wins per trace_id, exactly as
    the production compute_production_v44.py load_pairs does) — the 6
    byte-identical duplicate rows must not double-count in the q98
    pools (that shifted 3 rows' fixture thresholds vs the frozen gate)."""
    fam_pools = {f: {s: {d: [] for d in DIMS}
                     for s in ('inter', 'final')}
                 for f in m44.FAMS}
    disc = {}
    dedup = {}
    for l in open(os.path.join(OUT, 'checkpoint_pairs_v44.jsonl')):
        r = json.loads(l)
        dedup[r['trace_id']] = r
    for r in dedup.values():
        fam = r.get('fam')
        if fam not in fam_pools or not r.get('usable') or r.get('error'):
            continue
        fx = r.get('fixture') or m44.fixture_of(r['trace_id'])
        for u in r['usable']:
            scope = 'final' if u['is_final'] else 'inter'
            for d, obs in (u.get('ben_by_key') or {}).items():
                obs = [v for v in (obs or []) if v is not None]
                for v in obs:
                    fam_pools[fam][scope][d].append((fx, v))
                if d in DISC and obs:
                    kb = sum(1 for v in obs if v > 0)
                    disc.setdefault((fam, scope, d),
                                    Counter())[float(kb)] += 1
    return fam_pools, disc


fam_pools, disc_pools = build_ckpt_pools()
THR_CACHE: dict = {}


def ckpt_thr(fam, scope, d, fx, q):
    key = (fam, scope, d, fx, q)
    if key in THR_CACHE:
        return THR_CACHE[key]
    vals = [v for (f2, v) in fam_pools[fam][scope][d] if f2 != fx]
    if len(vals) < MIN_POOL:
        THR_CACHE[key] = None
        return None
    vals.sort()
    THR_CACHE[key] = vals[min(len(vals) - 1, int(q * len(vals)))]
    return THR_CACHE[key]


def ckpt_disc_null(fam, scope, d, k, alpha):
    cnt = disc_pools.get((fam, scope, d))
    if not cnt or sum(cnt.values()) < MIN_POOL:
        return None
    tot = sum(cnt.values())
    e = sum(n for kk, n in cnt.items() if kk >= float(k))
    return e / tot


def load_ckpt_rows():
    # dedupe exactly like the production gate (6 accidental duplicate
    # trace_id rows; retain-first): the gate denominator is 16,767.
    rows = {}
    for l in open(os.path.join(OUT, 'checkpoint_pairs_v44.jsonl')):
        r = json.loads(l)
        rows.setdefault(r['trace_id'], r)
    return rows


CKPT_ROWS = None


def m2_gate(q, alpha):
    """Candidate M2 gate on the checkpoint cache: continuous channels
    at fixture-disjoint q-quantile, discrete at the recorded-integer
    null with the candidate alpha.  Returns incidence + B2B objects +
    the M3/M4 joins at this candidate's M2 set."""
    global CKPT_ROWS
    if CKPT_ROWS is None:
        CKPT_ROWS = list(load_ckpt_rows().values())
    n = 0
    m2pos = 0
    by_dim = Counter()
    inter_f = 0
    final_f = 0
    probe_eval = probe_fire = 0
    row_or = 0
    rows_pos = []
    for r in CKPT_ROWS:
        fam = r.get('fam')
        if fam not in m44.FAMS or r.get('error') or not r.get('usable'):
            continue
        n += 1
        fx = r.get('fixture') or m44.fixture_of(r['trace_id'])
        usable = [u for u in r['usable'] if u.get('is_cross_agent')]
        fired = []
        rfired = False
        for u in usable:
            scope = 'final' if u['is_final'] else 'inter'
            for d in CONT:
                v = (u.get('comp_mean') or {}).get(d)
                if v is None:
                    continue
                t = ckpt_thr(fam, scope, d, fx, q)
                if t is None:
                    continue
                row_app_fired = v > t
                if row_app_fired:
                    fired.append((u.get('key'), d))
                obs = [x for x in (u.get('ben_by_key') or {}).get(d, [])
                       if x is not None]
                for x in obs:
                    probe_eval += 1
                    if x > t:
                        probe_fire += 1
                        rfired = True
            for d in DISC:
                kk = (u.get('comp_k') or {}).get(d)
                if kk is None or kk == 0:
                    continue
                pnull = ckpt_disc_null(fam, scope, d, kk, alpha)
                if pnull is None:
                    continue
                if pnull <= alpha:
                    fired.append((u.get('key'), d + ':k' + str(kk)))
                kobs = sum(1 for x in (u.get('ben_by_key') or {}).get(
                    d, []) if x > 0)
                pb = ckpt_disc_null(fam, scope, d, kobs, alpha)
                if kobs > 0 and pb is not None and pb <= alpha:
                    rfired = True
        is_pos = bool(fired)
        m2pos += int(is_pos)
        if is_pos:
            for _k, d in fired:
                by_dim[d.split(':')[0]] += 1
            if any(k != 'final' for k, _d in fired):
                inter_f += 1
            if any(k == 'final' for k, _d in fired):
                final_f += 1
            rows_pos.append(r['trace_id'])
        row_or += int(rfired)
    # M3 / M4 at this candidate's M2 set
    m4_by = {rr['trace_id']: rr.get('m4_class')
             for rr in m44._jsonl(m44.V6_LEDGER)}
    reach_by = {rr['trace_id']: rr.get('m3_reach_class') for rr in CKPT_ROWS}
    deliv_by = {rr['trace_id']: rr.get('delivery_verified') for rr in CKPT_ROWS}
    m2set = set(rows_pos)
    m3 = [t for t in m2set
          if reach_by.get(t) in ('verified', 'plausible')
          and deliv_by.get(t)]
    m4 = sum(1 for t in m3 if (m4_by.get(t) or 'none') == 'degraded')
    return {'n_rows': n, 'm2_positive': m2pos,
            'm2_rate': m2pos / n if n else None,
            'by_dim': dict(by_dim),
            'inter_fired': inter_f, 'final_fired': final_f,
            'probe_obs': probe_eval, 'probe_fire': probe_fire,
            'probe_fpr': (probe_fire / probe_eval) if probe_eval else None,
            'b2b_row_or': row_or,
            'b2b_row_or_fpr': row_or / n if n else None,
            'm3_supported': len(m3),
            'm3_verified': sum(1 for t in m3
                               if reach_by.get(t) == 'verified'),
            'm3_plausible': sum(1 for t in m3
                                if reach_by.get(t) == 'plausible'),
            'm4_matched': m4, 'm4_denominator': len(m3)}


def main():
    refs_ = load_refs()
    # ── M1 sweep: c in {1,.75,.5,.25} x alpha in {.05,.10,.25} ─────
    M1_GRID = [(1.0, 0.05, 'M1-A (V44E baseline: c=1.0, a=.05)'),
               (0.75, 0.05, 'M1-B (c=.75, a=.05)'),
               (0.50, 0.05, 'M1-C (c=.50, a=.05)'),
               (0.25, 0.05, 'M1-D (c=.25, a=.05)'),
               (1.0, 0.10, 'M1-E (c=1.0, a=.10)'),
               (0.50, 0.10, 'M1-F (c=.50, a=.10)'),
               (0.50, 0.25, 'M1-G (c=.50, a=.25)'),
               (0.25, 0.25, 'M1-H (c=.25, a=.25)')]
    m1out = {}
    for c, alpha, name in M1_GRID:
        conf = dev_confusion(c, alpha, refs_)
        conf['wilson95_fp'] = m44.wilson(conf['FP'], conf['n_neg']
                                         if 'n_neg' in conf else conf['FP'] +
                                         conf['TN']) if conf['FP'] + conf['TN'] else None
        conf['dev_fpr'] = conf['FP'] / (conf['FP'] + conf['TN']) \
            if conf['FP'] + conf['TN'] else None
        inc = 0
        n_inc = 0
        for row in comparable:
            n_inc += 1
            g = m1_gates(row, c, alpha)
            inc += int(g['union'] is True)
        s2 = probe_or_fpr(c, alpha)
        m1out[name] = {'c': c, 'alpha': alpha, 'dev': conf,
                       'm1_incidence': {'positive': inc, 'n': n_inc,
                                        'rate': inc / n_inc
                                        if n_inc else None},
                       'exec_level_benign_fpr': s2}
    # consistency anchor: M1-A must equal the frozen V44E dev numbers
    a = m1out['M1-A (V44E baseline: c=1.0, a=.05)']['dev']
    assert (a['TP'], a['FP'], a['FN']) == (56, 13, 28), \
        ('V44E baseline drift in sweep: ', a)
    # ── M2 sweep: q in {.98,.95,.90,.80} x alpha in {.05,.10,.25} ──
    M2_GRID = [
        (0.98, 0.05, 'M2-A (q98, a=.05)  [baseline gate]'),
        (0.95, 0.05, 'M2-B (q95, a=.05)'),
        (0.90, 0.10, 'M2-C (q90, a=.10)'),
        (0.80, 0.10, 'M2-D (q80, a=.10)'),
        (0.80, 0.25, 'M2-E (q80, a=.25)'),
        (0.98, 0.25, 'M2-F (q98, a=.25)')]
    m2out = {}
    for q, alpha, name in M2_GRID:
        m2out[name] = m2_gate(q, alpha)
    b = m2out['M2-A (q98, a=.05)  [baseline gate]']
    assert b['m2_positive'] == 2312, ('M2 baseline drift: ', b['m2_positive'])

    # ── Pareto frontier over (P, R, exec-level FPR) on the M1 grid ──
    cand = []
    for name, e in m1out.items():
        d = e['dev']
        if d['P'] is None or d['R'] is None:
            continue
        cand.append({
            'name': name,
            'P': round(d['P'], 4), 'R': round(d['R'], 4),
            'F1': round(d['F1'], 4),
            'exec_fpr': round(e['exec_level_benign_fpr']['rate'] or 0, 4),
            'exec_fpr_ci': [round(x, 4) for x in
                            e['exec_level_benign_fpr'][
                                'fixture_cluster_bootstrap95']],
            'm1_incidence': round(e['m1_incidence']['rate'] or 0, 4),
            'dev_fpr': round(d['dev_fpr'] or 0, 4),
            'passes_PRF1': (d['P'] >= 0.70 and d['R'] >= 0.70
                            and d['F1'] >= 0.65),
            'passes_5pct': (e['exec_level_benign_fpr']['rate'] or 1)
                           <= 0.05})
    cand.sort(key=lambda x: (x['P'], x['R']))
    pareto = []
    for i, a_ in enumerate(cand):
        dominated = any(
            (b_['P'] >= a_['P'] and b_['R'] >= a_['R']
             and b_['exec_fpr'] <= a_['exec_fpr'])
            and (b_['P'] > a_['P'] or b_['R'] > a_['R']
                 or b_['exec_fpr'] < a_['exec_fpr'])
            for b_ in cand if b_ is not a_)
        if not dominated:
            pareto.append(a_['name'])

    rep = {
        'note': ('threshold-sensitivity sweep on the cached V4.4 '
                 'measurements + calibrated nulls (self-cell shrink '
                 'c, discrete alpha, family-pool q).  V44E preserved '
                 'as the baseline (M1-A / M2-A).  This sweep REPORTS; '
                 'it does not select or freeze an operating point.  '
                 'No extractor/representation/reference/trace '
                 'change.'),
        'm1_grid': m1out, 'm2_grid': m2out,
        'pareto_frontier': pareto,
        'candidates_meeting_PRF1': [c_['name'] for c_ in cand
                                    if c_['passes_PRF1']],
        'candidates_passing_5pct_exec_fpr': [c_['name'] for c_ in cand
                                              if c_['passes_5pct']],
        'candidates_failing_5pct': [c_['name'] for c_ in cand
                                    if not c_['passes_5pct']]}
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, 'sweep_v44.json'), 'w') as fh:
        json.dump(rep, fh, indent=1, sort_keys=True)
    # compact printed table
    print('%-28s %6s %6s %6s %8s %8s %7s %6s' % (
        'candidate', 'P', 'R', 'F1', 'execFPR', 'M1inc', 'devFPR', 'ok5%'))
    for c_ in cand:
        print('%-28s %6.3f %6.3f %6.3f %8.4f %8.4f %7.4f %6s' % (
            c_['name'][:28], c_['P'], c_['R'], c_['F1'], c_['exec_fpr'],
            c_['m1_incidence'], c_['dev_fpr'],
            'YES' if c_['passes_5pct'] else 'no'))
    print('M2 grid:')
    for name, e in m2out.items():
        print('  %-26s M2+%5d (%.4f)  inter%d/final%d  probeFPR %.4f  '
              'rowOR %.4f  M3 %d (v%d/p%d)  M4 %d/%d' % (
                  name[:26], e['m2_positive'], e['m2_rate'] or 0,
                  e['inter_fired'], e['final_fired'],
                  e['probe_fpr'] or 0, e['b2b_row_or_fpr'] or 0,
                  e['m3_supported'], e['m3_verified'], e['m3_plausible'],
                  e['m4_matched'], e['m4_denominator']))
    print('Pareto frontier:', pareto)
    print('meeting P/R/F1:', rep['candidates_meeting_PRF1'] or 'NONE')
    print('passing 5%% exec-FPR:', rep['candidates_passing_5pct_exec_fpr']
          or 'NONE')
    print('wrote', os.path.join(OUT, 'sweep_v44.json'))


if __name__ == '__main__':
    if 'PYTHONHASHSEED' not in os.environ:
        os.environ['PYTHONHASHSEED'] = '0'
        os.execve(sys.executable, [sys.executable] + sys.argv,
                  os.environ)
    main()
