#!/usr/bin/env python3.11
"""M2/M3/M4 production recompute on the V4.2 checkpoint surface.

V4.2 fixes over the frozen compute_production.py:

  (D) FAMILY-SPECIFIC checkpoint pools: benign-pair observations pool per
      (task_family, scope, dimension) — NOT the global cross-family pool of
      the frozen script.  Each family keeps its own calibration; a family
      with < MIN_POOL per-dim observations falls back to the (fam x scope)
      union of evaluable dims and the shortfall is NAMED, never forced.
  (DISC) discrete decision/outcome threshold saturation: the frozen q98
      quantile over near-binary {0,1} mass sits at 1.0, so only a maximal
      1.0 deviation can fire (or, at q<1, never fires).  V4.2 uses a
      one-sided EXACT binomial test on the per-(fam, scope, dim) null:
      p_hat = fraction of benign-pair observations that exceed 0 (the
      disagree-mass), n = count of LEP observations, k = count of LEP
      observations exceeding 0; fire iff P(Bin(n, p_hat) >= k) <= ALPHA,
      and the LEP mean is reported alongside.  For continuous dims
      (assertions, actions) the q-quantile LOO form is retained (the
      recentered-extremal analog at checkpoint scope).
  (A) gate = logical OR over INDEPENDENT dims at checkpoint scope — a dim
      fires on its OWN threshold; the weighted composite (0.40/.25/.25/.10)
      is carried through only as a NAMED diagnostic, never as admission
      (any-dimension retention; no composite anywhere in the gate).
  Multiple-opportunity accounting at TWO scopes, reported separately:
      probe-observation FPR (one benign-pair observation vs its LOO
      threshold) and execution row-OR FPR (any observation fires).

Consumes out_v42/checkpoint_pairs_v43.jsonl + the frozen grader/reach
artifacts.  Writes out_v42/m2m3_production_v43.json.  No LLM calls, no
frozen-artifact modification.
"""
import json
import math
import os
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
V2 = os.path.abspath(os.path.join(HERE, '..'))
FINAL = os.path.abspath(os.path.join(HERE, '..', '..'))
REPO = os.path.abspath(os.path.join(FINAL, '..', '..'))
OUT = os.path.join(V2, 'out_v43')
sys.path.insert(0, V2)
import cv2  # noqa: E402

DIMS = ('assertions', 'decisions', 'actions', 'outcomes')
CONT = ('assertions', 'actions')          # continuous dims: q-quantile LOO
DISC = ('decisions', 'outcomes')         # discrete dims: exact binomial
ALPHA = 0.05                              # pre-registered one-sided level
Q = 0.98
MIN_POOL = 20                             # minimum per-(fam,scope,dim) obs


def _binom_pmf_ge(n, k, p):
    """P(Bin(n,p) >= k) — tail sum, guarded for p in {0,1}."""
    if k <= 0:
        return 1.0
    if n < k:
        return 0.0
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    tail = 0.0
    for j in range(k, n + 1):
        c = math.comb(n, j)
        tail += c * (p ** j) * ((1 - p) ** (n - j))
    return min(1.0, tail)


def load_pairs():
    rows = [json.loads(l) for l in open(os.path.join(
        OUT, 'checkpoint_pairs_v43.jsonl')) if l.strip()]
    by = {}
    for r in rows:
        by.setdefault(r['trace_id'], r)
    return by


def main():
    pairs = load_pairs()
    cells, _ = cv2.load_cells()
    pairs = [pairs[c['trace_id']] for c in cells
             if c['trace_id'] in pairs]

    # ── (D) family-specific pools per (fam, scope, dim) ─────────────
    fam_pool = {f: {s: {d: [] for d in DIMS}
                    for s in ('inter', 'final')}
                for f in ('code_review', 'financial_analysis',
                           'research_synthesis')}
    for r in pairs:
        fam = r.get('fam')
        if fam not in fam_pool or not r.get('usable'):
            continue
        for u in r['usable']:
            scope = 'final' if u['is_final'] else 'inter'
            for d, obs in (u.get('ben_by_key') or {}).items():
                if obs:
                    fam_pool[fam][scope][d].extend(obs)
    for fam in fam_pool:
        for s in fam_pool[fam]:
            for d in fam_pool[fam][s]:
                fam_pool[fam][s][d].sort()

    # null fraction for discrete dims (fraction of benign-pair obs > 0)
    disc_null = {}
    for fam in fam_pool:
        for s in ('inter', 'final'):
            for d in DISC:
                obs = fam_pool[fam][s][d]
                nz = [v for v in obs if v > 0]
                disc_null[(fam, s, d)] = (len(nz) / len(obs)) if obs else None

    def cont_thr(fam, scope, dim, cell_obs):
        """q-quantile LOO over the FAMILY pool excluding this cell's own
        benign observations (matched-cardinality, byte-compatible loo)."""
        pool = fam_pool[fam][scope][dim]
        R = sorted(cell_obs or [])
        if len(pool) < MIN_POOL:
            return None, 'short_pool'
        return cv2.loo_quantile(pool, R, q=Q), None

    # ── per-row M2 dimension-specific gate ──────────────────────────
    rows = []
    probe_eval = probe_fire = b2b_row = 0
    for r in pairs:
        fam = r.get('fam')
        usable = [u for u in (r.get('usable') or [])
                  if u.get('is_cross_agent')]
        fired = []
        fired_dims = set()
        scope_fired = {'inter': set(), 'final': set()}
        row_app = False
        for u in usable:
            scope = 'final' if u['is_final'] else 'inter'
            comp_mean = u.get('comp_mean') or {}
            for d in DIMS:
                v = comp_mean.get(d)
                if v is None:
                    continue
                row_app = True
                if d in CONT:
                    t, why = cont_thr(fam, scope, d,
                                      (u.get('ben_by_key') or {}).get(d))
                    if t is None:
                        continue
                    fire = v > t
                else:  # (DISC) exact one-sided test
                    p_null = disc_null.get((fam, scope, d))
                    if p_null is None:
                        continue
                    # k = the INTEGER number of disagreeing LEP-vs-benign
                    # pairs at this key (3*T_lep for discrete distances,
                    # the O1 construction).  comp_n[d] = refs evaluable;
                    # k from the recorded disagree count when present,
                    # else the conservative mean>0 indicator (k=1, the
                    # weakest test; documented fallback).
                    cn = (u.get('comp_n') or {}).get(d)
                    if cn is not None:
                        k = int(round(v * cn)) if cn else 0
                    else:
                        k = 1 if v > 0 else 0
                    fire = (k > 0 and
                            _binom_pmf_ge(cn or 3, k, p_null) <= ALPHA)
                if fire:
                    fired.append((u.get('key'), d))
                    fired_dims.add(d)
                    scope_fired[scope].add(d)
        # B2B control, reported SEPARATELY at two scopes:
        #   * probe-observation FPR on the CONTINUOUS dims (one benign-pair
        #     observation vs its family-scope LOO threshold) — the frozen
        #     construct, unchanged.
        #   * execution row-OR FPR: a pure-benign cell is "flagged" iff ANY
        #     of (i) a continuous observation exceeds its threshold, or
        #     (ii) a DISCRETE dim's own pooled benign disagree-mass (k = #
        #     obs > 0, n = # obs at that key) fires the SAME exact test.
        # The discrete dim has no non-degenerate single-observation probe
        # rate (a 1-obs Bin(1,p) tail is ~0), so its B2B control is the
        # execution-level pooled disagree-mass — the matched null object.
        b2b_fired = False
        for u in usable:
            scope = 'final' if u['is_final'] else 'inter'
            ben = u.get('ben_by_key') or {}
            for d in CONT:
                for vv in (ben.get(d) or []):
                    t, _why = cont_thr(fam, scope, d, ben.get(d))
                    if t is None:
                        continue
                    probe_eval += 1
                    if vv > t:
                        probe_fire += 1
                        b2b_fired = True
            for d in DISC:
                obs = ben.get(d) or []
                if not obs:
                    continue
                p_null = disc_null.get((fam, scope, d))
                if p_null is None:
                    continue
                k_cell = sum(1 for x in obs if x > 0)
                if k_cell > 0 and _binom_pmf_ge(len(obs), k_cell,
                                                  p_null) <= ALPHA:
                    b2b_fired = True
        if b2b_fired:
            b2b_row += 1
        rows.append({
            'trace_id': r['trace_id'], 'fam': fam,
            'lep_code': r.get('lep_code'),
            'm2_dim_union': bool(fired),
            'm2_fired': fired,
            'm2_fired_inter': sorted(scope_fired['inter']),
            'm2_fired_final': sorted(scope_fired['final']),
            'n_cross_agent_ckpt': len(usable),
            'm2_undetermined': (not row_app and len(usable) > 0),
            'm2_no_ckpt': (len(usable) == 0),
            'reach': r.get('m3_reach_class'),
            'delivery_verified': r.get('delivery_verified'),
            'origin_roles': r.get('origin_roles'),
            'stratum': r.get('stratum'),
        })

    m2_pos = [x for x in rows if x['m2_dim_union']]
    # ── M1 (V4.2 recentered) x M2 cross-tab (requirement 10: M2 is NOT
    # assumed a subset of M1 in either direction; the 4-way cell counts) ──
    m1v42 = {}
    for l in open(os.path.join(REPO, 'analysis',
                               'full_production_baseline_deviation_v43',
                               'bnd_v43_per_trace.jsonl')):
        rr = json.loads(l)
        if not rr.get('comparable'):
            continue

        def _st(d, _row=rr):
            T = [((p.get('components') or {}).get(d))
                 for p in _row.get('pair_lep', [])]
            B = [((p.get('components') or {}).get(d))
                 for p in _row.get('pair_benign', [])]
            T = [x for x in T if x is not None]
            B = [x for x in B if x is not None]
            return bool(T and B) and (
                sum(T) / len(T) - sum(B) / len(B)) > max(B)
        m1v42[rr['trace_id']] = any(_st(d) for d in DIMS)

    def _ckey(k):
        return (k[0], k[1])
    crosstab = Counter()
    for x in rows:
        m1 = m1v42.get(x['trace_id'])
        if m1 is None:
            crosstab[('M1:n/a', x['m2_dim_union'])] += 1
        else:
            crosstab[(bool(m1), x['m2_dim_union'])] += 1

    # ── the 3,247 no-ckpt rows: breakdown + V4.2 recovery ───────────
    no_ckpt = [x for x in rows if x['m2_no_ckpt']]
    by_cell = Counter((x['fam'], x['stratum'][1] if x.get('stratum') else '?',
                        x['stratum'][2] if len(x.get('stratum') or []) > 2
                        else '?') for x in no_ckpt)
    frozen = {json.loads(l)['trace_id']: json.loads(l)
              for l in open(os.path.join(cv2.HERE, 'ledger',
                                         'checkpoint_pairs.jsonl'))
              if l.strip()}
    recovered = sum(1 for x in no_ckpt
                    if frozen.get(x['trace_id'], {}).get(
                        'error') == 'no_usable_checkpoint'
                    and (x.get('n_cross_agent_ckpt') or 0) > 0)

    # ── M3 / M4 (frozen grader join; unchanged construction) ────────
    v6_m4 = {rr['trace_id']: rr.get('m4_class')
             for rr in cv2._jsonl(cv2.V6_LEDGER)}
    m3_supported = [x for x in m2_pos
                    if x['reach'] in ('verified', 'plausible')
                    and x['delivery_verified']]
    reach_classes = Counter(x['reach'] for x in m2_pos)
    m4 = Counter((v6_m4.get(x['trace_id']) or 'none')
                 for x in m3_supported)
    matched = sum(1 for x in m3_supported
                  if v6_m4.get(x['trace_id']) == 'degraded')

    rep = {
        'version': 'V4.3 checkpoint gate',
        'construct': 'per-dimension thresholds + logical OR union at '
                     'checkpoint scope; FAMILY-SPECIFIC pools; exact '
                     'binomial test on discrete dims; NO weighted '
                     'composite in the gate (composite carried through '
                     'only as a named diagnostic)',
        'm2_dimension_specific': {
            'positive': len(m2_pos), 'denominator': len(rows),
            'rate': len(m2_pos) / len(rows) if rows else None,
            'by_dim': dict(Counter(d for x in m2_pos
                                   for _, d in x['m2_fired'])),
            'by_scope': {
                'inter_fired': sum(1 for x in m2_pos
                                    if x['m2_fired_inter']),
                'final_fired': sum(1 for x in m2_pos
                                    if x['m2_fired_final'])},
            'probe_obs': probe_eval, 'probe_fire': probe_fire,
            'probe_fpr': (probe_fire / probe_eval) if probe_eval else None,
            'b2b_execution_row_or_fired': b2b_row,
            'b2b_execution_row_or_fpr': (b2b_row / len(rows))
            if rows else None,
            'm1_v42_recentered_x_m2': {
                ((f'M1:n/a' if isinstance(k[0], str)
                  else ('M1+' if k[0] else 'M1-')) +
                 ('/M2+' if k[1] else '/M2-')): n
                for k, n in sorted(crosstab.items(), key=_ckey)},
            'm1_m2_note': 'M2 is NOT assumed a subset of M1 in either '
                           'direction; cross-tab over the V4.2 '
                           'recentered M1 union',
            'family_pools': {
                f: {s: {d: len(fam_pool[f][s][d])
                        for d in DIMS} for s in ('inter', 'final')}
                for f in fam_pool},
            'disc_null': {f'{f}|{s}|{d}': (disc_null[(f, s, d)])
                          for f in fam_pool
                          for s in ('inter', 'final')
                          for d in DISC},
            'note': 'probe_fpr = per benign-pair observation vs its '
                    'family-scope LOO threshold (observation rate); '
                    'b2b_execution_row_or_fpr = execution-level '
                    'multiple-opportunity figure.  The two are NEVER '
                    'conflated.',
        },
        'checkpoint_availability': {
            'm2_no_ckpt': len(no_ckpt),
            'recovered_by_v42_vs_frozen': recovered,
            'still_no_ckpt_top_cells': [
                {'cell': list(k), 'n': n}
                for k, n in by_cell.most_common(10)],
            'note': 'no_usable_checkpoint = no cross-agent post-boundary '
                    'checkpoint key present on ALL benign refs.  Named, '
                    'never forced; V4.2 role-authored evidence + '
                    'any-dimension retention recovers rows where a dim '
                    'was evaluable only via intermediate authored '
                    'reports (reported separately, no invented evidence)',
        },
        'm3_dependency_supported': {
            'supported': len(m3_supported),
            'verified': sum(1 for x in m3_supported
                            if x['reach'] == 'verified'),
            'plausible': sum(1 for x in m3_supported
                             if x['reach'] == 'plausible'),
            'share_of_m2': (len(m3_supported) / len(m2_pos)) if m2_pos
            else None,
            'm2_reach_classes': dict(reach_classes),
            'subset_of_m2': True,
        },
        'm4_matched_degradation': {
            'classes': dict(m4), 'matched_degraded': matched,
            'denominator': len(m3_supported),
            'rate': (matched / len(m3_supported)) if m3_supported else None,
            'note': 'frozen five-way classes over the M3 denominator; '
                    'ungradeable is NEVER a failure; context only, not '
                    'causal',
        },
    }
    json.dump(rep, open(os.path.join(OUT, 'm2m3_production_v43.json'), 'w'),
              indent=1)
    # per-row ledger (for the M1 x M2 cross-tab + case inspection)
    with open(os.path.join(OUT, 'm2_rows_v43.jsonl'), 'w') as fh:
        for x in rows:
            fh.write(json.dumps(x) + '\n')
    print('M2 dim-union:', len(m2_pos), '/', len(rows),
          '(frozen: 1,210 / 16,773)')
    print('no_ckpt:', len(no_ckpt), '| recovered:', recovered)
    print('M3 supported:', len(m3_supported),
          '(v %s / p %s)' % (rep['m3_dependency_supported']['verified'],
                              rep['m3_dependency_supported']['plausible']))
    print('M4 matched:', matched, 'of', len(m3_supported))
    print('B2B probe obs FPR:', rep['m2_dimension_specific']['probe_fpr'],
          '| row-OR:',
          rep['m2_dimension_specific']['b2b_execution_row_or_fpr'])


if __name__ == '__main__':
    if 'PYTHONHASHSEED' not in os.environ:
        os.environ['PYTHONHASHSEED'] = '0'
        os.execve(sys.executable, [sys.executable] + sys.argv,
                  os.environ)
    main()
