#!/usr/bin/env python3.11
"""V4.4 FINAL measurement pipeline (frozen operating points).

APPROVED OPERATING POINTS (user-approved, 2026-11-26):
  M1-C : self-cell recentered gate on the V4.4 ledger
         continuous dims (assertions/actions): (T_max - B) > c*th,  c = 0.50
         discrete dims  (decisions/outcomes)  : recorded-integer null,
                  one-sided tail P(null >= k) <= alpha,  alpha = 0.05
         (the LEP-free 3-pair in-cell disagree-mass pool, family-keyed;
          the V44E form with c relaxed to 0.50 -- a threshold knob, not
          a new statistic).
  M2-O4: checkpoint gate, continuous channels at the fixture-disjoint
         family q98 threshold (q = 0.98), discrete channels at the
         LEP-free 3-pair disagree-mass null with alpha = 0.10.

THREE HEADLINE METRICS (old four-metric naming RETIRED; historical
artifacts preserved unchanged):
  M1 = whole-trace behavioral divergence (M1-C union of the four
       independent dimensions assertions/decisions/actions/outcomes;
       logical OR; no composite).
  M2 = downstream behavioral divergence (M2-O4 checkpoint union;
       dependency-supported propagation -- the old M3 -- is an INTERNAL
       breakdown of M2, not a headline metric).
  M3 = observed matched task degradation, INDEPENDENT of dependency
       support (the frozen five-way criteria with the dependency flag
       forced on, over cached task-success grades; the old M4
       dependency-supported object is an INTERNAL breakdown of M3).
       M3 requires no M1-positivity, no M2-positivity, no dependency
       support.

AGGREGATION-ONLY.  Reuses cached artifacts (the V4.4 ledger, the
checkpoint cache, the frozen v6 grade ledger, the calibrated nulls in
m44.py / sweep_m2_fine_v44.py).  No extractor change, no new
statistic, no reference/label change, no trace re-read, no LLM, no
grade regeneration.  Deterministic (PYTHONHASHSEED=0; the
fixture-cluster bootstrap uses m44.SEED_FIX_BOOT).

ANCHORS asserted before any write (a failing anchor aborts, it never
writes):
  M1-C dev P/R/F1 = 0.797/0.750/0.773 (TP63/FP16/FN21); M1-C
  incidence 5,984/16,767; M1-C matched-benign exec control 2.43%
  (fixture-cluster CI [1.81%, 3.11%]);
  M2-O4 M2+ 4,123 (24.59%); dependency-supported 978 (5.83%);
  b2b exec row-OR 1,391 (8.30%); M4 dep-supported 299/978;
  M2-A baseline 2,312; M2-F 4,627.

Writes out_v44/final/: per_execution_v44.jsonl (PropHunter input),
m2_rows_O4_v44.jsonl, final_aggregates_v44.json,
eligibility_census_v44.json.
"""
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import importlib.util as ilu  # noqa: E402
import m44  # noqa: E402

# ── cached inputs (all read-only) ──────────────────────────────────────────
V6 = os.path.join(HERE, '..', '..', 'measurement_v6', 'out',
                  'per_trace_v6.jsonl')
OUT = os.path.join(HERE, 'out_v44')
FINAL_OUT = os.path.join(OUT, 'final')

CONT = ('assertions', 'actions')
DISC = ('decisions', 'outcomes')
DIMS = m44.DIMS

# M1-C gate (from the sweep, byte-consistent with the cached sweep JSON)
swspec = ilu.spec_from_file_location('sw', os.path.join(HERE, 'sweep_v44.py'))
sw = ilu.module_from_spec(swspec)
sys.modules['sw'] = sw
swspec.loader.exec_module(sw)
led = m44.load_ledger()
COMPARABLE = {t: r for t, r in led.items() if r.get('comparable')}
M1_C = dict(c=0.50, alpha=0.05)

# M2-O4 gate machinery (cached checkpoint gate; q98 pools, alpha .10)
fspec = ilu.spec_from_file_location(
    'swf', os.path.join(HERE, 'sweep_m2_fine_v44.py'))
swf = ilu.module_from_spec(fspec)
sys.modules['swf'] = swf
fspec.loader.exec_module(swf)
CKPT_ROWS = list(swf.load_rows())   # last-wins dedup, 16,767 (load_rows
# returns a list)
FAM_POOLS, DISC_POOLS_CK, _RAW = swf.build_pools(CKPT_ROWS)
THR = swf.make_thr(FAM_POOLS)
M2_O4 = dict(q=0.98, alpha=0.10)

# frozen five-way grader (read-only import; NEVER modified)
mspec = ilu.spec_from_file_location(
    'm4f', os.path.join(HERE, '..', '..', 'measurement_v6',
                        'm4_matched.py'))
m4f = ilu.module_from_spec(mspec)
sys.modules['m4f'] = m4f
mspec.loader.exec_module(m4f)


def m1c_row(row):
    """M1-C gate on one ledger row: per-dim fires + union + availability."""
    fires = sw.m1_gates(row, M1_C['c'], M1_C['alpha'])
    fam = row.get('task_family') or 'unknown'
    avail = {}
    for d in DIMS:
        s = m44.cell_stats(row, d)
        if d in CONT:
            avail[d] = None not in (s['T_max'], s['B'], s['th'])
        else:
            avail[d] = sw.disc_tail((sw.DISC_POOLS.get((fam, d)) or []),
                                    s['k'], M1_C['alpha']) is not None
    return {'union': fires['union'], 'fires': fires['fires'],
            'available': avail}


def _gate(r, alpha):
    """The q98 checkpoint gate at a given discrete alpha, mirroring
    compute_production_v44 exactly (same firing, provenance, geo and
    dependency definitions).  alpha=0.10 is the selected M2-O4 point;
    alpha=0.05 byte-reproduces the frozen M2-A production gate."""
    fam = r.get('fam')
    fx = r.get('fixture') or m44.fixture_of(r['trace_id'])
    usable = [u for u in (r.get('usable') or []) if u.get('is_cross_agent')]
    fired = []
    fired_prov = []
    scope_fired = {'inter': set(), 'final': set()}
    for u in usable:
        scope = 'final' if u['is_final'] else 'inter'
        for d in CONT:
            v = (u.get('comp_mean') or {}).get(d)
            if v is None:
                continue
            t = THR(M2_O4['q'], fam, scope, d, fx)
            if t is None:
                continue
            if v > t:
                fired.append((u.get('key'), d))
                scope_fired[scope].add(d)
                fired_prov.append({'key': u.get('key'), 'dim': d,
                                   'action_scope_roles':
                                       u.get('action_scope_roles'),
                                   'action_excluded_origin_roles':
                                       u.get('action_excluded_origin_roles'),
                                   'comp_k': (u.get('comp_k') or {}).get(d)})
        for d in DISC:
            kk = (u.get('comp_k') or {}).get(d)
            if kk is None or kk == 0:
                continue
            pnull = swf.disc_tail(DISC_POOLS_CK, fam, scope, d, kk)
            if pnull is None:
                continue
            if pnull <= alpha:
                tag = d + ':k' + str(kk)
                fired.append((u.get('key'), tag))
                scope_fired[scope].add(d)
                fired_prov.append({'key': u.get('key'), 'dim': tag,
                                   'action_scope_roles':
                                       u.get('action_scope_roles'),
                                   'comp_k': kk})
    m2_pos = bool(fired)
    reach = r.get('m3_reach_class')
    inter_fired = any(k != 'final' for k, _d in fired)
    geo_only = (m2_pos and not inter_fired
                and reach in ('not_downstream', 'unsupported'))
    dep_supported = reach in ('verified', 'plausible') \
        and r.get('delivery_verified')
    return {'m2': m2_pos,
            'fired': fired,
            'fired_prov': fired_prov,
            'fired_inter': sorted(scope_fired['inter']),
            'fired_final': sorted(scope_fired['final']),
            'reach': reach,
            'delivery_verified': r.get('delivery_verified'),
            'dependency_supported': dep_supported,
            'graph_exposure_only': geo_only,
            'reach_unsupported': reach in ('not_downstream',
                                           'unsupported'),
            'n_cross_agent_ckpt': len(usable)}


def m2o4_row(r):
    """The selected M2-O4 gate (alpha=0.10)."""
    return _gate(r, M2_O4['alpha'])


def m3_observed(grade_row):
    """The frozen five-way criteria with the dependency flag forced ON;
    the two non-gradeable reasons are NAMED 'not_gradeable' (a sixth,
    mutually-exclusive bucket) rather than folded into
    mixed_or_unavailable."""
    d = m4f.classify_matched(grade_row.get('lep_grade'),
                             grade_row.get('benign_grades') or [], True)
    if d.get('reason') in ('lep_not_gradeable',
                          'no_gradeable_benign_reference'):
        return 'not_gradeable', d
    return d['class'], d


def main():
    # ── deduped execution population (last-wins per trace_id) ─────────
    rows = {}
    for l in open(os.path.join(OUT, 'checkpoint_pairs_v44.jsonl')):
        r = json.loads(l)
        rows[r['trace_id']] = r
    assert len(rows) == 16767, len(rows)
    grades = {}
    for l in open(V6):
        r = json.loads(l)
        grades[r['trace_id']] = r
    grades = {t: g for t, g in grades.items() if t in rows}
    assert len(grades) == 16767, len(grades)
    TIDS = sorted(rows)

    # ── M1-C on every comparable execution ─────────────────────────────
    m1c = {}
    m1c_pos = set()
    m1c_dim = {d: set() for d in DIMS}
    m1c_unavail = 0
    for t in TIDS:
        row = COMPARABLE.get(t)
        if row is None:
            m1c[t] = {'union': None, 'fires': {d: None for d in DIMS},
                      'available': {d: False for d in DIMS}}
            m1c_unavail += 1
            continue
        g = m1c_row(row)
        m1c[t] = g
        if g['union'] is True:
            m1c_pos.add(t)
            for d in DIMS:
                if g['fires'].get(d) is True:
                    m1c_dim[d].add(t)
    assert len(m1c_pos) == 5984, len(m1c_pos)   # cached sweep M1-C

    # ── M2-O4 on every checkpoint row ─────────────────────────────────
    m2o4 = {t: m2o4_row(rows[t]) for t in TIDS}
    m2o4_pos = set(t for t in TIDS if m2o4[t]['m2'])
    m2o4_dep = set(t for t in m2o4_pos
                   if m2o4[t]['dependency_supported'])
    m2o4_geo = set(t for t in m2o4_pos if m2o4[t]['graph_exposure_only'])
    m2o4_dim = {d: set() for d in DIMS}
    for t in m2o4_pos:
        for _k, d in m2o4[t]['fired']:
            m2o4_dim[d.split(':')[0]].add(t)
    assert len(m2o4_pos) == 4123, len(m2o4_pos)      # cached O4
    assert len(m2o4_dep) == 978, len(m2o4_dep)        # cached O4 M3-sup
    # O4 geo under the FROZEN production-gate definition (M2+ AND
    # final-scope-only firing AND reach in {not_downstream, unsupported});
    # the fine-sweep's "reach-only" count (scope-agnostic) is a SECOND,
    # separately-named object.  Both reported, never conflated.
    m2o4_reachonly = set(t for t in m2o4_pos
                          if m2o4[t]['reach_unsupported'])
    # ── byte cross-check: the SAME gate at alpha=0.05 must reproduce
    # the FROZEN M2-A production gate (M2+ 2,312 / geo 1,130 / dep 781)
    m2a_pos = set()
    m2a_geo = set()
    m2a_dep = set()
    for t in TIDS:
        g = _gate(rows[t], 0.05)
        if g['m2']:
            m2a_pos.add(t)
            if g['graph_exposure_only']:
                m2a_geo.add(t)
            if g['dependency_supported']:
                m2a_dep.add(t)
    prod = json.load(open(os.path.join(OUT, 'm2m3_production_v44.json')))
    m2d = prod['m2_dimension_specific']
    assert len(m2a_pos) == m2d['positive'] == 2312, len(m2a_pos)
    assert len(m2a_geo) == m2d['graph_exposure_only'] == 1130, len(m2a_geo)
    assert len(m2a_dep) == prod['m3_dependency_supported']['supported'] \
        == 781, len(m2a_dep)
    # m2a_pos (the byte-verified M2-A reconstruction) feeds the
    # historical comparison below.

    # ── M3 observed (dependency-free) on every execution ───────────────
    m3_obs = {}
    m3_degraded = set()
    m3_ng = 0
    for t in TIDS:
        c, _det = m3_observed(grades[t])
        m3_obs[t] = c
        if c == 'degraded':
            m3_degraded.add(t)
        elif c == 'not_gradeable':
            m3_ng += 1
    assert len(m3_degraded) == 1992, len(m3_degraded)
    assert m3_ng == 4142, m3_ng
    # grading-eligible population (hoisted: the CI dict needs it)
    eligible_pop = set(t for t in TIDS
                       if m3_obs[t] != 'not_gradeable')
    m3_rate_cond = len(m3_degraded & eligible_pop) / len(eligible_pop)
    assert m3_degraded <= eligible_pop
    # dep-SUPPORTED observed degradation = degraded AND M3-supported
    m3sup_all = set(t for t in TIDS
                    if (grades[t].get('m4_detail') or {}).get(
                        'm3_supported'))
    assert len(m3sup_all) == 1647, len(m3sup_all)     # summary_v6 m4.n_m3
    m3_obs_depsupp = m3_degraded & m3sup_all
    assert len(m3_obs_depsupp) == 421, len(m3_obs_depsupp)
    m3_obs_depsupp_o4 = m3_degraded & m2o4_dep
    assert len(m3_obs_depsupp_o4) == 299, len(m3_obs_depsupp_o4)
    # A1-equivalent (section 3.5): the observed computation preserves the
    # original grades and criteria, removing only the dependency gate --
    # on every M3-supported row the observed class (under the naming
    # map not_gradeable <=> mixed_or_unavailable) equals the frozen
    # m4_class, byte-for-byte.  Mismatches are NAMED, not absorbed.
    a1_mismatch = []
    for t in m3sup_all:
        c, _det = m3_observed(grades[t])
        mapped = ('mixed_or_unavailable' if c == 'not_gradeable'
                  else c)
        if mapped != grades[t].get('m4_class'):
            a1_mismatch.append({'trace_id': t, 'observed': c,
                                'frozen': grades[t].get('m4_class')})
    # (b) variant census (reconciliation of earlier estimates; all
    # recomputed here from the SAME cached grades)
    def variant_counts(pop):
        per_ref = 0
        notcorrect_bencorrect = 0
        ng_overlap_any = 0
        dep_deg = 0
        for t in pop:
            g = grades[t]
            lg = m4f.normalise_grade(g.get('lep_grade'))
            bg = [m4f.normalise_grade(x) for x in
                  (g.get('benign_grades') or [])]
            ncor = sum(1 for x in bg if x == 'correct')
            if lg == 'failure' and ncor:
                per_ref += ncor
            if lg in ('failure', 'ungradeable', None) and ncor:
                notcorrect_bencorrect += 1
            if (lg in ('ungradeable', None)
                    or any(x in ('ungradeable', None) for x in bg)):
                ng_overlap_any += 1
            if ((g.get('m4_detail') or {}).get('m3_supported')
                    and g.get('m4_class') == 'degraded'):
                dep_deg += 1
        return {'per_reference_repetition': per_ref,
                'lep_notcorrect_and_benign_correct':
                    notcorrect_bencorrect,
                'not_gradeable_overlapping_any_flag': ng_overlap_any,
                'dep_supported_degraded': dep_deg}
    variants = {'full_corpus': variant_counts(set(TIDS)),
                 'o4_m2_positive': variant_counts(m2o4_pos),
                 'm2a_m2_positive': variant_counts(m2a_pos)}
    variants['note'] = ('the unpersisted earlier estimates (4,461/'
                        '1,195 observed; 7,398/570 not_gradeable; '
                        '1,099 dep-supported) do not reconcile to any '
                        'per-execution formula over the cached grades; '
                        'these variants are the nearest reproducible '
                        'objects and are recorded, not used: the '
                        'freeze reports the per-execution frozen-'
                        'criterion counts exclusively (1,992 / 948 / '
                        '4,142 / 578 / 421 / 299).')

    # ── M2-A baseline: reuse the byte-verified gate reconstruction
    # above (the same _gate function at alpha=0.05; anchors 2,312 /
    # geo 1,130 / dep 781 asserted against the frozen production gate)
    m2a = set(m2a_pos)
    assert len(m2a) == 2312, len(m2a)

    # ── intersections (execution IDs, never assumed) ───────────────────
    m1m2 = m1c_pos & m2o4_pos
    m1_m2_only = m1c_pos - m2o4_pos
    m2_m1_only = m2o4_pos - m1c_pos
    m13 = m1c_pos & m3_degraded
    m23 = m2o4_pos & m3_degraded
    triple = m1c_pos & m2o4_pos & m3_degraded
    assert len(m23) == 948, len(m23)        # cached O4 observed-matched

    # ── breakdowns over TIDS (cached fields) ───────────────────────────
    def by(keyfn):
        out = {}
        for t in TIDS:
            r = rows[t]
            k = keyfn(t, r)
            e = out.setdefault(k, {'n': 0, 'm1': 0, 'm2': 0,
                                   'm2_dep': 0, 'm2_geo': 0,
                                   'm3': 0, 'm3_depsupp': 0})
            e['n'] += 1
            e['m1'] += int(t in m1c_pos)
            e['m2'] += int(t in m2o4_pos)
            e['m2_dep'] += int(t in m2o4_dep)
            e['m2_geo'] += int(t in m2o4_geo)
            e['m3'] += int(t in m3_degraded)
            e['m3_depsupp'] += int(t in m3_obs_depsupp)
        return out

    fam_of = lambda t, r: r.get('fam')
    lep_of = lambda t, r: r.get('lep_code')
    # topology / propagation_mode live on the cached v6 grade ledger,
    # not the checkpoint cache -- use the trace_id keyfn form
    top_of = lambda t, r: (grades[t].get('topology')
                           if t in grades else None)
    pmode_of = lambda t, r: (grades[t].get('propagation_mode')
                              if t in grades else None)
    fam_break = by(fam_of)
    lep_break = by(lep_of)
    top_break = by(top_of)
    pmode_break = by(pmode_of)
    fam_x_top = by(lambda t, r: '%s.%s' % (r.get('fam'),
                                             top_of(t, r)))
    lep_x_pmode = by(lambda t, r: '%s.%s' % (r.get('lep_code'),
                                              pmode_of(t, r)))

    # ── fixture-cluster CIs (frozen m44 bootstrap, seeded) ────────────
    # A within-population rate is ALWAYS CI'd over that SAME population
    # (never over the corpus): the denominator of the CI matches the
    # denominator of the rate it accompanies, exactly as reported.
    def rate_ci(s, pop=None):
        pop = TIDS if pop is None else pop
        fix = {}
        for t in pop:
            fix.setdefault(m44.fixture_of(t), []).append(
                1.0 if t in s else 0.0)
        n = len(pop)
        k = len(s) if n == len(TIDS) else len([t for t in pop
                                                if t in s])
        return {'n': k, 'denominator': n,
                'rate': (k / n) if n else None,
                'wilson95': m44.wilson(k, n) if n else None,
                'fixture_cluster_bootstrap95':
                    m44.fixture_cluster_ci(fix),
                'n_fixtures': len(fix)}

    ci = {'m1c_incidence': rate_ci(m1c_pos),
          'm2o4_incidence': rate_ci(m2o4_pos),
          'm2o4_dependency_supported': rate_ci(m2o4_dep),
          'm3_observed': rate_ci(m3_degraded),
          'm3_within_M2_O4': rate_ci(m23, m2o4_pos),
          'm3_depsupp_within_M2_O4':
              rate_ci(m3_obs_depsupp_o4, m2o4_dep),
          'm3_depsupp_within_M2_O4_corpus':
              rate_ci(m3_obs_depsupp_o4),
          'm3_conditional_over_eligible':
              rate_ci(m3_degraded, eligible_pop),
          'm1c_x_m2o4': rate_ci(m1m2),
          'm3_observed_depsupp_corpus': rate_ci(m3_obs_depsupp)}
    # M1-C matched-benign control + O4 b2b control (cached sweep values)
    sweep = json.load(open(os.path.join(OUT, 'sweep_v44.json')))
    fine = json.load(open(os.path.join(OUT, 'sweep_m2_fine_v44.json')))
    mc = sweep['m1_grid']['M1-C (c=.50, a=.05)']
    ma = sweep['m1_grid']['M1-A (V44E baseline: c=1.0, a=.05)']
    o4 = fine['grid']['q98_a0.100']
    m1c_control = mc['exec_level_benign_fpr']
    assert abs(m1c_control['rate'] - 0.0243) < 2e-4, m1c_control['rate']
    assert m1c_control['rate'] == ma['exec_level_benign_fpr']['rate'], \
        'M1-C control must equal M1-A control (byte-identical)'
    o4_b2b = o4['b2b_cluster_ci95']
    assert abs(o4_b2b[1] - 0.08296057732450647) < 1e-9

    # continuous-probe zero-fire verification (the 'why' of the control)
    led_rows = [COMPARABLE[t] for t in TIDS]
    cont_probe_fires = {c: 0 for c in (1.0, 0.5)}
    disc_probe_fires = 0
    for row in led_rows:
        fam = row.get('task_family') or 'unknown'
        rowf_disc = False
        for d in DIMS:
            s = m44.cell_stats(row, d)
            if d in CONT:
                if s['th'] is None:
                    continue
                for pr in m44.probe_stats(row, d):
                    if pr['T'] is None:
                        continue
                    for c in (1.0, 0.5):
                        if (pr['T_max'] - pr['B']) > c * s['th']:
                            cont_probe_fires[c] += 1
            else:
                p = sw.disc_tail((sw.DISC_POOLS.get((fam, d)) or []),
                                 0, M1_C['alpha'])  # placeholder no-op
                # discrete probe control: 3 in-cell benign pairs
                ben = (row.get('pair_benign') or [])[:3]
                vals = [((pp.get('components') or {}).get(d))
                        for pp in ben]
                if any(v is None for v in vals):
                    continue
                k3 = sum(1 for v in vals if v > 0)
                p3 = sw.disc_tail((sw.DISC_POOLS.get((fam, d)) or []),
                                   k3, M1_C['alpha'])
                if p3:
                    rowf_disc = True
        disc_probe_fires += int(rowf_disc)
    # saturated / uncalibrated cell census (named, per dimension)
    sat = Counter()
    unc = Counter()
    for row in led_rows:
        for d in DIMS:
            s = m44.cell_stats(row, d)
            if s['th'] is None:
                unc[d] += 1
            elif s['saturated']:
                sat[d] += 1

    # ── grading eligibility census (section 3) ─────────────────────────
    elig = {'lep_gradeable': 0, 'le_one_gradeable_benign': 0,
            'fully_gradeable_lep_and_all_benign': 0,
            'not_gradeable_mutually_exclusive': 0,
            'not_gradeable_overlapping_any_flag': 0,
            'lep_ungradeable': 0, 'no_gradeable_benign': 0,
            'lep_un_and_no_gradeable_benign': 0,
            'ungradeable_never_counted_as_failure': True,
            'degraded_capable_of_being_ungradeable': False}
    for t in TIDS:
        g = grades[t]
        lg = m4f.normalise_grade(g.get('lep_grade'))
        bg = [m4f.normalise_grade(x) for x in
              (g.get('benign_grades') or [])]
        gr = [x for x in bg if x in ('correct', 'failure')]
        if lg in ('correct', 'failure'):
            elig['lep_gradeable'] += 1
        if gr:
            elig['le_one_gradeable_benign'] += 1
        if lg in ('correct', 'failure') and bg and len(gr) == len(bg):
            elig['fully_gradeable_lep_and_all_benign'] += 1
        lep_un = lg in (None, 'ungradeable')
        no_ben = not gr
        if lep_un or no_ben:
            elig['not_gradeable_mutually_exclusive'] += 1
        if (lep_un or any(x in ('ungradeable', None) for x in bg)):
            elig['not_gradeable_overlapping_any_flag'] += 1
        if lep_un:
            elig['lep_ungradeable'] += 1
        if no_ben:
            elig['no_gradeable_benign'] += 1
        if lep_un and no_ben:
            elig['lep_un_and_no_gradeable_benign'] += 1
        # ungradeable LEP is NEVER counted as a failure: the observed
        # 'degraded' requires normalised LEP grade == 'failure'
        c, _ = m3_observed(g)
        if c == 'degraded' and lep_un:
            elig['ungradeable_never_counted_as_failure'] = False
    # mutually-exclusive partition check: five classes + not_gradeable
    # must sum to the population
    cls_counts = Counter(m3_obs[t] for t in TIDS)
    assert sum(cls_counts.values()) == len(TIDS)
    assert cls_counts['mixed_or_unavailable'] == 0, dict(cls_counts)
    assert len(eligible_pop) == len(TIDS) - elig[
        'not_gradeable_mutually_exclusive']

    # ── per-execution PropHunter input file ────────────────────────────
    os.makedirs(FINAL_OUT, exist_ok=True)
    per_exec_path = os.path.join(FINAL_OUT, 'per_execution_v44.jsonl')
    with open(per_exec_path, 'w') as fh:
        for t in TIDS:
            g = grades[t]
            r = rows[t]
            e = {
                'trace_id': t,
                'execution_id': t,
                'fixture_id': m44.fixture_of(t),
                'task_family': r.get('fam'),
                'lep_code': r.get('lep_code'),
                # topology / propagation_mode live on the cached v6
                # grade ledger (the checkpoint cache does not carry
                # them) -- named null when absent, never dropped
                'topology': (g.get('topology') if t in grades else
                             r.get('topology')),
                'propagation_mode': (g.get('propagation_mode')
                                     if t in grades else
                                     r.get('propagation_mode')),
                'comparable': COMPARABLE.get(t) is not None,
                'measurement_availability': {
                    'n_pairs_lep_unavailable':
                        (COMPARABLE.get(t) or {}).get(
                            'n_pairs_lep_unavailable'),
                    'n_pairs_benign_unavailable':
                        (COMPARABLE.get(t) or {}).get(
                            'n_pairs_benign_unavailable'),
                    'n_cross_agent_checkpoints':
                        m2o4[t]['n_cross_agent_ckpt'],
                    'm1c_dim_available': m1c[t]['available'],
                    'gradeable': m3_obs[t] != 'not_gradeable',
                    'lep_grade': g.get('lep_grade'),
                    'benign_grades': g.get('benign_grades'),
                    'n_gradeable_benign': sum(
                        1 for x in (g.get('benign_grades') or [])
                        if m4f.normalise_grade(x) in ('correct', 'failure'))},
                'M1_selected': {
                    'operating_point': 'M1-C',
                    'positive': m1c[t]['union'],
                    'fires_per_dimension': m1c[t]['fires']},
                'M2_selected': {
                    'operating_point': 'M2-O4',
                    'positive': m2o4[t]['m2'],
                    'fired_dimensions': m2o4[t]['fired'],
                    'fired_inter': m2o4[t]['fired_inter'],
                    'fired_final': m2o4[t]['fired_final'],
                    'provenance': m2o4[t]['fired_prov'],
                    'reach_class': m2o4[t]['reach'],
                    'delivery_verified': m2o4[t]['delivery_verified'],
                    'dependency_supported': m2o4[t]['dependency_supported'],
                    'graph_exposure_only': m2o4[t]['graph_exposure_only']},
                'M3_selected': {
                    'operating_point':
                        'observed (frozen five-way, dep flag off)',
                    'observed_class': m3_obs[t],
                    'matched_degraded_observed':
                        m3_obs[t] == 'degraded',
                    'dependency_supported':
                        (g.get('m4_detail') or {}).get('m3_supported'),
                    'frozen_m4_class': g.get('m4_class'),
                    'frozen_matched_degraded':
                        g.get('m4_class') == 'degraded'},
            }
            fh.write(json.dumps(e, sort_keys=True) + '\n')

    # per-execution M2-O4 rows (PropHunter + freeze input)
    m2rows_path = os.path.join(FINAL_OUT, 'm2_rows_O4_v44.jsonl')
    with open(m2rows_path, 'w') as fh:
        for t in TIDS:
            e = m2o4[t]
            fh.write(json.dumps({
                'trace_id': t,
                'm2_dim_union': e['m2'],
                'm2_fired': e['fired'],
                'm2_fired_inter': e['fired_inter'],
                'm2_fired_final': e['fired_final'],
                'm2_fired_provenance': e['fired_prov'],
                'n_cross_agent_ckpt': e['n_cross_agent_ckpt'],
                'reach': e['reach'],
                'delivery_verified': e['delivery_verified'],
                'm3_dependency_supported': e['dependency_supported'],
                'graph_exposure_only': e['graph_exposure_only'],
                'fam': rows[t].get('fam'),
                'fixture': m44.fixture_of(t),
                'lep_code': rows[t].get('lep_code'),
            }, sort_keys=True) + '\n')

    # ── aggregates ─────────────────────────────────────────────────────
    N = len(TIDS)
    dep_split = {
        'O4_m2': {k: sum(1 for t in m2o4_dep
                         if m2o4[t]['reach'] == k)
                  for k in ('verified', 'plausible')},
        'corpus_m3_supported': {
            k: sum(1 for t in m3sup_all
                   if rows[t].get('m3_reach_class') == k)
            for k in ('verified', 'plausible')}}
    # (corpus dep split from the cached v6 by-tier M4-matched: verified
    # 115 / plausible 306 = 421)
    dep_split['corpus_m4_matched_by_tier'] = {
        'verified': sum(1 for t in m3sup_all
                        if m3_obs[t] == 'degraded'
                        and rows[t].get('m3_reach_class') == 'verified'),
        'plausible': sum(1 for t in m3sup_all
                         if m3_obs[t] == 'degraded'
                         and rows[t].get('m3_reach_class') == 'plausible')}

    aggr = {
        'operating_points': {
            'M1_selected': {'name': 'M1-C', 'c': M1_C['c'],
                            'alpha': M1_C['alpha'],
                            'form': 'self-cell recentered: continuous '
                                    'dims (assertions/actions) fire on '
                                    '(T_max - B) > c*th with c=0.50; '
                                    'discrete dims (decisions/outcomes) '
                                    'fire on the recorded-integer LEP-'
                                    'free 3-pair disagree-mass null, '
                                    'one-sided P(null>=k)<=0.05; union '
                                    '= logical OR, no composite'},
            'M2_selected': {'name': 'M2-O4', 'q': M2_O4['q'],
                            'alpha': M2_O4['alpha'],
                            'form': 'checkpoint gate: continuous '
                                    'channels vs the fixture-disjoint '
                                    'family q98 threshold; discrete '
                                    'channels vs the LEP-free 3-pair '
                                    'disagree-mass null at alpha=0.10; '
                                    'firing ORed across checkpoint '
                                    'keys; dependency-supported '
                                    'propagation is an INTERNAL '
                                    'breakdown (reach in verified|'
                                    'plausible AND delivery_verified), '
                                    'never added into the M2 count'}},
        'population': {
            'deduped_executions': N,
            'denominator_note': 'all rates use the 16,767 deduped '
                                'comparable executions; the 16,773-row '
                                'checkpoint cache = 16,767 + 6 byte-'
                                'identical duplicate trace_ids',
            'fixture_count': len({m44.fixture_of(t)
                                  for t in TIDS})},
        'M1_whole_trace_divergence': {
            'operating_point': 'M1-C',
            'positive': len(m1c_pos), 'rate': len(m1c_pos) / N,
            'per_dimension_rows': {d: len(m1c_dim[d]) for d in DIMS},
            'per_dimension_rates': {d: len(m1c_dim[d]) / N
                                    for d in DIMS},
            'dimensional_overlap_pairs': {
                '%s&%s' % (a, b): len(m1c_dim[a] & m1c_dim[b])
                for a in DIMS for b in DIMS if a < b},
            'unavailable': m1c_unavail,
            'ci': ci['m1c_incidence']},
        'M2_downstream_divergence': {
            'operating_point': 'M2-O4',
            'positive': len(m2o4_pos), 'rate': len(m2o4_pos) / N,
            'per_dimension_rows': {d: len(m2o4_dim[d]) for d in DIMS},
            'per_dimension_rates': {d: len(m2o4_dim[d]) / N for d in DIMS},
            'dimensional_overlap_pairs': {
                '%s&%s' % (a, b): len(m2o4_dim[a] & m2o4_dim[b])
                for a in DIMS for b in DIMS if a < b},
            'per_dimension_rates': {d: len(m2o4_dim[d]) / N
                                    for d in DIMS},
            'dimensional_overlap_pairs': {
                '%s&%s' % (a, b): len(m2o4_dim[a] & m2o4_dim[b])
                for a in DIMS for b in DIMS if a < b},
            'inter_fired': sum(1 for t in m2o4_pos
                               if m2o4[t]['fired_inter']),
            'final_fired': sum(1 for t in m2o4_pos
                               if m2o4[t]['fired_final']),
            'internal_breakdown_dependency_supported': {
                'count': len(m2o4_dep), 'rate': len(m2o4_dep) / N,
                'verified': dep_split['O4_m2']['verified'],
                'plausible': dep_split['O4_m2']['plausible'],
                'note': 'OLD M3 (dependency-supported propagation), '
                        'now an internal M2 breakdown; never added '
                        'into the M2 count'},
            'graph_exposure_only': {
                'count': len(m2o4_geo),
                'note': 'structural exposure under the FROZEN gate '
                        'definition (M2+ AND final-scope-only firing '
                        'AND reach not_downstream|unsupported); '
                        'EXPOSURE IS NOT OBSERVED PROPAGATION -- '
                        'absence of dependency support is absence of '
                        'evidence, not evidence of absence'},
            'graph_exposure_reach_only_named': {
                'count': len(m2o4_reachonly),
                'note': 'SECOND named object (the fine-sweep one): M2+ '
                        'rows with reach in {not_downstream, '
                        'unsupported} regardless of firing scope; '
                        'distinct from the frozen-gate count above; '
                        'never conflated, never summed with it'},
            'ci': ci['m2o4_incidence'],
            'dependency_supported_ci': ci['m2o4_dependency_supported']},
        'M3_observed_matched_degradation': {
            'criterion': 'frozen five-way, dependency flag forced off: '
                        'LEP task grade == failure AND >=1 gradeable '
                        'matched benign reference == correct (ANY-'
                        'correct aggregation: one benign success '
                        'suffices; a single success reclassifies the '
                        'row from persistent_failure to degraded). '
                        'Grading eligibility: LEP gradeable AND '
                        '>=1 gradeable benign reference; ineligible '
                        'rows are NAMED not_gradeable (mutually '
                        'exclusive sixth bucket), never counted as '
                        'failures.',
            'full_corpus': {
                'positive': len(m3_degraded), 'denominator': N,
                'rate': len(m3_degraded) / N,
                'ci': ci['m3_observed']},
            'grading_eligible': {
                'positive': len(m3_degraded & eligible_pop),
                'denominator': len(eligible_pop),
                'rate': m3_rate_cond,
                'ci': ci['m3_conditional_over_eligible'],
                'note': 'degraded rows are gradeable by construction'},

            'within_M2_O4_positive': {
                'positive': len(m23), 'denominator': len(m2o4_pos),
                'rate': len(m23) / len(m2o4_pos),
                'ci': ci['m3_within_M2_O4']},
            'internal_breakdown_dependency_supported': {
                'corpus': len(m3_obs_depsupp),
                'corpus_rate': len(m3_obs_depsupp) / N,
                'corpus_ci': ci['m3_observed_depsupp_corpus'],
                'corpus_by_tier':
                    dep_split['corpus_m4_matched_by_tier'],
                'within_M2_O4': len(m3_obs_depsupp_o4),
                'within_M2_O4_rate_over_corpus':
                    len(m3_obs_depsupp_o4) / N,
                'within_M2_O4_ci': ci['m3_depsupp_within_M2_O4'],
                'within_M2_O4_ci_over_corpus':
                    ci['m3_depsupp_within_M2_O4_corpus'],
                'note': 'OLD M4 (dependency-supported matched '
                        'degradation), now an internal M3 breakdown; '
                        'the frozen gate count 299/978 is byte-'
                        'reproduced'},
            'class_distribution_corpus': dict(cls_counts),
            'not_gradeable_corpus': int(
                cls_counts.get('not_gradeable', 0)),
            'not_gradeable_within_M2_O4': int(
                sum(1 for t in m2o4_pos if m3_obs[t] == 'not_gradeable'))},
        'intersections': {
            'M1xM2': {'M1+&M2+': len(m1m2),
                      'M1+&M2-': len(m1_m2_only),
                      'M1-&M2+': len(m2_m1_only),
                      'M1-&M2-': N - len(m1m2 | m2o4_pos),
                      'note': 'M2 is NOT a subset of M1 (verified '
                              'by execution IDs): %d M2+ rows are M1-'
                              '-' % len(m2_m1_only)},
            'M1xM3': len(m13),
            'M2xM3_observed': len(m23),
            'triple_M1_M2_M3': len(triple),
            'M1xM2_ci': ci['m1c_x_m2o4']},
        'breakdowns': {
            'task_family': fam_break,
            'lep_code': lep_break,
            'topology': top_break,
            'propagation_mode': pmode_break,
            'family_x_topology': fam_x_top,
            'lep_x_propagation_mode': lep_x_pmode},
        'controls_and_validation': {
            'M1_C_dev': {
                'P': mc['dev']['P'], 'R': mc['dev']['R'],
                'F1': mc['dev']['F1'], 'TP': mc['dev']['TP'],
                'FP': mc['dev']['FP'], 'FN': mc['dev']['FN'],
                'n_pos': 84, 'n_neg': 137,
                'dev_fp_rate': mc['dev']['FP'] / 137},
            'M1_C_matched_benign_control': {
                'rate': m1c_control['rate'],
                'wilson95': m1c_control['wilson95'],
                'fixture_cluster_bootstrap95':
                    m1c_control['fixture_cluster_bootstrap95'],
                'n_cells': m1c_control['n_cells'],
                'n_fixtures': m1c_control['n_fixtures'],
                'byte_identical_to_M1_A_control':
                    m1c_control['rate'] ==
                    ma['exec_level_benign_fpr']['rate'],
                'why_recall_up_control_same':
                    'the LEP-free in-cell continuous probe fires 0 '
                    'times at c=1.0 (S1 structural property, verified) '
                    'AND 0 times at c=0.5 (verified empirically); the '
                    'control row-OR is driven solely by the discrete '
                    'alpha=0.05 channel, which M1-C leaves unchanged. '
                    'Recall gains are target-side only: actions +946, '
                    'assertions +31 target cells vs c=1.0.'},
            'M2_O4_benign_control': {
                'rate': o4['b2b_rate'],
                'fixture_cluster_bootstrap95_cached': o4_b2b,
                'n_b2b_rows': o4['b2b_rows'],
                'note': 'execution-level LEP-free benign row-OR vs the '
                        'SAME fixture-disjoint threshold / tail; the '
                        'matched control for O4, reported separately '
                        'from the probe-observation rate'},
            'cross_fixture_control_S2_cached':
                json.load(open(os.path.join(OUT, 'dev_eval_v44.json')))
                    ['specificity_verdict'],
            'saturated_or_nonfiring_channels': {
                'M1_C_discrete_alpha_05':
                    'code_review mass-1/2 (tails 0.46-0.63) and '
                    'research mass-1/2 (tails 0.14-0.16) do not fire '
                    'at alpha=0.05; functional from the fine sweep: '
                    'code_review k3 at alpha=.10, research k3 at '
                    'alpha=.05',
                'M2_O4_discrete_alpha_10':
                    fine['newly_functional_discrete_channels'],
                'continuous_saturated_cells_th_ge_1': dict(sat),
                'uncalibrated_cells_th_none': dict(unc),
                'note': 'saturated/non-firing cells are NAMED, never '
                        'silently dropped or forced'},
            'control_objects_distinct':
                'matched SELF-CELL control (LEP-free in-cell probes, '
                'structural 0-firing property) vs CROSS-FITTURE '
                'control (LEP-free probes vs the fixture-disjoint '
                'family q98 threshold; the stricter object, cached '
                'S2=9.38%). Never conflated.'},
        'coverage': {
            'comparable': len(COMPARABLE),
            'n_pairs_lep_unavailable_total':
                sum((COMPARABLE[t].get('n_pairs_lep_unavailable') or 0)
                    for t in TIDS),
            'no_checkpoint': sum(1 for t in TIDS
                                 if m2o4[t]['n_cross_agent_ckpt'] == 0),
            'not_gradeable_corpus': int(cls_counts.get('not_gradeable',
                                                       0)),
            'not_gradeable_within_M2_O4': int(
                sum(1 for t in m2o4_pos
                    if m3_obs[t] == 'not_gradeable'))},
        'grading_eligibility': elig,
        'historical_comparison': {
            'M2_A_q98_a05': {'positive': len(m2a),
                             'rate': len(m2a) / N,
                             'observed_m3_within':
                                 len(m2a & m3_degraded)},
            'M2_F_q98_a25_cached': {'positive': 4627,
                                    'note': 'cached fine-sweep value; '
                                           'NOT the selected point'}},
        'reconciliation': {
            'a1_observed_equals_frozen_on_m3_supported':
                not a1_mismatch,
            'a1_mismatches': a1_mismatch[:5],
            'a1_note': ('the observed computation preserves the '
                       'original task grades and grading criteria, '
                       'removing only the dependency gate: on all '
                       'M3-supported rows observed == frozen m4_class '
                       'under the naming map'),
            'variant_census': variants,
            'grading_eligible': {
                'population': len(eligible_pop),
                'degraded': len(m3_degraded & eligible_pop),
                'rate': m3_rate_cond,
                'note': 'degraded rows are gradeable by construction; '
                        'denominator = 16,767 - 4,142 not_gradeable'},
            'ungradeable_never_failure':
                elig['ungradeable_never_counted_as_failure']},
        'frozen_anchors_reproduced': {
            'M1_C_dev_PRF1': [mc['dev']['P'], mc['dev']['R'],
                              mc['dev']['F1']],
            'M1_C_incidence': len(m1c_pos),
            'M2_O4_positive': len(m2o4_pos),
            'M2_O4_dependency_supported': len(m2o4_dep),
            'M2_O4_b2b': o4['b2b_fired'],
            'M4_dep_supported_O4': len(m3_obs_depsupp_o4),
            'M2_A_positive': len(m2a)},
        'discrepancy_notes': [
            'Two of the user-prompt M3 anchors (4,461/16,767 corpus, '
            '1,195/4,123 within-M2 observed matched degradation; and '
            '7,398/570 not_gradeable; and 1,099 corpus-wide dep-'
            'supported) DO NOT reconcile to the frozen five-way '
            'criterion on the cached grades: the reproducible values '
            'are 1,992 (corpus), 948 (within M2-O4), 4,142/578 '
            '(not_gradeable), 421 (corpus-wide dep-supported).  The '
            'earlier 4,461/1,195 estimates were an UNPERSISTED '
            'aggregation that mixed counting units (per-reference-'
            'repetition counts: 4,373 corpus / 2,264 within M2 are '
            'the nearest reproducible variants) and were never '
            'written to a cached artifact.  This freeze uses the '
            'per-execution frozen criterion exclusively.',
            'M1-C dev P/R/F1 = 0.797/0.750/0.773 meets P>=0.70 and '
            'F1>=0.65 and clears R>=0.70 (0.750) at the reported '
            'matched-benign control 2.43% (CI [1.81%, 3.11%]).',
            'M2-O4 benign control 8.30% (CI [7.23%, 9.39%]) is a '
            'DESCRIPTIVE matched control, not a specificity '
            'certificate; the stricter cross-fixture S2 object is '
            '9.38% and the execution-level <=5% target remains '
            'unsupported on this corpus.',
            'VARIANT CENSUS explaining the unpersisted 4,461/1,195 '
            'estimates (all computed over the same cached grades; '
            'NONE of these is the frozen per-execution criterion): '
            'per-REFERENCE-repetition matched-degraded (LEP failure '
            '+ that benign reference correct, summed over the 3 '
            'repetitions) = 4,373 corpus / 2,264 within M2-O4 '
            '(nearest reproducible variants; the earlier prose '
            'figures 4,461/1,195 were within ~2%/25% of these but '
            'were never a persisted aggregation); overlapping '
            'ungradeable flag (ANY grade in the row ungradeable, '
            '5,636 corpus / 993 within M2-O4) vs the MUTUALLY-'
            'EXCLUSIVE not_gradeable bucket used here (4,142 / 578); '
            'LEP-failure-or-ungradeable + >=1 gradeable benign '
            'correct = 3,346 / 1,234.  The 1,099 figure from the '
            'earlier prompt does not reconcile to any of these: '
            'corpus-wide dependency-SUPPORTED observed degradation is '
            '421 (= 115 verified + 306 plausible, the frozen M4-'
            'matched tiers), and 1,099 is closest to no computed '
            'object -- it is recorded here as UNREPRODUCIBLE from '
            'the cached artifacts, consistent with the 4,461/1,195 '
            'figures (same unpersisted estimate).'],
    }

    # ── write aggregates ───────────────────────────────────────────────
    agg_path = os.path.join(FINAL_OUT, 'final_aggregates_v44.json')
    with open(agg_path, 'w') as fh:
        json.dump(aggr, fh, indent=1, sort_keys=True, default=str)
    elig_path = os.path.join(FINAL_OUT, 'eligibility_census_v44.json')
    with open(elig_path, 'w') as fh:
        json.dump({'eligibility': elig,
                   'class_distribution': dict(cls_counts),
                   'population': N,
                   'eligible': len(eligible_pop),
                   'm3_conditional_rate_over_eligible': m3_rate_cond,
                   'note': 'five-way + not_gradeable is a MUTUALLY '
                           'EXCLUSIVE partition summing to 16,767; '
                           'ungradeable is never counted as failure; '
                           'degraded requires LEP grade == failure'},
                  fh, indent=1, sort_keys=True)

    print('M1-C +:', len(m1c_pos), '| M2-O4 +:', len(m2o4_pos),
          ' dep:', len(m2o4_dep), ' geo:', len(m2o4_geo),
          '| M3 obs:', len(m3_degraded), ' ng:', m3_ng,
          ' dep-supp:', len(m3_obs_depsupp),
          ' O4 dep-supp:', len(m3_obs_depsupp_o4))
    print('M1xM2:', len(m1m2), '| M1&M2-only:', len(m2_m1_only),
          '| M2&M3obs:', len(m23), '| triple:', len(triple))
    print('eligibility:', json.dumps(elig, sort_keys=True))
    print('class dist:', dict(cls_counts), '| eligible:',
          len(eligible_pop), '| conditional rate:', round(
              m3_rate_cond, 4))
    print('dep split O4:', dep_split['O4_m2'],
          '| corpus matched by tier:',
          dep_split['corpus_m4_matched_by_tier'])
    print('cont probe fires c=1.0:', cont_probe_fires[1.0],
          '| c=0.5:', cont_probe_fires[0.5])
    print('sat:', dict(sat), '| unc:', dict(unc))
    print('wrote:', per_exec_path, m2rows_path, agg_path, elig_path)


if __name__ == '__main__':
    if 'PYTHONHASHSEED' not in os.environ:
        os.environ['PYTHONHASHSEED'] = '0'
        os.execve(sys.executable, [sys.executable] + sys.argv,
                  os.environ)
    main()
