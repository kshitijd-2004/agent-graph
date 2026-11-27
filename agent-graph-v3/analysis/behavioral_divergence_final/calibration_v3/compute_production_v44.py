#!/usr/bin/env python3.11
"""V4.4 consolidated downstream production gate (M2/M3/M4).

Consumes out_v44/checkpoint_pairs_v44.jsonl (the V4.4 checkpoint
recompute) + the frozen reach/m4 joins.  The pre-registered gate form:

  * per-dimension thresholds + LOGICAL OR over the four independent
    dimensions (NO weighted composite anywhere in the gate; the
    0.40/.25/.25/.10 composite stays a NAMED availability diagnostic);
  * continuous channels (assertions, actions): cross-fixture family
    q98 on the LEP-free benign-pair pools (individual observations,
    frozen-comparable to the V4.3 pool), FIXTURE-DISJOINT LOO (all
    rows sharing the fixture's reference set excluded together — the
    sibling-blob leakage the V4.3 cell-level LOO missed);
  * discrete-support channels (decisions, outcomes): the LEP-free
    family pool of the RECORDED integer disagree-mass at the key
    (count of benign-pair distances > 0), one-sided tail on the
    ACTUAL support, ties counted, alpha=0.05 — the
    round(mean*n)-then-binomial reconstruction is NOT used;
  * the mean statistic fires a channel; the sparse MAX statistic is
    scored on the SAME threshold as a NAMED secondary channel (its
    fires are reported alongside, never selected for);
  * M2 = cross-agent checkpoint OR; every firing carries its
    provenance record (key, dim, action_scope_roles, excluded origin
    roles, recorded comp_k); a positive whose only firings are
    final-scope AND whose reach class is not verified/plausible is
    NAMED graph_exposure_only (structural exposure is not observed
    propagation);
  * M3 (matched task degradation) = the M2 subset gated on
    dependency-supported reach (verified|plausible) AND
    delivery_verified; the verified/plausible split is the INTERNAL M2
    breakdown — M3's denominator is the M2-supported set, M4's is M3
    (no double counting);
  * M1 x M2 cross-tab: M1 = the V4.4 ledger self-cell recentered-mean
    union (the V4.3 OP0 gate object on V4.4 distances).

B2B reporting at the three named scopes (never conflated): the
probe-observation rate here (one benign-pair observation vs its
fixture-disjoint threshold); the execution-level OR + fixture-cluster
CI lives on the dev side (m44.s2_cross_fixture); the structural in-
cell property is m44.s1_structural.  No LLM calls; frozen V4.2/V4.3
artifacts untouched.  Writes out_v44/m2m3_production_v44.json +
m2_rows_v44.jsonl.
"""
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
FINAL = os.path.abspath(os.path.join(HERE, '..'))
REPO = os.path.abspath(os.path.join(FINAL, '..', '..'))
OUT = os.path.join(HERE, 'out_v44')
sys.path.insert(0, HERE)
import m44  # noqa: E402

DIMS = m44.DIMS
CONT = ('assertions', 'actions')
DISC = ('decisions', 'outcomes')
ALPHA = m44.ALPHA
Q = 0.98
MIN_POOL = m44.MIN_POOL


def load_pairs():
    rows = [json.loads(l) for l in open(os.path.join(
        OUT, 'checkpoint_pairs_v44.jsonl')) if l.strip()]
    return {r['trace_id']: r for r in rows}


def main():
    rows = load_pairs()
    v44 = m44.load_ledger()

    # ── (D) fixture-tagged family pools per (fam, scope, dim) ──────
    # Frozen-comparable to the V4.3 construct: individual benign-pair
    # observations pool per (fam, scope, dim) (NOT the 3-reference
    # means — that lowers the bar and the per-key OR over ~30 keys
    # explodes by multiple-opportunity).  The one V4.4 correction to
    # this pool is the FIXTURE-DISJOINT leave-out in cont_thr below:
    # every row sharing the fixture's reference set is excluded together
    # (the 1,474 byte-identical sibling p3 blobs the V4.3 cell-level LOO
    # leaked), instead of excluding only the single cell's own obs.
    fam_pools = {f: {s: {d: [] for d in DIMS}
                     for s in ('inter', 'final')}
                 for f in m44.FAMS}
    disc_pools = {}   # (fam, scope, dim) -> Counter over RECORDED k
    for r in rows.values():
        fam = r.get('fam')
        if fam not in fam_pools or not r.get('usable') or r.get('error'):
            continue
        fx = r.get('fixture') or m44.fixture_of(r['trace_id'])
        for u in r['usable']:
            scope = 'final' if u['is_final'] else 'inter'
            for d, obs in (u.get('ben_by_key') or {}).items():
                obs = [v for v in (obs or []) if v is not None]
                for v in obs:                       # individual obs
                    fam_pools[fam][scope][d].append((fx, v))
                if d in DISC and obs:
                    kb = sum(1 for v in obs if v > 0)
                    disc_pools.setdefault((fam, scope, d),
                                          Counter())[float(kb)] += 1
    for fam in fam_pools:
        for s in fam_pools[fam]:
            for d in fam_pools[fam][s]:
                fam_pools[fam][s][d] = sorted(
                    fam_pools[fam][s][d], key=lambda p: p[1])

    def cont_thr(fam, scope, dim, fixture):
        """Cross-fixture q98, fixture-disjoint LOO: every row sharing
        this fixture's reference set is excluded (the 1,474
        byte-identical sibling blobs the cell-level LOO leaked); None
        (NAMED short pool) when the disjoint pool is too thin."""
        vals = [v for (fx, v) in fam_pools[fam][scope][dim]
                if fx != fixture]
        if len(vals) < MIN_POOL:
            return None
        vals.sort()
        return vals[min(len(vals) - 1, int(Q * len(vals)))]

    def disc_null(fam, scope, dim, k_target):
        """One-sided tail on the RECORDED integer support (LEP-free:
        the pool holds benign-pair disagree counts), ties counted;
        None when the support is too thin (NAMED, never forced)."""
        c = disc_pools.get((fam, scope, dim))
        if not c or sum(c.values()) < MIN_POOL:
            return None
        tot = sum(c.values())
        e = sum(n for k, n in c.items() if k >= float(k_target))
        return e / tot

    # ── per-row M2 gate ─────────────────────────────────────────────
    m2rows = []
    probe_eval = probe_fire = 0
    b2b_row_or = 0        # execution-level B2B row-OR (separate object)
    b2b_rows = 0
    for r in rows.values():
        fam = r.get('fam')
        fx = r.get('fixture') or m44.fixture_of(r['trace_id'])
        if r.get('error') or not r.get('usable'):
            m2rows.append({'trace_id': r['trace_id'], 'fam': fam,
                           'fixture': fx,
                           'm2_dim_union': None,
                           'm2_no_ckpt': not r.get('usable'),
                           'm2_undetermined': True,
                           'n_cross_agent_ckpt': 0})
            continue
        usable = [u for u in r['usable'] if u.get('is_cross_agent')]
        fired = []
        scope_fired = {'inter': set(), 'final': set()}
        fired_provenance = []
        sparse_max_fires = []
        row_app = False
        for u in usable:
            scope = 'final' if u['is_final'] else 'inter'
            # continuous channels: mean statistic vs fixture-disjoint
            # family threshold (the sparse max on the SAME threshold
            # is recorded as a named secondary channel)
            for d in CONT:
                v = (u.get('comp_mean') or {}).get(d)
                if v is None:
                    continue
                row_app = True
                thr = cont_thr(fam, scope, d, fx)
                if thr is None:
                    continue
                if v > thr:
                    fired.append((u.get('key'), d))
                    scope_fired[scope].add(d)
                    fired_provenance.append({
                        'key': u.get('key'), 'dim': d,
                        'action_scope_roles':
                            u.get('action_scope_roles'),
                        'action_excluded_origin_roles':
                            u.get('action_excluded_origin_roles'),
                        'comp_k': (u.get('comp_k') or {}).get(d)})
                cd = (u.get('comp_dist') or {}).get(d) or []
                vmax = max(cd) if cd else None
                if vmax is not None and vmax > thr:
                    sparse_max_fires.append((u.get('key'), d))
            # discrete channels: the RECORDED integer disagree mass
            for d in DISC:
                kk = (u.get('comp_k') or {}).get(d)
                if kk is None or kk == 0:
                    continue
                p_null = disc_null(fam, scope, d, kk)
                if p_null is None:
                    continue
                if p_null <= ALPHA:
                    tag = d + ':k' + str(kk)
                    fired.append((u.get('key'), tag))
                    scope_fired[scope].add(d)
                    fired_provenance.append(
                        {'key': u.get('key'), 'dim': tag,
                         'action_scope_roles':
                         u.get('action_scope_roles'),
                         'comp_k': kk})
        # B2B observation-scope control (frozen-comparable: ONE benign-
        # pair observation vs its fixture-disjoint threshold; the
        # execution-level OR over probes is a SEPARATE named object,
        # reported below, never conflated with this observation rate)
        row_fired = False
        for u in usable:
            scope = 'final' if u['is_final'] else 'inter'
            for d in CONT:
                for vv in [v for v in ((u.get('ben_by_key') or {}).get(d)
                                        or []) if v is not None]:
                    t = cont_thr(fam, scope, d, fx)
                    if t is None:
                        continue
                    probe_eval += 1
                    if vv > t:
                        probe_fire += 1
                        row_fired = True
        b2b_rows += 1
        b2b_row_or += int(row_fired)
        m2_pos = bool(fired)
        reach = r.get('m3_reach_class')
        inter_fired = any(k != 'final' for k, _d in fired)
        graph_exposure_only = (m2_pos and not inter_fired
                               and reach in ('not_downstream',
                                             'unsupported'))
        m2rows.append({
            'trace_id': r['trace_id'], 'fam': fam, 'fixture': fx,
            'lep_code': r.get('lep_code'),
            'm2_dim_union': m2_pos,
            'm2_fired': fired,
            'm2_fired_inter': sorted(scope_fired['inter']),
            'm2_fired_final': sorted(scope_fired['final']),
            'm2_fired_provenance': fired_provenance,
            'm2_sparse_max_fires': sparse_max_fires,
            'n_cross_agent_ckpt': len(usable),
            'm2_undetermined': (not row_app and len(usable) > 0),
            'm2_no_ckpt': False,
            'reach': reach,
            'delivery_verified': r.get('delivery_verified'),
            'origin_roles': r.get('origin_roles'),
            'graph_exposure_only': graph_exposure_only,
            'stratum': r.get('stratum'),
        })

    m2pos = [x for x in m2rows if x.get('m2_dim_union')]
    no_ckpt = [x for x in m2rows if x.get('m2_no_ckpt')]

    # ── M1 (V4.4 ledger, self-cell recentered mean = the V4.3 OP0
    # object on V4.4 distances) x M2 cross-tab ──────────────────────
    def m1_union(tid):
        row = v44.get(tid)
        if row is None or not row.get('comparable'):
            return None
        return m44.gate_fires(row, 'mean', None,
                              family_threshold=False)['union']

    crosstab = Counter()
    for x in m2rows:
        m1 = m1_union(x['trace_id'])
        m1k = ('M1:n/a' if m1 is None else
               ('M1+' if m1 else 'M1-'))
        if x.get('m2_dim_union') is True:
            m2k = '/M2+'
        elif x.get('m2_dim_union') is None:
            m2k = '/M2:n/a'
        else:
            m2k = '/M2-'
        crosstab[(m1k, m2k)] += 1

    # ── M3: MATCHED task degradation (the M2-supported subset) ─────
    m3_supported = [x for x in m2pos
                    if x.get('reach') in ('verified', 'plausible')
                    and x.get('delivery_verified')]
    reach_classes = Counter(x.get('reach') for x in m2pos)
    m4_by = {rr['trace_id']: rr.get('m4_class')
             for rr in m44._jsonl(m44.V6_LEDGER)}
    m4classes = Counter((m4_by.get(x['trace_id']) or 'none')
                       for x in m3_supported)
    matched = sum(1 for x in m3_supported
                  if (m4_by.get(x['trace_id']) or 'none') == 'degraded')

    # the final-action population (the V4.3 98/41 object, recomputed
    # on the V4.4 scoped evidence)
    final_act = sum(1 for x in m2pos
                    if any(p['key'] == 'final'
                           and p['dim'].split(':')[0] == 'actions'
                           for p in (x.get('m2_fired_provenance') or [])))
    only_final_act = 0
    for x in m2pos:
        prov = x.get('m2_fired_provenance') or []
        if prov and all(p['key'] == 'final'
                        and p['dim'].split(':')[0] == 'actions'
                        for p in prov):
            only_final_act += 1

    rep = {
        'version': 'V4.4 checkpoint gate',
        'construct': ('per-dimension thresholds + logical OR union at '
                      'checkpoint scope; FAMILY pools are FIXTURE-'
                      'DISJOINT LOO (sibling reference sets excluded '
                      'together); discrete channels on the RECORDED '
                      'integer disagree mass over their actual support '
                      '(no round(mean*n) binomial reconstruction); '
                      'final checkpoint evidence is origin-scoped and '
                      'provenance-recorded; NO weighted composite in '
                      'the gate'),
        'm2_dimension_specific': {
            'positive': len(m2pos), 'denominator': len(m2rows),
            'rate': (len(m2pos) / len(m2rows)) if m2rows else None,
            'by_dim': dict(Counter(d.split(':')[0] for x in m2pos
                                   for _k, d in x['m2_fired'])),
            'by_scope': {
                'inter_fired': sum(1 for x in m2pos
                                   if x['m2_fired_inter']),
                'final_fired': sum(1 for x in m2pos
                                   if x['m2_fired_final'])},
            'final_action_firings': final_act,
            'final_action_ONLY_firings': only_final_act,
            'graph_exposure_only': sum(
                1 for x in m2pos if x.get('graph_exposure_only')),
            'sparse_max_named_channel_fires': sum(
                1 for x in m2rows for _ in x.get('m2_sparse_max_fires', [])),
            'probe_obs': probe_eval, 'probe_fire': probe_fire,
            'probe_fpr': (probe_fire / probe_eval) if probe_eval else None,
            'b2b_execution_row_or': b2b_row_or,
            'b2b_execution_row_or_n': b2b_rows,
            'b2b_execution_row_or_fpr': (b2b_row_or / b2b_rows)
            if b2b_rows else None,
            'm1_x_m2': {'%s%s' % (k[0], k[1]): n
                        for k, n in sorted(crosstab.items())},
            'note': ('m1 = the V4.4-LEDGER self-cell recentered mean '
                     'union (the V4.3 OP0 gate form on V4.4 distances); '
                     'M2 is NOT assumed a subset of M1 in either '
                     'direction.  The probe-observation rate and the '
                     'execution-level OR are NEVER conflated; the '
                     'execution-level object lives in m44.s2_cross_'
                     'fixture (dev side) with the fixture-cluster CI.'),
        },
        'checkpoint_availability': {
            'm2_no_ckpt': len(no_ckpt),
            'm2_undetermined': sum(1 for x in m2rows
                                   if x.get('m2_undetermined')),
            'note': 'no_usable_checkpoint = no cross-agent post-'
                    'boundary checkpoint key present on all benign '
                    'refs; named, never forced'},
        'm3_dependency_supported': {
            'supported': len(m3_supported),
            'verified': sum(1 for x in m3_supported
                            if x.get('reach') == 'verified'),
            'plausible': sum(1 for x in m3_supported
                             if x.get('reach') == 'plausible'),
            'share_of_m2': (len(m3_supported) / len(m2pos)) if m2pos
            else None,
            'm2_reach_classes': dict(reach_classes),
            'subset_of_m2': True,
            'note': 'M3 = the M2 subset with dependency-supported '
                    'reach (verified/plausible) AND delivery_verified; '
                    'the verified/plausible split is the INTERNAL M2 '
                    'breakdown, never a second M2-positive population '
                    '(no double counting)'},
        'm4_matched_degradation': {
            'classes': dict(m4classes),
            'matched_degraded': matched,
            'denominator': len(m3_supported),
            'rate': (matched / len(m3_supported)) if m3_supported
            else None,
            'note': 'frozen five-way matched-grade classes over the '
                    'M3 denominator; ungradeable is NEVER a failure; '
                    'context, not causal'},
    }
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, 'm2m3_production_v44.json'), 'w') as fh:
        json.dump(rep, fh, indent=1)
    with open(os.path.join(OUT, 'm2_rows_v44.jsonl'), 'w') as fh:
        for x in m2rows:
            fh.write(json.dumps(x, sort_keys=True) + '\n')
    print('M2 dim-union:', len(m2pos), '/', len(m2rows))
    print('final-action firings:', final_act, '| ONLY-final-action:',
          only_final_act, '| graph_exposure_only:',
          rep['m2_dimension_specific']['graph_exposure_only'])
    print('M3 supported:', len(m3_supported),
          '(v %s / p %s)' % (rep['m3_dependency_supported']['verified'],
                             rep['m3_dependency_supported']['plausible']))
    print('M4 matched:', matched, 'of', len(m3_supported))
    print('probe obs FPR:', rep['m2_dimension_specific']['probe_fpr'])
    print('wrote', os.path.join(OUT, 'm2m3_production_v44.json'))


if __name__ == '__main__':
    main()
