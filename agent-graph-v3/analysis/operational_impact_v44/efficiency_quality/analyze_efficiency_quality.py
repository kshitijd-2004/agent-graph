#!/usr/bin/env python3.11
"""Efficiency/quality extension analysis (V4.4, standalone under efficiency_quality/).

Reads (read-only):
  ../../operational_impact_per_execution.jsonl   (enriched base table, 16,767)
  ../../benign_reference_operational.jsonl        (8,187 refs)
  out/generation_length_per_trace.jsonl           (this extension's extractor)
Writes (this directory only):
  out/efficiency_quality_per_execution.jsonl
  out/efficiency_quality_aggregates.json
  mechanism_examples.jsonl (top-level of efficiency_quality/)
Pre-registered estimands: PREREGISTRATION.md.  No base artifact is touched.
"""
from __future__ import annotations

import json
import math
import os
import sys
from collections import defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)                       # operational_impact_v44/
OUT = os.path.join(HERE, 'out')
sys.path.insert(0, HERE)
sys.path.insert(0, BASE)
import eq_stats as es
import boot as base_boot

SEED = es.SEED
N_BOOT = es.N_BOOT
TRUSTED = ('attempt_id', 'consistent')

# ── latency modes ────────────────────────────────────────────────────────────
# combined: trusted recorded runtime, else event-span fallback (base latency_s)
# trusted_runtime: recorded runtime_seconds ONLY on trusted refs/rows; missing otherwise
# event_span: trace event-timeline span ONLY
BINS7 = [
    ('faster_>25%', lambda rel: rel < -0.25),
    ('faster_10_25%', lambda rel: -0.25 <= rel < -0.10),
    ('faster_5_10%', lambda rel: -0.10 <= rel < -0.05),
    ('within_5%', lambda rel: -0.05 <= rel <= 0.05),
    ('slower_5_10%', lambda rel: 0.05 < rel <= 0.10),
    ('slower_10_25%', lambda rel: 0.10 < rel <= 0.25),
    ('slower_>25%', lambda rel: rel > 0.25),
]
# alternative bin boundaries (pre-specified sensitivity, §8.5)
ALT_BINS = [
    ('alt_inner5_10split', [
        ('faster_>15%', lambda r: r < -0.15), ('faster_5_15%', lambda r: -0.15 <= r < -0.05),
        ('within_5%', lambda r: -0.05 <= r <= 0.05),
        ('slower_5_15%', lambda r: 0.05 < r <= 0.15), ('slower_>15%', lambda r: r > 0.15)]),
    ('alt_inner3', [
        ('faster_>10%', lambda r: r < -0.10), ('faster_3_10%', lambda r: -0.10 <= r < -0.03),
        ('within_3%', lambda r: -0.03 <= r <= 0.03),
        ('slower_3_10%', lambda r: 0.03 < r <= 0.10), ('slower_>10%', lambda r: r > 0.10)]),
]


def _med(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    vals = sorted(vals)
    n = len(vals)
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def load_all():
    rows = [json.loads(l) for l in open(os.path.join(BASE, 'operational_impact_per_execution.jsonl')) if l.strip()]
    ben = {json.loads(l)['trace_id']: json.loads(l) for l in
           open(os.path.join(BASE, 'benign_reference_operational.jsonl')) if l.strip()}
    gen = {json.loads(l)['trace_id']: json.loads(l) for l in
           open(os.path.join(OUT, 'generation_length_per_trace.jsonl')) if l.strip()}
    return rows, ben, gen


def build_latency_modes(rows, ben):
    """Attach per-mode latency, benign median/mean, diff/rel/sign, validity."""
    for b in ben.values():
        tr = b.get('trace') or {}
        rec = b.get('record') or {}
        b['_rt'] = rec.get('runtime_seconds') if b.get('runtime_trust') in TRUSTED else None
        b['_span'] = tr.get('trace_span_s')
        b['_comb'] = b.get('latency_s')
    for r in rows:
        refs = [ben[x] for x in r.get('benign_refs', []) if x in ben]
        rt = [b['_rt'] for b in refs]
        sp = [b['_span'] for b in refs]
        cb = [b['_comb'] for b in refs]
        # perturbed-side values
        r['lat_trusted_runtime'] = ((r.get('record') or {}).get('runtime_seconds')
                                    if r.get('runtime_trust') in TRUSTED else None)
        r['lat_event_span'] = (r.get('trace') or {}).get('trace_span_s')
        r['lat_combined'] = r.get('latency_s')
        for mode, pv, med, mean in (
                ('trusted_runtime', r['lat_trusted_runtime'], _med(rt), _mean(rt)),
                ('event_span', r['lat_event_span'], _med(sp), _mean(sp)),
                ('combined', r['lat_combined'], _med(cb), _mean(cb))):
            r[f'ben_med_{mode}'] = med
            r[f'ben_mean_{mode}'] = mean
            r[f'diff_{mode}'] = (pv - med) if (pv is not None and med is not None) else None
            r[f'rel_{mode}'] = ((pv - med) / med) if (pv is not None and med not in (None, 0.0)) else None
            r[f'rel_mean_{mode}'] = ((pv - mean) / mean) if (pv is not None and mean not in (None, 0.0)) else None
            r[f'sign_{mode}'] = None
            if r[f'diff_{mode}'] is not None:
                r[f'sign_{mode}'] = 1 if r[f'diff_{mode}'] > 0 else (-1 if r[f'diff_{mode}'] < 0 else 0)
            r[f'sign_mean_{mode}'] = None
            if r[f'rel_mean_{mode}'] is not None:
                r[f'sign_mean_{mode}'] = 1 if r[f'rel_mean_{mode}'] > 0 else (-1 if r[f'rel_mean_{mode}'] < 0 else 0)
    return rows


def fixture_clusters(rows):
    d = defaultdict(list)
    for r in rows:
        d[r['fixture_id']].append(r)
    return d


def _out_rate(vals):
    """outcome class share among gradeable rows."""
    n = len(vals)
    if not n:
        return None
    c = defaultdict(int)
    for v in vals:
        c[v] += 1
    return {k: c[k] / n for k in ('degraded', 'improved', 'persistent_correct',
                                  'persistent_failure', 'not_gradeable')}


def _ts_rate(rows_in):
    tv = [r.get('record_task_success') for r in rows_in
          if r.get('runtime_trust') in TRUSTED and isinstance(r.get('record_task_success'), bool)]
    if not tv:
        return {'n_valid': 0, 'rate': None}
    return {'n_valid': len(tv), 'rate': sum(1.0 for x in tv if x) / len(tv)}


def _ci_rate(flags_by_fix):
    """fixture-cluster CI for a pooled 0/1 rate (vectorized base estimator)."""
    return base_boot.ci_pool_rate(flags_by_fix)


def _fix_counts(rows_in, pred, flagfn):
    fc = defaultdict(lambda: [0, 0])
    for r in rows_in:
        if pred(r):
            f = fc[r['fixture_id']]
            f[0] += 1
            f[1] += int(flagfn(r))
    return {k: v for k, v in fc.items() if v[0]}


# ── §3 primary contingency + joint estimators ───────────────────────────────
def faster_slower_compare(pop, rel_key='rel_combined', band=0.10, ci=True):
    faster = [r for r in pop if r.get(rel_key) is not None and r[rel_key] < -band]
    slower = [r for r in pop if r.get(rel_key) is not None and r[rel_key] > band]
    unchanged = [r for r in pop if r.get(rel_key) is not None and abs(r[rel_key]) <= band]

    def cell_stats(rs, tag):
        n = len(rs)
        deg = sum(1 for r in rs if r['M3_matched_degraded_observed'])
        ovr = _out_rate([r['M3_observed_class'] for r in rs]) or {}
        ts = _ts_rate(rs)
        return {
            'n': n, 'n_degraded': deg, 'degradation_rate': (deg / n) if n else None,
            'outcome_classes': {k: ovr.get(k) for k in ('degraded', 'improved',
                                                        'persistent_correct',
                                                        'persistent_failure', 'not_gradeable')},
            'task_success': ts,
            'persistent_failure_rate': ovr.get('persistent_failure'),
            'persistent_correct_rate': ovr.get('persistent_correct'),
            'improved_rate': ovr.get('improved'),
            'degradation_ci': _ci_rate({f: [r['M3_matched_degraded_observed'] for r in v]
                                       for f, v in fixture_clusters(rs).items()}) if ci else None,
        }

    out = {
        'band': band,
        'rel_key': rel_key,
        'population_n': len(pop),
        'faster': cell_stats(faster, 'faster'),
        'slower': cell_stats(slower, 'slower'),
        'unchanged': cell_stats(unchanged, 'unchanged'),
        'n_faster': len(faster), 'n_slower': len(slower), 'n_unchanged': len(unchanged),
        'n_missing_rel': sum(1 for r in pop if r.get(rel_key) is None),
    }
    if ci:
        fixes = sorted({r['fixture_id'] for r in faster + slower})
        fdeg = [0] * len(fixes); fn = [0] * len(fixes); sdeg = [0] * len(fixes); sn = [0] * len(fixes)
        fm = {f: i for i, f in enumerate(fixes)}
        for r in faster:
            i = fm[r['fixture_id']]
            fn[i] += 1
            fdeg[i] += int(r['M3_matched_degraded_observed'])
        for r in slower:
            i = fm[r['fixture_id']]
            sn[i] += 1
            sdeg[i] += int(r['M3_matched_degraded_observed'])
        f_total, f_deg = sum(fn), sum(fdeg)
        s_total, s_deg = sum(sn), sum(sdeg)
        faster_rate = f_deg / f_total if f_total else None
        slower_rate = s_deg / s_total if s_total else None
        ard = (faster_rate - slower_rate) if (f_total and s_total) else None
        rr = (faster_rate / slower_rate) if (faster_rate is not None and slower_rate) else None
        od = ((f_deg * (s_total - s_deg)) / (s_deg * (f_total - f_deg))) \
            if (f_total and s_total and s_deg and (f_total - f_deg)) else None
        out['abs_risk_difference'] = ard
        out['relative_risk'] = rr
        out['odds_ratio'] = od
        out['ard_ci'] = es.ci_ard(fixes, fdeg, fn, sdeg, sn) if ci else None
        out['rr_ci'] = es.ci_rr(fixes, fdeg, fn, sdeg, sn) if ci else None
        out['or_ci'] = es.ci_odds_ratio(fixes, fdeg, fn, sdeg, sn) if ci else None
    return out


# ── §4 magnitude ─────────────────────────────────────────────────────────────
def bin_analysis(pop, rel_key='rel_combined', bins=BINS7, abs_diff=False, abs_key=None):
    rows = [r for r in pop if (r.get(abs_key) if abs_diff else r.get(rel_key)) is not None]
    val = (lambda r: r[abs_key]) if abs_diff else (lambda r: r[rel_key])
    out = {'n': len(rows), 'bins': {}}
    for name, pred in bins:
        sel = [r for r in rows if pred(val(r))]
        n = len(sel)
        deg = sum(1 for r in sel if r['M3_matched_degraded_observed'])
        out['bins'][name] = {
            'n': n,
            'n_degraded': deg,
            'degradation_rate': (deg / n) if n else None,
            'degradation_ci': _ci_rate({f: [r['M3_matched_degraded_observed'] for r in v]
                                       for f, v in fixture_clusters(sel).items()}) if n else None,
            'task_success': _ts_rate(sel),
            'persistent_failure_rate': (_out_rate([r['M3_observed_class'] for r in sel]) or {}).get('persistent_failure'),
            'm1_incidence': (sum(1 for r in sel if r['M1_positive']) / n) if n else None,
            'm2_incidence': (sum(1 for r in sel if r['M2_positive']) / n) if n else None,
            'sparse': (n < 30),
        }
    return out


def continuous_latency(pop, rel_key='rel_combined'):
    sel = [r for r in pop if r.get(rel_key) is not None]
    deg = [r['M3_matched_degraded_observed'] for r in sel]
    x = np.array([r[rel_key] for r in sel], dtype=float)
    y = np.array([1.0 if d else 0.0 for d in deg])
    res = _logistic(x.reshape(-1,1), y)
    out = {
        'n': len(sel),
        'slope_per_unit_rel': res['coef'][0],
        'or_per_10pct_slower': math.exp(res['coef'][0] * 0.10),
        'se': res['se'][0],
        'z': res['z'][0],
        'ci95': [res['coef'][0] - 1.96 * res['se'][0], res['coef'][0] + 1.96 * res['se'][0]],
        'n_degraded': int(y.sum()),
    }
    # nonlinear: add rel^2
    res2 = _logistic(np.column_stack([x, x ** 2]), y)
    out['quadratic_coef'] = res2['coef'][1]
    out['quadratic_z'] = res2['z'][1]
    out['quadratic_ci95'] = [res2['coef'][1] - 1.96 * res2['se'][1],
                             res2['coef'][1] + 1.96 * res2['se'][1]]
    # monotone-direction test: faster-bin aggregate vs slower-bin aggregate (pre-specified H2)
    f = [r for r in sel if r[rel_key] < -0.10]
    s = [r for r in sel if r[rel_key] > 0.10]
    fd = sum(1 for r in f if r['M3_matched_degraded_observed'])
    sd = sum(1 for r in s if r['M3_matched_degraded_observed'])
    out['h2_faster_slower'] = {
        'n_faster': len(f), 'n_slower': len(s),
        'rate_faster': (fd / len(f)) if f else None,
        'rate_slower': (sd / len(s)) if s else None,
        'note': 'pre-specified H2 direction test uses the same estimands as §3 (ARD/RR/OR joint CIs)',
    }
    return out


def _logistic(X, y):
    """IRLS logistic fit, Gaussian SEs (used for slopes only; cluster SEs via
    the sandwich below)."""
    b = np.zeros(X.shape[1])
    n = len(y)
    for _ in range(25):
        eta = X @ b
        p = 1.0 / (1.0 + np.exp(-eta))
        p = np.clip(p, 1e-12, 1 - 1e-12)
        W = p * (1 - p)
        g = X.T @ (y - p)
        H = X.T @ (W[:, None] * X)
        try:
            step = np.linalg.solve(H, g)
        except np.linalg.LinAlgError:
            step = np.linalg.pinv(H) @ g
        b = b + step
    eta = X @ b
    p = np.clip(1.0 / (1.0 + np.exp(-eta)), 1e-12, 1 - 1e-12)
    cov = np.linalg.pinv(X.T @ ((p * (1 - p))[:, None] * X))
    return {'coef': b, 'se': np.sqrt(np.diag(cov)), 'z': b / np.sqrt(np.diag(cov)),
            'n': n}


def _logistic_cluster(X, y, cluster_idx):
    """IRLS fit + fixture-cluster sandwich SEs (pre-specified H3 uncertainty)."""
    b = np.zeros(X.shape[1])
    for _ in range(30):
        eta = X @ b
        p = np.clip(1.0 / (1.0 + np.exp(-eta)), 1e-12, 1 - 1e-12)
        W = p * (1 - p)
        g = X.T @ (y - p)
        H = X.T @ (W[:, None] * X)
        b = b + np.linalg.pinv(H) @ g
    eta = X @ b
    p = np.clip(1.0 / (1.0 + np.exp(-eta)), 1e-12, 1 - 1e-12)
    r = y - p
    W = p * (1 - p)
    A = np.linalg.pinv(X.T @ (W[:, None] * X))
    B = np.zeros_like(A)
    Wr = W * r
    for c in np.unique(cluster_idx):
        m = cluster_idx == c
        me = X[m].T @ Wr[m]                    # (k,) per-cluster score
        B += np.outer(me, me)
    V = A @ B @ A
    se = np.sqrt(np.clip(np.diag(V), 0, None))
    return {'coef': b, 'se': se, 'z': b / np.where(se > 0, se, 1),
            'ci95': [b - 1.96 * se, b + 1.96 * se], 'n': int(len(y))}


# ── §5 generation comparison ────────────────────────────────────────────────
GEN_KEYS = ('n_llm_calls', 'llm_inference_s_total', 'llm_inference_s_per_call',
            'generated_content_chars', 'generated_content_words',
            'mean_generated_chars_per_llm_call', 'final_response_chars',
            'tool_execution_s_total')


def generation_compare(rows, ben, gen, cells):
    def genmed(refs, k):
        vals = [gen.get(x, {}).get(k) for x in refs]
        vals = [v for v in vals if v is not None]
        return _med(vals)

    out = {}
    for tag, sel in cells.items():
        blk = {'n': len(sel), 'rates': {}}
        for k in GEN_KEYS:
            p_vals = [gen.get(r['trace_id'], {}).get(k) for r in sel]
            p_vals = [v for v in p_vals if v is not None]
            b_vals = []
            for r in sel:
                refs = [x for x in r.get('benign_refs', []) if x in gen]
                m = _med([gen.get(x, {}).get(k) for x in refs if gen.get(x, {}).get(k) is not None])
                if m is not None:
                    b_vals.append(m)
            blk[k] = {
                'perturbed_median': _med(p_vals),
                'ben_median_of_medians': _med(b_vals),
                'diff_median': (_med(p_vals) - _med(b_vals))
                if p_vals and b_vals and _med(p_vals) is not None and _med(b_vals) is not None else None,
                'n_pert_valid': len(p_vals), 'n_ben_med_valid': len(b_vals),
            }
        out[tag] = blk
    return out


# ── §6 mechanisms ───────────────────────────────────────────────────────────
EVID_TOOLS = ('read_text_file', 'read_file', 'list_directory', 'exec_search')


def _read_distinct_paths(r):
    """distinct document/file paths read (union) — evidence COVERAGE."""
    ms = (r.get('trace') or {}).get('tool_call_multiset') or {}
    paths = set()
    for key, cnt in ms.items():
        if ':' not in key:
            continue
        role, tool = key.split(':', 1)
        if tool in EVID_TOOLS:
            # path lives in args; approximate coverage via the count only when args absent
            pass
    # args not in compact multiset; approximate coverage = read call count
    total = 0
    for key, cnt in ms.items():
        if ':' not in key:
            continue
        _role, tool = key.split(':', 1)
        if tool in EVID_TOOLS:
            total += cnt
    return total


def _verif_chars_of(tid, gen):
    """Generated chars of the topology's verification role (verifier/synthesizer/
    reviewer), if that role produced generated content in this trace."""
    pr = (gen.get(tid) or {}).get('per_role_generated_chars') or {}
    for rk in ('verifier', 'synthesizer', 'reviewer'):
        if pr.get(rk) is not None:
            return pr[rk]
    return None


def mechanism_flags(rows, gen, ben):
    """Structural mechanism flags for the extension (§6). All predicates use
    FLAT fields on the row (perturbed) side and the matched-benign reference
    table (refs) side; benign medians over the exact V4.4 matched references.
    MG1 = reduced evidence-retrieval calls; MG3 = shorter verification-role
    generated content; MG4 = premature finalization (<=50% of benign-median
    events AND calls); MG5 = reduced repeated analysis; MG6 = fewer
    within-stage tool calls at the same stage count; MG7 = structurally
    complete (same stage count, no base MK2/MK3) yet M3-degraded.
    MG2 (distinct-path evidence coverage) is NOT observable from the compact
    tool multiset (paths live in tool_arguments) and is reported as such."""
    for r in rows:
        refs = [ben[x] for x in r.get('benign_refs', []) if x in ben]
        f = {}
        reads = _read_distinct_paths(r)
        bm_reads = _med([_read_distinct_paths(b) for b in refs]) if refs else None
        f['MG1_reduced_evidence_reads'] = (reads < bm_reads - 1) if bm_reads is not None else None
        f['MG2_reduced_evidence_coverage'] = None  # not observable (see docstring)
        verif_chars = _verif_chars_of(r['trace_id'], gen)
        bm_verif = _med([_verif_chars_of(b['trace_id'], gen) for b in refs])
        f['MG3_shorter_verification_content'] = (
            verif_chars is not None and bm_verif is not None and verif_chars < bm_verif) \
            if (verif_chars is not None or bm_verif is not None) else None
        ev_p = r.get('n_events')
        bm_ev = _med([b.get('n_events') for b in refs])
        ll_p = r.get('n_llm_calls')
        bm_ll = _med([b.get('n_llm_calls') for b in refs])
        f['MG4_earlier_finalization'] = (
            ev_p is not None and bm_ev and ll_p is not None and bm_ll and
            ev_p < 0.5 * bm_ev and ll_p < 0.5 * bm_ll)
        rep_p = r.get('repeated_tool_excess') or 0
        bm_rep = _med([(b.get('repeated_tool_excess') or 0) for b in refs])
        f['MG5_reduced_repeated_analysis'] = (rep_p < bm_rep) if bm_rep is not None else None
        tc_p = r.get('n_tool_calls')
        bm_tc = _med([b.get('n_tool_calls') for b in refs])
        st_p = r.get('n_stages_completed')
        bm_st = _med([b.get('n_stages_completed') for b in refs])
        f['MG6_reduced_within_stage_tools'] = (
            tc_p is not None and bm_tc is not None and st_p == bm_st and tc_p < bm_tc - 1)
        base_m = r.get('mechanisms') or {}
        f['MG7_complete_but_wrong'] = (
            r['M3_matched_degraded_observed'] and st_p is not None and st_p == bm_st
            and not base_m.get('MK2_skipped_verification')
            and not base_m.get('MK3_premature_finalization'))
        r['mg_flags'] = f
        r['reads_evidence_calls'] = reads
    return rows


def select_eq_examples(rows, gen, n=6):
    """Deterministic selection with stable IDs + matched-benign reference IDs."""
    import random
    rng = random.Random(SEED)
    per_tid = gen  # trace_id -> generation table

    def emit(tag, group, sortkey, note):
        sel = sorted([r for r in group], key=lambda r: sortkey(r))
        out = []
        for r in sel[:n]:
            out.append({
                'tag': tag, 'execution_id': r['execution_id'], 'trace_id': r['trace_id'],
                'fixture_id': r['fixture_id'], 'topology': r['topology'],
                'task_family': r['task_family'], 'lep_code': r['lep_code'],
                'matched_benign_refs': r.get('benign_refs', []),
                'rel_combined': r.get('rel_combined'), 'diff_combined': r.get('diff_combined'),
                'M3_observed_class': r.get('M3_observed_class'),
                'record_task_success': r.get('record_task_success'),
                'runtime_trust': r.get('runtime_trust'),
                'n_stages_completed': r.get('n_stages_completed'),
                'n_llm_calls': r.get('n_llm_calls'), 'n_tool_calls': r.get('n_tool_calls'),
                'reads_evidence_calls': r.get('reads_evidence_calls'),
                'mg_flags': r.get('mg_flags'),
                'gen': {k: per_tid.get(r['trace_id'], {}).get(k) for k in
                         ('generated_content_chars', 'n_llm_calls', 'llm_inference_s_per_call',
                          'mean_generated_chars_per_llm_call')},
                'note': note,
            })
        return out

    ge = [r for r in rows if r['gradeable'] and r.get('rel_combined') is not None]
    fd = [r for r in ge if r['M3_matched_degraded_observed'] and r['rel_combined'] < -0.10]
    so = [r for r in ge if r['M3_matched_degraded_observed'] and r['rel_combined'] > 0.10]
    fn = [r for r in ge if not r['M3_matched_degraded_observed'] and r['rel_combined'] < -0.10]
    ex = []
    ex += emit('faster_degraded_dom', fd, lambda r: r['rel_combined'],
               'dominant cell: faster + observed matched degradation; structural completeness visible in n_stages/mg_flags')
    ex += emit('slower_degraded', so, lambda r: -r['rel_combined'],
               'slower + degraded (the naive wasted-effort cell)')
    ex += emit('faster_not_degraded_counter', fn, lambda r: r['rel_combined'],
               'counterexample: faster with no degradation')
    return ex


# ── §7 adjusted model (H3) ─────────────────────────────────────────────────
VALID_COMBOS = [('branch_and_verify', 'many_to_one'), ('branch_and_verify', 'single_origin'),
                ('coordinator_workers', 'one_to_many'), ('coordinator_workers', 'single_origin'),
                ('review_loop', 'single_origin')]


def design_matrix(pop):
    fam_ref = 'code_review'
    tm_ref = ('branch_and_verify', 'single_origin')
    lep_ref = 'LEP_HANDOFF_CORRUPTION'
    fams = sorted(set(r['task_family'] for r in pop))
    leps = sorted(set(r['lep_code'] for r in pop))
    X = []
    for r in pop:
        v = [1.0]
        v += [float(r['rel_combined'])]
        v += [float(r['task_family'] == f) for f in fams if f != fam_ref]
        tm = (r['topology'], r['propagation_mode'])
        v += [float(tm == t) for t in VALID_COMBOS if t != tm_ref]
        v += [float(r['lep_code'] == l) for l in leps if l != lep_ref]
        v += [float((r.get('ben_med_combined') or 0.0))]
        v += [float((r.get('backend_retries') or 0) > 0),
              float((r.get('n_tool_errors') or 0) > 0),
              float((r.get('record') or {}).get('termination_reason') not in ('completed', None))]
        X.append(v)
    cols = ['intercept', 'rel_latency',
            [f'fam_{f}' for f in fams if f != fam_ref],
            [f'tm_{t[0]}_{t[1]}' for t in VALID_COMBOS if t != tm_ref],
            [f'lep_{l}' for l in leps if l != lep_ref],
            'ben_median_latency', 'retries_gt0', 'tool_errors_gt0', 'failed_termination']
    flat = []
    for c in cols:
        if isinstance(c, list):
            flat += c
        else:
            flat.append(c)
    return np.array(X, dtype=float), flat


def adjusted_model(pop):
    if not pop:
        return None
    X, cols = design_matrix(pop)
    y = np.array([1.0 if r['M3_matched_degraded_observed'] else 0.0 for r in pop])
    cl = np.array([i for i, r in enumerate(pop) for _ in [0]])
    cl = np.array([hash(r['fixture_id']) for r in pop])
    # deterministic cluster ids
    fids = sorted(set(r['fixture_id'] for r in pop))
    cmap = {f: i for i, f in enumerate(fids)}
    cl = np.array([cmap[r['fixture_id']] for r in pop])
    fit = _logistic_cluster(X, y, cl)
    out = {'n': fit['n'], 'n_clusters': len(fids),
           'n_degraded': int(y.sum())}
    for i, c in enumerate(cols):
        lo, hi = fit['ci95'][0][i], fit['ci95'][1][i]
        out[c] = {'coef': fit['coef'][i], 'se': fit['se'][i],
                  'ci95': [lo, hi],
                  'or': math.exp(fit['coef'][i]) if i > 0 else None}
    out['note'] = ('primary adjusted model per PREREGISTRATION H3: covariates = task family, '
                   'valid topology×mode, LEP, benign-median latency, infrastructure flags; '
                   'fixture-cluster sandwich SEs (903 clusters). LLM-call counts / content '
                   'lengths are mediators and are NOT in this model. Observational — not causal.')
    return out


# ── §9 efficiency × quality joint cells ─────────────────────────────────────
def joint_cells(pop):
    def fast(r): return r.get('rel_combined') is not None and r['rel_combined'] < -0.10
    def slow(r): return r.get('rel_combined') is not None and r['rel_combined'] > 0.10
    def corr(r):
        if r.get('runtime_trust') in TRUSTED and isinstance(r.get('record_task_success'), bool):
            return r['record_task_success']
        return None
    cells = {}
    for tag, pred in (
            ('faster_task_correct', lambda r: fast(r) and corr(r) is True),
            ('faster_task_incorrect', lambda r: fast(r) and corr(r) is False),
            ('slower_task_correct', lambda r: slow(r) and corr(r) is True),
            ('slower_task_incorrect', lambda r: slow(r) and corr(r) is False),
            ('faster_m3_degraded', lambda r: fast(r) and r['M3_matched_degraded_observed']),
            ('faster_m3_improved', lambda r: fast(r) and r['M3_observed_class'] == 'improved')):
        sel = [r for r in pop if pred(r)]
        cells[tag] = {'n': len(sel), 'share_of_pop': (len(sel) / len(pop)) if pop else None}
    # "would a latency-only assessment call degraded executions operational improvements?"
    deg = [r for r in pop if r['M3_matched_degraded_observed']]
    not_slower = [r for r in deg if r.get('rel_combined') is not None and r['rel_combined'] <= 0.10]
    cells['degraded_classified_not_slower_by_latency_only'] = {
        'n': len(not_slower), 'n_degraded_with_rel': sum(1 for r in deg if r.get('rel_combined') is not None),
        'share': (len(not_slower) / sum(1 for r in deg if r.get('rel_combined') is not None))
        if any(r.get('rel_combined') is not None for r in deg) else None}
    return cells


def main():
    rows, ben, gen = load_all()
    rows = build_latency_modes(rows, ben)
    # join base mechanism flags (MK*) onto rows
    mk = {json.loads(l)['trace_id']: json.loads(l) for l in
          open(os.path.join(BASE, 'mechanism_examples.jsonl')) if l.strip()}
    for r in rows:
        r['mechanisms'] = (mk.get(r['trace_id'], {}) or {}).get('mechanisms')
    rows = mechanism_flags(rows, gen, ben)

    # ── per-execution extension table (compact) ──
    pex_path = os.path.join(OUT, 'efficiency_quality_per_execution.jsonl')
    keep = ('execution_id', 'trace_id', 'fixture_id', 'lep_code', 'task_family', 'topology',
            'propagation_mode', 'M1_positive', 'M2_positive', 'M2_dependency_supported',
            'M2_reach_class', 'M3_observed_class', 'M3_matched_degraded_observed', 'gradeable',
            'record_task_success', 'runtime_trust', 'benign_refs', 'latency_s',
            'lat_trusted_runtime', 'lat_event_span', 'lat_combined',
            'ben_med_trusted_runtime', 'ben_mean_trusted_runtime',
            'ben_med_event_span', 'ben_mean_event_span',
            'ben_med_combined', 'ben_mean_combined',
            'diff_trusted_runtime', 'rel_trusted_runtime', 'sign_trusted_runtime',
            'diff_event_span', 'rel_event_span', 'sign_event_span',
            'diff_combined', 'rel_combined', 'sign_combined',
            'rel_mean_trusted_runtime', 'rel_mean_event_span', 'rel_mean_combined',
            'n_stages_completed', 'n_llm_calls', 'n_tool_calls', 'n_events',
            'n_tool_errors', 'backend_retries', 'repeated_tool_excess',
            'reads_evidence_calls', 'mg_flags')
    with open(pex_path, 'w') as fh:
        for r in rows:
            fh.write(json.dumps({k: r.get(k) for k in keep}, separators=(',', ':')) + '\n')
    print('wrote', pex_path)

    # ── population definitions ──
    ge = [r for r in rows if r['gradeable']]
    valid_comb = [r for r in ge if r.get('diff_combined') is not None]
    infra_clean = [r for r in rows
                   if (r.get('backend_retries') or 0) == 0 and (r.get('n_tool_errors') or 0) == 0
                   and (r.get('record') or {}).get('termination_reason') in ('completed', None)
                   and r.get('diff_combined') is not None]
    ic_ge = [r for r in infra_clean if r['gradeable']]

    # at least one SUCCESSFUL trusted benign reference
    def has_success_ref(r):
        for x in r.get('benign_refs', []):
            b = ben.get(x)
            if b and b.get('runtime_trust') in TRUSTED and \
                    (b.get('record') or {}).get('task_success') is True:
                return True
        return False
    success_ref = [r for r in valid_comb if has_success_ref(r)]

    agg = {'meta': {'seed': SEED, 'n_boot': N_BOOT,
                    'populations': {'grading_eligible': len(ge),
                                    'ge_valid_combined_latency': len(valid_comb),
                                    'infra_clean': len(infra_clean),
                                    'infra_clean_ge': len(ic_ge),
                                    'with_successful_ref': len(success_ref)},
                    'preregistration': 'see PREREGISTRATION.md'},
           'sections': {}, 'figures_inputs': {}}

    # ── §3 primary ──
    primary = faster_slower_compare(valid_comb, 'rel_combined')
    # reconcile base 608/444/1,052
    base_pop = [r for r in ge if (r.get('record') is not None)
                and r.get('ben_med_combined') is not None and r.get('diff_combined') is not None]
    recon = {
        'base_tradeoff_pop_n': len(base_pop),
        'note': ('base §6 tradeoff pop (12,562) = gradeable AND record-non-null AND frozen '
                 'ben-median/diff valid; M3-degraded cells on that pop: 612/441/934 = 1,987. '
                 'The extension primary pop (12,625) additionally includes 63 grading-eligible '
                 'rows whose refs lack a recorded runtime but have a traced span (span-fallback '
                 'ben-median); of those, 5 are M3-positive (1 slower + 4 unchanged) giving '
                 '612/442/938 = 1,992. The base report BODY text (608/444/935, "1,052") is from '
                 'an early trusted-record ben-median pass; the frozen base aggregate and this '
                 'extension both use the combined-median definition.'),
    }
    fd = sum(1 for r in base_pop if r['M3_matched_degraded_observed'] and r.get('rel_combined') is not None
             and r['rel_combined'] < -0.10)
    sd = sum(1 for r in base_pop if r['M3_matched_degraded_observed'] and r.get('rel_combined') is not None
             and r['rel_combined'] > 0.10)
    uc = sum(1 for r in base_pop if r['M3_matched_degraded_observed'] and r.get('rel_combined') is not None
             and abs(r['rel_combined']) <= 0.10)
    recon['m3_degraded_faster'] = fd
    recon['m3_degraded_slower'] = sd
    recon['m3_degraded_unchanged'] = uc
    recon['sum'] = fd + sd + uc
    recon['excluded_from_1052'] = 1992 - (fd + sd + uc)
    recon['excluded_breakdown'] = {
        'record_null_rows': sum(1 for r in [x for x in ge if x['M3_matched_degraded_observed']]
                               if r.get('record') is None),
        'unchanged_band': uc,
    }
    agg['sections']['s3_primary'] = {
        'population': 'grading-eligible with valid combined matched latency',
        'n': len(valid_comb),
        'contingency': {
            'faster_degraded': primary['faster']['n_degraded'],
            'faster_not_degraded': primary['faster']['n'] - primary['faster']['n_degraded'],
            'slower_degraded': primary['slower']['n_degraded'],
            'slower_not_degraded': primary['slower']['n'] - primary['slower']['n_degraded'],
            'unchanged_degraded': primary['unchanged']['n_degraded'],
            'unchanged_not_degraded': primary['unchanged']['n'] - primary['unchanged']['n_degraded'],
        },
        'faster': primary['faster'], 'slower': primary['slower'], 'unchanged': primary['unchanged'],
        'ard': primary['abs_risk_difference'], 'ard_ci': primary['ard_ci'],
        'rr': primary['relative_risk'], 'rr_ci': primary['rr_ci'],
        'or': primary['odds_ratio'], 'or_ci': primary['or_ci'],
        'reconcile_base_counts': recon,
    }
    # §3 repeat with successful-ref population
    s3b = faster_slower_compare(success_ref, 'rel_combined')
    agg['sections']['s3_successful_ref_pop'] = {
        'n': len(success_ref),
        'ard': s3b['abs_risk_difference'], 'ard_ci': s3b['ard_ci'],
        'rr': s3b['relative_risk'], 'rr_ci': s3b['rr_ci'],
        'or': s3b['odds_ratio'], 'or_ci': s3b['or_ci'],
        'faster_rate': s3b['faster']['degradation_rate'],
        'slower_rate': s3b['slower']['degradation_rate'],
        'note': 'persistent failures cannot exhibit matched degradation; this pop has >=1 '
                'successful trusted benign ref',
    }

    # ── §4 magnitude ──
    agg['sections']['s4_bins7'] = bin_analysis(valid_comb, 'rel_combined')
    agg['sections']['s4_abs_diff_bins7'] = bin_analysis(valid_comb, abs_diff=True, abs_key='diff_combined')
    agg['sections']['s4_continuous_rel'] = continuous_latency(valid_comb, 'rel_combined')
    for tag, abins in ALT_BINS:
        agg['sections'][f's4_alt_bins_{tag}'] = bin_analysis(valid_comb, 'rel_combined', bins=abins)

    # ── §5 generation ──
    ge_rel = [r for r in ge if r.get('rel_combined') is not None]
    cells = {
        'faster_degraded': [r for r in ge_rel if r['M3_matched_degraded_observed'] and r['rel_combined'] < -0.10],
        'faster_not_degraded': [r for r in ge_rel if not r['M3_matched_degraded_observed'] and r['rel_combined'] < -0.10],
        'slower_degraded': [r for r in ge_rel if r['M3_matched_degraded_observed'] and r['rel_combined'] > 0.10],
        'slower_not_degraded': [r for r in ge_rel if not r['M3_matched_degraded_observed'] and r['rel_combined'] > 0.10],
        'M3_degraded_all': [r for r in ge_rel if r['M3_matched_degraded_observed']],
    }
    agg['sections']['s5_generation'] = generation_compare(rows, ben, gen, cells)
    # per-role generation for verifier
    agg['sections']['s5_generation_note'] = (
        'char/word counts are CONTENT-LENGTH measurements, not token counts; '
        'dedup counts each generated piece once at its source tool_call; '
        'reasoning.output_text is a [tool_use:X] marker, NOT chain-of-thought '
        '(CoT is not stored in this corpus).')

    # ── §6 mechanisms ──
    for tag, sel in cells.items():
        pre = {}
        for k in ('MG1_reduced_evidence_reads', 'MG3_shorter_verification_content',
                  'MG4_earlier_finalization', 'MG5_reduced_repeated_analysis',
                  'MG6_reduced_within_stage_tools', 'MG7_complete_but_wrong'):
            tv = [r.get('mg_flags', {}).get(k) for r in sel]
            tv = [v for v in tv if v is not None]
            pre[k] = {'n_flagged': sum(1 for v in tv if v), 'n_evaluable': len(tv),
                      'prevalence': (sum(1 for v in tv if v) / len(tv)) if tv else None}
        agg['sections'].setdefault('s6_mechanisms', {})[tag] = pre
    examples = select_eq_examples(rows, gen)
    ex_path = os.path.join(HERE, 'mechanism_examples.jsonl')
    with open(ex_path, 'w') as fh:
        for e in examples:
            fh.write(json.dumps(e, separators=(',', ':')) + '\n')
    print('wrote', ex_path, len(examples), 'examples')

    # ── §7 strata + adjusted ──
    strata = {}
    for fam in sorted(set(r['task_family'] for r in valid_comb)):
        sub = [r for r in valid_comb if r['task_family'] == fam]
        c = faster_slower_compare(sub, 'rel_combined')
        strata[f'family_{fam}'] = {'n': len(sub), 'ard': c['abs_risk_difference'],
                                   'ard_ci': c['ard_ci'], 'rr': c['relative_risk'],
                                   'rr_ci': c['rr_ci'], 'or': c['odds_ratio'],
                                   'or_ci': c['or_ci'],
                                   'faster_rate': c['faster']['degradation_rate'],
                                   'slower_rate': c['slower']['degradation_rate']}
    for lep in sorted(set(r['lep_code'] for r in valid_comb)):
        sub = [r for r in valid_comb if r['lep_code'] == lep]
        c = faster_slower_compare(sub, 'rel_combined')
        strata[f'lep_{lep}'] = {'n': len(sub), 'ard': c['abs_risk_difference'],
                                'ard_ci': c['ard_ci'], 'rr': c['relative_risk'],
                                'rr_ci': c['rr_ci'], 'faster_rate': c['faster']['degradation_rate'],
                                'slower_rate': c['slower']['degradation_rate']}
    for t in sorted(set(r['topology'] for r in valid_comb)):
        sub = [r for r in valid_comb if r['topology'] == t]
        c = faster_slower_compare(sub, 'rel_combined')
        strata[f'topology_{t}'] = {'n': len(sub), 'ard': c['abs_risk_difference'],
                                   'ard_ci': c['ard_ci'], 'faster_rate': c['faster']['degradation_rate'],
                                   'slower_rate': c['slower']['degradation_rate']}
    for t, m in VALID_COMBOS:
        sub = [r for r in valid_comb if r['topology'] == t and r['propagation_mode'] == m]
        c = faster_slower_compare(sub, 'rel_combined')
        strata[f'topo_mode_{t}_{m}'] = {'n': len(sub), 'ard': c['abs_risk_difference'],
                                        'ard_ci': c['ard_ci'],
                                        'faster_rate': c['faster']['degradation_rate'],
                                        'slower_rate': c['slower']['degradation_rate'],
                                        'sparse': len(sub) < 30}
    for coh in ('neither', 'm1_only', 'm2_only', 'both'):
        pred = ({'neither': lambda r: not r['M1_positive'] and not r['M2_positive'],
                 'm1_only': lambda r: r['M1_positive'] and not r['M2_positive'],
                 'm2_only': lambda r: not r['M1_positive'] and r['M2_positive'],
                 'both': lambda r: r['M1_positive'] and r['M2_positive']}[coh])
        sub = [r for r in valid_comb if pred(r)]
        c = faster_slower_compare(sub, 'rel_combined')
        strata[f'cohort_{coh}'] = {'n': len(sub), 'ard': c['abs_risk_difference'],
                                   'ard_ci': c['ard_ci'],
                                   'faster_rate': c['faster']['degradation_rate'],
                                   'slower_rate': c['slower']['degradation_rate']}
    m2s = [r for r in valid_comb if r['M2_positive'] and r['M2_dependency_supported']]
    m2u = [r for r in valid_comb if r['M2_positive'] and not r['M2_dependency_supported']]
    for key, sub in (('m2_dep_supported', m2s), ('m2_dep_unsupported', m2u)):
        c = faster_slower_compare(sub, 'rel_combined')
        strata[key] = {'n': len(sub), 'ard': c['abs_risk_difference'], 'ard_ci': c['ard_ci'],
                       'faster_rate': c['faster']['degradation_rate'],
                       'slower_rate': c['slower']['degradation_rate'],
                       'note': 'M2-O4 dependency-supported vs unsupported reach classes; '
                               'unsupported != disproven (checkpoint-gate outcome)'}
    for rc in ('verified', 'plausible'):
        sub = [r for r in valid_comb if r.get('M2_reach_class') == rc]
        c = faster_slower_compare(sub, 'rel_combined')
        strata[f'm2_reach_{rc}'] = {'n': len(sub), 'ard': c['abs_risk_difference'],
                                    'ard_ci': c['ard_ci'],
                                    'faster_rate': c['faster']['degradation_rate'],
                                    'slower_rate': c['slower']['degradation_rate']}
    agg['sections']['s7_strata'] = strata
    agg['sections']['s7_adjusted'] = adjusted_model(valid_comb)
    # secondary mediator models (mediators are NOT in the primary model — PREREGISTRATION)
    med = {}
    X0, cols0 = design_matrix(valid_comb)
    y = np.array([1.0 if r['M3_matched_degraded_observed'] else 0.0 for r in valid_comb])
    fids = sorted(set(r['fixture_id'] for r in valid_comb))
    cm = {f: i for i, f in enumerate(fids)}
    cidx = np.array([cm[r['fixture_id']] for r in valid_comb])
    rel_i = cols0.index('rel_latency')
    for mk_, getv in (('n_llm_calls', lambda r: r.get('n_llm_calls')),
                      ('gen_content_chars',
                       lambda r: gen.get(r['trace_id'], {}).get('generated_content_chars')),
                      ('llm_s_per_call',
                       lambda r: gen.get(r['trace_id'], {}).get('llm_inference_s_per_call'))):
        extra = np.array([float(getv(r)) if getv(r) is not None else 0.0 for r in valid_comb])
        Xmed = np.column_stack([X0, extra])
        colsmed = cols0 + [f'mediator_{mk_}']
        fit = _logistic_cluster(Xmed, y, cidx)
        imed = colsmed.index(f'mediator_{mk_}')
        med[mk_] = {'coef': fit['coef'][imed],
                    'ci95': [fit['ci95'][0][imed], fit['ci95'][1][imed]],
                    'or': math.exp(fit['coef'][imed])}
    # does the latency coefficient persist once each mediator is in the model?
    med['rel_latency_coefs_after_adding_mediator'] = {}
    for mk_, getv in (('n_llm_calls', lambda r: r.get('n_llm_calls')),
                      ('gen_content_chars',
                       lambda r: gen.get(r['trace_id'], {}).get('generated_content_chars')),
                      ('llm_s_per_call',
                       lambda r: gen.get(r['trace_id'], {}).get('llm_inference_s_per_call'))):
        extra = np.array([float(getv(r)) if getv(r) is not None else 0.0 for r in valid_comb])
        fit = _logistic_cluster(np.column_stack([X0, extra]), y, cidx)
        med['rel_latency_coefs_after_adding_mediator'][mk_] = {
            'coef': fit['coef'][rel_i], 'ci95': [fit['ci95'][0][rel_i], fit['ci95'][1][rel_i]]}
    med['note'] = 'secondary models; mediators excluded from the primary model; ' \
                  'observational, not causal'
    agg['sections']['s7_mediator_models'] = med

    # ── §9 joint cells ──
    agg['sections']['s9_joint'] = joint_cells(valid_comb)

    # ── §2 latency audit (reconciliations) ──
    audit = {
        'corpus': len(rows),
        'trusted_runtime_valid': sum(1 for r in valid_comb if r.get('diff_trusted_runtime') is not None),
        'event_span_valid': sum(1 for r in valid_comb if r.get('diff_event_span') is not None),
        'combined_valid': len(valid_comb),
        'record_trust_census': {
            'attempt_id': sum(1 for r in rows if r.get('runtime_trust') == 'attempt_id'),
            'consistent': sum(1 for r in rows if r.get('runtime_trust') == 'consistent'),
            'inconsistent': sum(1 for r in rows if r.get('runtime_trust') == 'inconsistent'),
            'none': sum(1 for r in rows if r.get('runtime_trust') == 'none')},
        'failed_harness_3626': ('inconsistent + none rows are failed-harness / record-less rows; '
                                 'their recorded runtime (~0s, 3-event shape) is EXCLUDED from the '
                                 'trusted_runtime population by construction (runtime_trust gate); '
                                 'the infra_clean population adds termination/completed + retry/error gates'),
        'mean_vs_median_corpus': {
            'mean_wall_clock': _mean([r.get('lat_combined') for r in valid_comb]),
            'median_wall_clock': _med([r.get('lat_combined') for r in valid_comb]),
            'mean_matched_diff_combined': _mean([r.get('diff_combined') for r in valid_comb]),
            'median_matched_diff_combined': _med([r.get('diff_combined') for r in valid_comb]),
            'abs_dist': None, 'rel_dist_median': _med([r.get('rel_combined') for r in valid_comb])},
    }
    # record vs span interval comparability
    pairs = [(r.get('lat_trusted_runtime'), r.get('lat_event_span')) for r in rows
             if r.get('lat_trusted_runtime') is not None and r.get('lat_event_span') is not None]
    diffs = [abs(a - b) for a, b in pairs]
    audit['runtime_vs_span_comparability'] = {
        'n_pairs': len(pairs), 'median_abs_diff_s': _med(diffs),
        'p95_abs_diff_s': (sorted(diffs)[int(0.95 * len(diffs))] if diffs else None),
        'max_abs_diff_s': (max(diffs) if diffs else None),
        'note': 'runtime_seconds and event span describe the same execution interval '
                '(logging points differ); p95 0.024 s in the base audit'}
    agg['sections']['s2_latency_audit'] = audit

    # ── §8 sensitivities ──
    sens = {}
    for mode, dkey, rkey in (('trusted_runtime', 'diff_trusted_runtime', 'rel_trusted_runtime'),
                             ('event_span', 'diff_event_span', 'rel_event_span')):
        popm = [r for r in ge if r.get(dkey) is not None]
        c = faster_slower_compare(popm, rkey)
        sens[f'mode_{mode}'] = {'n': len(popm), 'ard': c['abs_risk_difference'],
                                'ard_ci': c['ard_ci'], 'rr': c['relative_risk'],
                                'faster_rate': c['faster']['degradation_rate'],
                                'slower_rate': c['slower']['degradation_rate']}
    cic = [r for r in ic_ge if r.get('diff_combined') is not None]
    c = faster_slower_compare(cic, 'rel_combined')
    sens['infra_clean'] = {'n': len(cic), 'ard': c['abs_risk_difference'],
                          'ard_ci': c['ard_ci'],
                          'faster_rate': c['faster']['degradation_rate'],
                          'slower_rate': c['slower']['degradation_rate']}
    # benign mean reference
    popmean = [r for r in valid_comb if r.get('rel_mean_combined') is not None]
    c = faster_slower_compare(popmean, 'rel_mean_combined')
    sens['benign_mean_reference'] = {'n': len(popmean), 'ard': c['abs_risk_difference'],
                                     'ard_ci': c['ard_ci'],
                                     'faster_rate': c['faster']['degradation_rate'],
                                     'slower_rate': c['slower']['degradation_rate']}
    # alternative bands
    for band in (0.05, 0.20):
        c = faster_slower_compare(valid_comb, 'rel_combined', band=band)
        sens[f'band_{int(band*100)}pct'] = {'n': len(valid_comb), 'ard': c['abs_risk_difference'],
                                            'ard_ci': c['ard_ci'],
                                            'faster_rate': c['faster']['degradation_rate'],
                                            'slower_rate': c['slower']['degradation_rate']}
    # winsorized extremes
    p999 = sorted(abs(r['diff_combined']) for r in valid_comb if r.get('diff_combined') is not None)
    thr = p999[int(0.999 * len(p999))]
    popw = [r for r in valid_comb if abs(r.get('diff_combined') or 0) <= thr]
    c = faster_slower_compare(popw, 'rel_combined')
    sens['winsor_p999'] = {'n': len(popw), 'n_excluded': len(valid_comb) - len(popw),
                           'threshold_abs_s': thr, 'ard': c['abs_risk_difference'],
                           'ard_ci': c['ard_ci'],
                           'faster_rate': c['faster']['degradation_rate'],
                           'slower_rate': c['slower']['degradation_rate']}
    # within-family adjusted stability (secondary; pre-specified sensitivity §8.9)
    wf = {}
    for fam in sorted(set(r['task_family'] for r in valid_comb)):
        sub = [r for r in valid_comb if r['task_family'] == fam]
        Xf, colsf = design_matrix(sub)
        yf = np.array([1.0 if r['M3_matched_degraded_observed'] else 0.0 for r in sub])
        fids = sorted(set(r['fixture_id'] for r in sub))
        cm = {f: i for i, f in enumerate(fids)}
        cidx = np.array([cm[r['fixture_id']] for r in sub])
        fit = _logistic_cluster(Xf, yf, cidx)
        i = colsf.index('rel_latency')
        wf[fam] = {'n': len(sub), 'coef': fit['coef'][i],
                   'ci95': [fit['ci95'][0][i], fit['ci95'][1][i]],
                   'or_per_10pct_slower': math.exp(fit['coef'][i] * 0.10)}
    sens['within_family_adjusted'] = wf
    agg['sections']['s8_sensitivities'] = sens

    # figures inputs
    agg['figures_inputs'] = {
        'bin_rates': [
            {'label': k, **v} for k, v in agg['sections']['s4_bins7']['bins'].items()],
    }
    outp = os.path.join(OUT, 'efficiency_quality_aggregates.json')
    with open(outp, 'w') as fh:
        json.dump(agg, fh, indent=1, default=float)
    print('wrote', outp)
    p3 = agg['sections']['s3_primary']
    print('primary ARD=%s RR=%s OR=%s | faster_rate=%s slower_rate=%s' % (
        p3['ard'], p3['rr'], p3['or'], p3['faster']['degradation_rate'], p3['slower']['degradation_rate']))


if __name__ == '__main__':
    main()
