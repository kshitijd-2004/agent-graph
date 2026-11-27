#!/usr/bin/env python3.11
"""V4.4 operational-impact aggregation (deterministic, frozen-contract).

Reads:
  out/operational_impact_per_execution.jsonl   (extracted, deterministic)
  out/benign_reference_operational.jsonl
  out/extraction_coverage.json
Writes:
  operational_impact_aggregates.json

Method (documented in the report):
  * unit of analysis = perturbed execution vs its matched benign references
    (frozen V4.4 identities, reused verbatim).
  * benign reference statistic = median (primary); mean = sensitivity.
  * independent observation unit = fixture (903). All CIs = fixture-cluster
    bootstrap, SEED=20270301, N_BOOT=4000 (pinned, same seed as V4.4).
  * missing values are never zero-filled; coverage is carried as n_valid.
  * cohorts are mutually exclusive: neither / m1_only / m2_only / both.
  * 'slower'/'faster' vs the benign median; |rel diff| <= 0.10 = 'unchanged'
    (named 10% band; sensitivity at 5% and 20% reported alongside).
"""
from __future__ import annotations

import json
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import boot  # vectorized fixture-cluster bootstrap (same scheme/seed as the ref)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out')
SEED = 20270301
N_BOOT = 4000
UNCHANGED_BAND = 0.10
SENS_BANDS = (0.05, 0.20)

# core operational metrics (key -> row-path)
METRIC_PATHS = {
    'wall_clock_s': ('record', 'runtime_seconds'),
    'trace_span_s': ('trace', 'trace_span_s'),
    'llm_inference_s': ('trace', 'llm_inference_s_reconstructed'),
    'tool_execution_s': ('trace', 'tool_execution_s_reconstructed'),
    'orchestration_wait_s': ('trace', 'orchestration_wait_s_reconstructed'),
    'n_events': ('trace', 'n_events'),
    'n_llm_calls': ('trace', 'n_reasoning'),
    'n_tool_calls': ('trace', 'n_tool_calls'),
    'n_tool_results': ('trace', 'n_tool_results'),
    'n_tool_errors': ('trace', 'n_tool_errors'),
    'n_agent_handoffs': ('trace', 'n_agent_handoffs'),
    'n_topology_transitions': ('trace', 'n_topology_transitions'),
    'n_memory_ops': ('trace', 'n_memory_write'),
    'n_finalization': ('trace', 'n_finalization_handoff'),
    'dup_tool_groups': ('trace', 'duplicate_tool_call_groups'),
    'repeated_tool_excess': ('trace', 'repeated_tool_call_excess'),
    'backend_retries': ('trace', 'metadata.backend_retry_count'),
    'chars_in_proxy': ('trace', 'chars_input_reasoning_proxy'),
    'chars_out_proxy': ('trace', 'chars_output_reasoning_proxy'),
    'n_stages_completed': ('trace', 'metadata.stage_count'),
    'review_cycles': ('trace', 'metadata.review_cycle_count'),
}

COHORTS = ('neither', 'm1_only', 'm2_only', 'both')


def gp(row, a, b=None):
    cur = row.get(a)
    if cur is None:
        return None
    if b is None:
        return cur if isinstance(cur, (int, float)) and not isinstance(cur, bool) else None
    if not isinstance(cur, dict):
        return None
    v = cur.get(b)
    if isinstance(v, str):
        return v
    return v if isinstance(v, (int, float, bool)) else None


def gbool(row, a, b):
    """Boolean-recorded fields (record.task_success etc.). None if absent/not bool."""
    cur = row.get(a)
    if not isinstance(cur, dict):
        return None
    v = cur.get(b)
    return v if isinstance(v, bool) else None


def trusted(row):
    """True when the row's matched record describes the measured execution."""
    return row.get('runtime_trust') in ('attempt_id', 'consistent')


ben_trusted = trusted


def num(row, a, b=None):
    v = gp(row, a, b)
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def flat(row):
    """Flatten trace-derived convenience fields to top level (idempotent)."""
    t = row.get('trace') or {}
    md = t.get('metadata') or {}
    row['trace_span_s'] = t.get('trace_span_s')
    row['llm_inference_s'] = t.get('llm_inference_s_reconstructed')
    row['tool_execution_s'] = t.get('tool_execution_s_reconstructed')
    row['orchestration_wait_s'] = t.get('orchestration_wait_s_reconstructed')
    row['n_events'] = t.get('n_events')
    row['n_llm_calls'] = t.get('n_reasoning')
    row['n_tool_calls'] = t.get('n_tool_calls')
    row['n_tool_errors'] = t.get('n_tool_errors')
    row['n_agent_handoffs'] = t.get('n_agent_handoffs')
    row['n_memory_ops'] = ((t.get('n_memory_write') or 0) + (t.get('n_memory_retrieval') or 0))
    row['dup_tool_groups'] = t.get('duplicate_tool_call_groups')
    row['repeated_tool_excess'] = t.get('repeated_tool_call_excess')
    row['backend_retries'] = md.get('backend_retry_count')
    row['review_cycles'] = md.get('review_cycle_count')
    row['n_stages_completed'] = md.get('stage_count')
    row['termination_reason'] = ((row.get('record') or {}).get('termination_reason')
                                 or md.get('termination_reason'))
    row['trace_source_prefix'] = (t.get('trace_source_basename') or '')
    row['trace_sha256'] = t.get('trace_sha256')
    row['ts_valid'] = t.get('ts_valid')
    row['ts_monotonic'] = t.get('ts_monotonic')
    row['ts_nonmonotonic_count'] = t.get('ts_nonmonotonic_count')
    row['record_task_success'] = (row.get('record') or {}).get('task_success')
    row['latency_s'], row['latency_source'] = eff_latency(row)
    return row


def metric(row, key):
    a, b = METRIC_PATHS[key]
    if key == 'wall_clock_s':
        return row.get('latency_s')
    if key == 'n_memory_ops':
        w = num(row, 'trace', 'n_memory_write')
        r = num(row, 'trace', 'n_memory_retrieval')
        if w is None and r is None:
            return None
        return (w or 0.0) + (r or 0.0)
    if key == 'n_stages_completed':
        v = row.get('n_stages_completed')
        if v is None:
            v = num(row, 'record', 'num_stages')
        return v
    if key in ('n_memory_ops', 'review_cycles'):
        return row.get(key)
    return num(row, a, b)


def cohort(row):
    m1 = row['M1_positive']
    m2 = row['M2_positive']
    return ('neither' if not (m1 or m2) else
            'm1_only' if m1 and not m2 else
            'm2_only' if m2 and not m1 else 'both')


def eff_latency(row):
    """Effective latency measure for a row (primary matched latency quantity).

    runtime_seconds is used only where the record provably describes this
    execution (runtime_trust in {'attempt_id','consistent'}). Where the
    matched record is a failed harness attempt (trust 'inconsistent':
    termination backend_http_error / backend_connection_error, runtime ~0) or
    absent ('none'), fall back to the recorded trace event-timeline span.
    Never zero-filled.
    """
    t = row.get('runtime_trust')
    rec = row.get('record') or {}
    if t in ('attempt_id', 'consistent') and rec.get('runtime_seconds') is not None:
        return rec['runtime_seconds'], 'runtime_recorded'
    span = (row.get('trace') or {}).get('trace_span_s')
    return span, ('trace_span_fallback' if span is not None else 'unavailable')


def _median(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _pct(xs, q):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    xs = sorted(xs)
    if len(xs) == 1:
        return xs[0]
    i = q * (len(xs) - 1)
    lo = int(math.floor(i))
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


def _fmt(x, nd=4):
    if x is None:
        return None
    if isinstance(x, float):
        if abs(x) >= 1000:
            return float(f'%.4g' % x)
        return round(x, nd)
    return x


def dist_summary(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return {
        'n_valid': len(vals),
        'mean': _fmt(_mean(vals)),
        'median': _fmt(_median(vals)),
        'p25': _fmt(_pct(vals, 0.25)),
        'p75': _fmt(_pct(vals, 0.75)),
        'p90': _fmt(_pct(vals, 0.90)),
        'p95': _fmt(_pct(vals, 0.95)),
        'p99': _fmt(_pct(vals, 0.99)),
        'min': _fmt(min(vals)),
        'max': _fmt(max(vals)),
    }


def load_all():
    rows = [json.loads(l) for l in open(os.path.join(OUT, 'operational_impact_per_execution.jsonl')) if l.strip()]
    ben = {json.loads(l)['trace_id']: json.loads(l) for l in
           open(os.path.join(OUT, 'benign_reference_operational.jsonl')) if l.strip()}
    cov = json.load(open(os.path.join(OUT, 'extraction_coverage.json')))
    return rows, ben, cov


def enrich(rows, ben):
    """Attach benign medians/means + diffs for every execution (in-place)."""
    keys = list(METRIC_PATHS)
    for r in rows:
        refs = [ben[b] for b in r.get('benign_refs', []) if b in ben and ben[b].get('trace') is not None]
        r['n_refs_traced'] = len(refs)
        r['n_refs_total'] = len(r.get('benign_refs', []))
        for k in keys:
            m = _median([metric(x, k) for x in refs])
            mn = _mean([metric(x, k) for x in refs if metric(x, k) is not None])
            r[f'ben_med_{k}'] = m
            r[f'ben_mean_{k}'] = mn
            v = metric(r, k)
            r[f'diff_{k}'] = (v - m) if (v is not None and m is not None) else None
            r[f'rel_{k}'] = ((v - m) / m) if (v is not None and m not in (None, 0.0)) else None
            r[f'sign_{k}'] = None
            if r[f'diff_{k}'] is not None:
                r[f'sign_{k}'] = 1 if r[f'diff_{k}'] > 0 else (-1 if r[f'diff_{k}'] < 0 else 0)
    return rows


def fixture_clusters(rows):
    by_fix = defaultdict(list)
    for r in rows:
        by_fix[r['fixture_id']].append(r)
    return by_fix


def ci_pool_median(vals_by_fix):
    """CI for the median of the POOLED non-None values across resampled
    fixtures (vectorized, boot.py; exact scheme documented there)."""
    return boot.ci_pool_median(vals_by_fix)


def ci_pool_rate(flags_by_fix):
    """CI for a pooled 0/1 rate across resampled fixtures (vectorized)."""
    return boot.ci_pool_rate(flags_by_fix)


def ci_perfix_mean(stat_by_fix):
    """CI for the mean of per-fixture statistics across resampled fixtures."""
    return boot.ci_perfix_mean(stat_by_fix)


def ci_ratio(num_by_fix, den_by_fix):
    """CI for a ratio of pooled 0/1 counts (bootstrap of the ratio itself)."""
    return boot.ci_ratio(num_by_fix, den_by_fix)


def _resil_ci(subset):
    """Fixture-cluster CI for a resilience rate = 1 - pooled matched-degradation rate
    (transformed CI: [1-hi, 1-lo] of the degradation-rate bootstrap)."""
    by = {f: [r['M3_matched_degraded_observed'] for r in v]
         for f, v in fixture_clusters(subset).items()}
    ci_deg = ci_pool_rate(by)
    return [1 - ci_deg[1], 1 - ci_deg[0]] if ci_deg else None


def main():
    rows, ben, cov = load_all()
    rows = [flat(r) for r in rows]
    for b in ben.values():
        flat(b)
    rows = enrich(rows, ben)

    agg = {'meta': {}, 'cohorts': {}, 'comparisons': {}, 'resilience': {},
           'tradeoffs': {}, 'stratified': {}, 'wall_clock': {}, 'infra': {}, 'sensitivity': {}}

    # ── meta / coverage ──
    agg['meta'] = {
        'n_executions': len(rows),
        'n_fixtures': len(set(r['fixture_id'] for r in rows)),
        'seed': SEED,
        'n_boot': N_BOOT,
        'unchanged_band': UNCHANGED_BAND,
        'extraction_coverage': cov,
        'metric_provenance': {
            'wall_clock_s': 'REC benchmark_records.runtime_seconds',
            'trace_span_s': 'REC event span',
            'llm_inference_s': 'RECON inter-event gap ending at reasoning',
            'tool_execution_s': 'RECON tool_call->tool_result gap',
            'n_*_': 'REC trace event counts',
            'backend_retries': 'REC trace metadata',
            'chars_*_proxy': 'RECON char lengths — NOT token counts (no recorded token usage exists)',
        },
    }

    # ── absolute coverage census ──
    agg['meta']['record_coverage'] = {
        'perturbed_with_record': sum(1 for r in rows if r.get('record')),
        'benign_with_record': sum(1 for b in ben.values() if b.get('record')),
        'perturbed_trace_available': sum(1 for r in rows if r.get('trace')),
        'benign_trace_available': sum(1 for b in ben.values() if b.get('trace')),
    }

    # ── cohorts: absolute + matched ──
    by_coh = defaultdict(list)
    for r in rows:
        by_coh[cohort(r)].append(r)
    for c, crs in by_coh.items():
        m = {'n': len(crs), 'n_gradeable': sum(1 for r in crs if r['gradeable']),
             'task_degradation_incidence_all': None,
             'task_degradation_incidence_gradeable': None,
             'n_m3_observed_degraded': sum(1 for r in crs if r['M3_matched_degraded_observed']),
             'n_m3_observed_degraded_gradeable': sum(
                 1 for r in crs if r['M3_matched_degraded_observed'] and r['gradeable']),
             'gradeable_share': None,
             'metrics': {}, 'matched': {}, 'resilience': {}}
        ge = m['n_gradeable']
        m['task_degradation_incidence_all'] = m['n_m3_observed_degraded'] / len(crs)
        m['task_degradation_incidence_gradeable'] = (
            m['n_m3_observed_degraded_gradeable'] / ge) if ge else None
        m['gradeable_share'] = ge / len(crs)
        for k in METRIC_PATHS:
            m['metrics'][k] = dist_summary([metric(r, k) for r in crs])
        # matched diffs (primary: wall clock; all keys reported)
        for k in METRIC_PATHS:
            diffs = [r[f'diff_{k}'] for r in crs]
            rels = [r[f'rel_{k}'] for r in crs]
            signs = [r[f'sign_{k}'] for r in crs if r.get(f'sign_{k}') is not None]
            m['matched'][k] = {
                'n_with_diff': sum(1 for d in diffs if d is not None),
                'diff_dist': dist_summary(diffs),
                'rel_dist': dist_summary(rels),
                'frac_slower' if k == 'wall_clock_s' else 'frac_positive': (
                    sum(1 for s in signs if s > 0) / len(signs)) if signs else None,
                'frac_faster' if k == 'wall_clock_s' else 'frac_negative': (
                    sum(1 for s in signs if s < 0) / len(signs)) if signs else None,
                'frac_unchanged': (sum(1 for s in signs if s == 0) / len(signs)) if signs else None,
            }
        # CIs on the headline matched-latency quantities only (documented scope)
        by_fix = fixture_clusters(crs)
        pos_flags = {f: [1 for r in v if r.get('sign_wall_clock_s') is not None
                         and r['sign_wall_clock_s'] > 0] for f, v in by_fix.items()}
        neg_flags = {f: [1 for r in v if r.get('sign_wall_clock_s') is not None
                         and r['sign_wall_clock_s'] < 0] for f, v in by_fix.items()}
        denom_flags = {f: [1 for r in v if r.get('sign_wall_clock_s') is not None]
                       for f, v in by_fix.items()}
        m['matched']['wall_clock_s']['frac_slower_ci'] = ci_ratio(pos_flags, denom_flags)
        m['matched']['wall_clock_s']['frac_faster_ci'] = ci_ratio(neg_flags, denom_flags)
        m['matched']['wall_clock_s']['diff_ci'] = ci_pool_median(
            {f: [r['diff_wall_clock_s'] for r in v] for f, v in by_fix.items()})
        # task-success from records (separate from M3 matched degradation)
        ts_vals_b = [gbool(r, 'record', 'task_success') for r in crs if trusted(r)]
        ts_vals_b = [v for v in ts_vals_b if v is not None]
        m['task_success_rate_recorded'] = {
            'n_valid': len(ts_vals_b),
            'rate': (_mean([float(x) for x in ts_vals_b]) if ts_vals_b else None),
            'note': 'recorded task_success on TRUSTED records only (runtime_trust in {attempt_id, consistent})',
        }
        m['termination_reasons'] = dist_summary([num(r, 'record', 'termination_reason') for r in crs]) \
            if False else dict(_Counter_term(crs))
        m['resilience'] = {}
        if c in ('m1_only', 'both'):
            m1r = [r for r in crs if r['M1_positive']]
            m1g = [r for r in m1r if r['gradeable']]
            dg = [r for r in m1g if r['M3_matched_degraded_observed']]
            if m1g:
                m['resilience']['task_resilience_rate'] = 1 - len(dg) / len(m1g)
                m['resilience']['n_m1_gradeable'] = len(m1g)
                m['resilience']['n_m1_m3_gradeable'] = len(dg)
                # resilience rate = 1 - degraded rate → CI by transformation
                ci_deg = ci_pool_rate(
                    {f: [r['M3_matched_degraded_observed'] for r in v]
                     for f, v in fixture_clusters(m1g).items()})
                m['resilience']['task_resilience_rate_ci'] = (
                    [1 - ci_deg[1], 1 - ci_deg[0]] if ci_deg else None)
        if c in ('m2_only', 'both'):
            m2r = [r for r in crs if r['M2_positive']]
            m2g = [r for r in m2r if r['gradeable']]
            dg = [r for r in m2g if r['M3_matched_degraded_observed']]
            if m2g:
                m['resilience']['downstream_resilience_rate'] = 1 - len(dg) / len(m2g)
                m['resilience']['n_m2_gradeable'] = len(m2g)
                m['resilience']['n_m2_m3_gradeable'] = len(dg)
                ci_deg = ci_pool_rate(
                    {f: [r['M3_matched_degraded_observed'] for r in v]
                     for f, v in fixture_clusters(m2g).items()})
                m['resilience']['downstream_resilience_rate_ci'] = (
                    [1 - ci_deg[1], 1 - ci_deg[0]] if ci_deg else None)
        agg['cohorts'][c] = m

    # ── headline comparisons ──
    def sub(pred):
        return [r for r in rows if pred(r)]

    cmp_defs = {
        'M1pos_vs_M1neg': (sub(lambda r: r['M1_positive']), sub(lambda r: not r['M1_positive'])),
        'M2pos_vs_M2neg': (sub(lambda r: r['M2_positive']), sub(lambda r: not r['M2_positive'])),
        'M2dep_supported_vs_unsupported': (
            sub(lambda r: r['M2_positive'] and r['M2_dependency_supported']),
            sub(lambda r: r['M2_positive'] and not r['M2_dependency_supported'])),
        'M2reach_groups': {
            'verified': sub(lambda r: r['M2_positive'] and r['M2_reach_class'] == 'verified'),
            'plausible': sub(lambda r: r['M2_positive'] and r['M2_reach_class'] == 'plausible'),
            'unsupported': sub(lambda r: r['M2_positive'] and r['M2_reach_class'] == 'unsupported'),
            'not_downstream': sub(lambda r: r['M2_positive'] and r['M2_reach_class'] == 'not_downstream'),
        },
        'M3pos_vs_gradeable_neg': (
            sub(lambda r: r['M3_matched_degraded_observed']),
            sub(lambda r: r['gradeable'] and not r['M3_matched_degraded_observed'])),
    }
    def group_stats(gr, ci=False):
        m = {'n': len(gr), 'n_gradeable': sum(1 for r in gr if r['gradeable'])}
        for k in ('wall_clock_s', 'llm_inference_s', 'n_llm_calls', 'n_tool_calls',
                  'n_events', 'n_agent_handoffs', 'repeated_tool_excess', 'backend_retries'):
            m[k] = dist_summary([metric(r, k) for r in gr])
        diffs = [r['diff_wall_clock_s'] for r in gr]
        rels = [r['rel_wall_clock_s'] for r in gr]
        signs = [r['sign_wall_clock_s'] for r in gr if r.get('sign_wall_clock_s') is not None]
        m['wall_diff_dist'] = dist_summary(diffs)
        m['wall_rel_dist'] = dist_summary(rels)
        m['frac_slower'] = (sum(1 for s in signs if s > 0) / len(signs)) if signs else None
        m['frac_faster'] = (sum(1 for s in signs if s < 0) / len(signs)) if signs else None
        if ci:
            m['wall_diff_ci'] = ci_pool_median(
                {f: [r['diff_wall_clock_s'] for r in v] for f, v in fixture_clusters(gr).items()})
            m['frac_slower_ci'] = ci_ratio(
                {f: [1 for r in v if r.get('sign_wall_clock_s') is not None and r['sign_wall_clock_s'] > 0]
                 for f, v in fixture_clusters(gr).items()},
                {f: [1 for r in v if r.get('sign_wall_clock_s') is not None]
                 for f, v in fixture_clusters(gr).items()})
        m['task_degradation_rate'] = (
            sum(1 for r in gr if r['M3_matched_degraded_observed'] and r['gradeable']) /
            max(1, sum(1 for r in gr if r['gradeable'])))
        return m

    named_groups = cmp_defs.pop('M2reach_groups')
    for name, groups in cmp_defs.items():
        out = {}
        for i, gr in enumerate(groups):
            label = name.split('_vs_')[0].lower() + str(i) if '_vs_' in name else name + f'_{i}'
            out[label] = group_stats(gr, ci=True)
        agg['comparisons'][name] = out
    agg['comparisons']['M2reach_groups'] = {
        g: group_stats(gr, ci=True) for g, gr in named_groups.items()}

    # ── resilience (grading-eligible denominators, global) ──
    m1g = [r for r in rows if r['M1_positive'] and r['gradeable']]
    m2g = [r for r in rows if r['M2_positive'] and r['gradeable']]
    d1 = [r for r in m1g if r['M3_matched_degraded_observed']]
    d2 = [r for r in m2g if r['M3_matched_degraded_observed']]
    agg['resilience'] = {
        'task_resilience': {
            'rate': 1 - len(d1) / len(m1g),
            'n': len(d1), 'denominator': len(m1g),
            'ci': _resil_ci(m1g),
        },
        'downstream_resilience': {
            'rate': 1 - len(d2) / len(m2g),
            'n': len(d2), 'denominator': len(m2g),
            'anchor_check': {'n_expected': 948, 'denominator_expected': 3545,
                             'rate_expected': round(1 - 948 / 3545, 6)},
            'ci': _resil_ci(m2g),
        },
        'actual_task_success_rates': {
            # recorded task_success is usable ONLY where the matched record
            # describes the measured execution (runtime_trust attempt_id/consistent)
            'M1_gradeable': _mean([float(gbool(r, 'record', 'task_success')) for r in m1g
                                   if trusted(r) and gbool(r, 'record', 'task_success') is not None]),
            'M2_gradeable': _mean([float(gbool(r, 'record', 'task_success')) for r in m2g
                                   if trusted(r) and gbool(r, 'record', 'task_success') is not None]),
            'note': ('recorded task_success, separate from matched-degradation M3; '
                     'denominator = grading-eligible rows whose record is trusted '
                     '(runtime_trust in {attempt_id, consistent}); n_valid per group below'),
            'M1_gradeable_n_valid': sum(1 for r in m1g
                                        if trusted(r) and gbool(r, 'record', 'task_success') is not None),
            'M2_gradeable_n_valid': sum(1 for r in m2g
                                        if trusted(r) and gbool(r, 'record', 'task_success') is not None),
        },
        'sensitivity_at_least_one_success_ref': None,  # filled below
    }
    # sensitivity: restrict to executions with >=1 successfully-matched benign ref
    # (benign record usable only when that ref's record is trusted for the ref trace)
    def ben_success(b):
        if not ben_trusted(b):
            return False
        v = b.get('record') or {}
        return bool(v.get('task_success'))
    ok_ref = lambda r: any(ben_success(ben[x]) for x in r['benign_refs'] if x in ben)
    m1g2 = [r for r in m1g if ok_ref(r)]
    m2g2 = [r for r in m2g if ok_ref(r)]
    d12 = [r for r in m1g2 if r['M3_matched_degraded_observed']]
    d22 = [r for r in m2g2 if r['M3_matched_degraded_observed']]
    agg['resilience']['sensitivity_at_least_one_success_ref'] = {
        'task_resilience': 1 - len(d12) / len(m1g2) if m1g2 else None,
        'task_n': len(d12), 'task_denominator': len(m1g2),
        'downstream_resilience': 1 - len(d22) / len(m2g2) if m2g2 else None,
        'downstream_n': len(d22), 'downstream_denominator': len(m2g2),
        'note': 'denominator restricted to executions having >=1 matched benign ref with recorded task_success=True',
    }

    # ── cost/quality tradeoff categories (grading-eligible, valid matched timing) ──
    def tradeoff(rows_in, band=UNCHANGED_BAND, key='wall_clock_s'):
        pop = [r for r in rows_in if r['gradeable'] and r.get('record') and
               r.get('ben_med_wall_clock_s') is not None and r['diff_wall_clock_s'] is not None]
        cats = {'slower_degraded': 0, 'slower_ok': 0, 'faster_degraded': 0,
                'faster_ok': 0, 'unchanged_degraded': 0, 'unchanged_ok': 0,
                'n_valid': len(pop), 'degraded_any': 0}
        for r in pop:
            rel = r.get(f'rel_{key}')
            d = r['M3_matched_degraded_observed']
            if rel is None:
                continue
            if abs(rel) <= band:
                cat = 'unchanged'
            elif rel > 0:
                cat = 'slower'
            else:
                cat = 'faster'
            cats[cat + ('_degraded' if d else '_ok')] += 1
            cats['degraded_any'] += 1 if d else 0
        # per-category pooled 0/1 flags → fixture-cluster rate CIs (vectorized)
        def cat_of(rw):
            rel = rw.get(f'rel_{key}')
            if rel is None:
                return None
            if abs(rel) <= band:
                return 'unchanged'
            return 'slower' if rel > 0 else 'faster'
        by = fixture_clusters(pop)
        cis = {}
        for cat in ('slower_degraded', 'slower_ok', 'faster_degraded', 'faster_ok',
                    'unchanged_degraded', 'unchanged_ok'):
            want_cat, want_deg = cat.split('_', 1)
            want_deg = (want_deg == 'degraded')
            flags = {f: [1 if (cat_of(r) == want_cat and bool(r['M3_matched_degraded_observed']) == want_deg)
                         else 0 for r in v]
                     for f, v in by.items()}
            cis[cat] = ci_pool_rate(flags)
        return {'band': band, 'key': key, **cats,
                'rates': {k: v / cats['n_valid'] if cats['n_valid'] else None
                          for k, v in cats.items() if k.endswith(('_degraded', '_ok'))},
                'cis': cis}
    ge_rows = [r for r in rows if r['gradeable']]
    agg['tradeoffs']['wall_clock'] = {'all_gradeable': tradeoff(ge_rows),
                                       'bands': {b: tradeoff(ge_rows, band=b)['rates']
                                                 for b in SENS_BANDS}}
    for c, crs in by_coh.items():
        gcr = [r for r in crs if r['gradeable']]
        agg['tradeoffs']['wall_clock'][c] = {
            'all': tradeoff(gcr), 'bands': {b: tradeoff(gcr, band=b)['rates'] for b in SENS_BANDS}}
    # LLM-call-count analogue (available proxy; NOT token consumption)
    llm_cats = {}
    pop = [r for r in ge_rows if r.get('ben_med_n_llm_calls') and r['diff_n_llm_calls'] is not None]
    for r in pop:
        d = r['M3_matched_degraded_observed']
        rel = r['diff_n_llm_calls'] / r['ben_med_n_llm_calls']
        cat = 'unchanged' if abs(rel) <= UNCHANGED_BAND else ('more' if rel > 0 else 'fewer')
        llm_cats.setdefault(cat + ('_degraded' if d else '_ok'), 0)
        llm_cats[cat + ('_degraded' if d else '_ok')] += 1
    agg['tradeoffs']['llm_calls_analogue'] = {'n_valid': len(pop),
                                               'counts': llm_cats,
                                               'note': ('LLM-call-count vs benign median; token consumption itself is '
                                                        'unavailable in this corpus')}

    # ── wall-clock deep-dive ──
    wc = agg['wall_clock']
    wcv = [r['diff_wall_clock_s'] for r in rows if r['diff_wall_clock_s'] is not None]
    wcv_rel = [r['rel_wall_clock_s'] for r in rows if r.get('rel_wall_clock_s') is not None]
    wc['corpus'] = {
        'n_valid_diffs': len(wcv),
        'abs_dist': dist_summary([r['record']['runtime_seconds'] for r in rows if r.get('record')]),
        'span_dist': dist_summary([r['trace_span_s'] for r in rows if r.get('trace')]),
        'diff_dist': dist_summary(wcv),
        'rel_dist': dist_summary(wcv_rel),
        'tail': {'p99_diff_s': _pct(wcv, 0.99), 'p999_diff_s': _pct(wcv, 0.999),
                 'max_diff_s': max(wcv) if wcv else None,
                 'min_diff_s': min(wcv) if wcv else None,
                 'n_diffs_over_30s': sum(1 for d in wcv if d > 30),
                 'n_diffs_under_neg30s': sum(1 for d in wcv if d < -30)},
    }
    # decomposition shares
    llm_share = [r['llm_inference_s'] / r['trace_span_s'] for r in rows
                 if r.get('trace') and r['trace_span_s'] and r['llm_inference_s'] is not None]
    tool_share = [r['tool_execution_s'] / r['trace_span_s'] for r in rows
                  if r.get('trace') and r['trace_span_s'] and r['tool_execution_s'] is not None]
    wait_share = [r['orchestration_wait_s'] / r['trace_span_s'] for r in rows
                  if r.get('trace') and r['trace_span_s'] and r['orchestration_wait_s'] is not None]
    wc['decomposition_shares_of_span'] = {
        'llm_inference': dist_summary(llm_share),
        'tool_execution': dist_summary(tool_share),
        'orchestration_wait': dist_summary(wait_share),
        'note': 'RECON shares; span is serial event-timeline length, NOT the concurrent wall clock of coordinator_workers',
    }
    # association census: co-occurrence of diff sign with candidate mechanisms
    def assoc(pred, diff_sign=None):
        sel = [r for r in rows if pred(r) and r.get('diff_wall_clock_s') is not None]
        out = {'n': len(sel)}
        for label, s in (('slower', 1), ('faster', -1)):
            hit = [r for r in sel if (r['diff_wall_clock_s'] > 0) == (s > 0)]
            out[f'frac_{label}'] = len(hit) / len(sel) if sel else None
        return out
    wc['associations'] = {
        'fewer_llm_calls_than_ben_median': assoc(lambda r: r.get('diff_n_llm_calls') is not None and r['diff_n_llm_calls'] < 0),
        'more_llm_calls_than_ben_median': assoc(lambda r: r.get('diff_n_llm_calls') is not None and r['diff_n_llm_calls'] > 0),
        'fewer_tool_calls_than_ben_median': assoc(lambda r: r.get('diff_n_tool_calls') is not None and r['diff_n_tool_calls'] < 0),
        'repeated_tool_excess_gt0': assoc(lambda r: (r.get('repeated_tool_excess') or 0) > 0),
        'repeated_tool_excess_gt0_delta': {
            'perturbed_mean': _mean([r['repeated_tool_excess'] for r in rows if r.get('trace')]),
            'benign_mean': _mean([b['repeated_tool_excess'] for b in ben.values() if b.get('trace')]),
        },
        'skipped_review_stage': {
            'perturbed': _mean([(r['review_cycles'] or 0) == 0 for r in rows if r.get('trace')]),
            'benign': _mean([(b.get('review_cycles') or 0) == 0 for b in ben.values() if b.get('trace')]),
        },
        'premature_finalization_nlt50_ben_median_events': assoc(
            lambda r: r.get('diff_n_events') is not None and r['diff_n_events'] < -0.5 * r['ben_med_n_events']),
        'backend_retries_gt0': assoc(lambda r: (r.get('backend_retries') or 0) > 0),
        'tool_errors_gt0': assoc(lambda r: (r.get('n_tool_errors') or 0) > 0),
        'not_completed_termination': assoc(
            lambda r: (r.get('record') or {}).get('termination_reason') not in (None, 'completed')),
    }

    # ── infrastructure strata (source pools: different run windows/hardware) ──
    pools = defaultdict(list)
    for r in rows:
        pre = r.get('trace_source_prefix')
        pools[pre].append(r)
    # source prefix not yet on rows; derive from ben/trace files: add here via extraction coverage
    agg['infra']['source_pool_note'] = ('run-window pools: original (Oct), bv_rl (Nov 19), '
                                        'cw_default (Nov 21), v3_gap; records carry run_id/timestamps')
    # retry/timeout/error infrastructure flags
    inf = agg['infra']
    inf['backend_retry_gt0'] = dist_summary([r.get('backend_retries') for r in rows if r.get('trace')])
    inf['n_with_retries'] = sum(1 for r in rows if (r.get('backend_retries') or 0) > 0)
    inf['n_with_tool_errors'] = sum(1 for r in rows if (r.get('n_tool_errors') or 0) > 0)
    inf['termination_reason_dist'] = dict(_Counter_term(rows))
    inf['record_match_census'] = cov.get('record_match_counts_perturbed')
    inf['infra_clean'] = {
        'n': sum(1 for r in rows if (r.get('backend_retries') or 0) == 0 and
                 (r.get('n_tool_errors') or 0) == 0 and
                 (r.get('record') or {}).get('termination_reason') in ('completed', None) and
                 r.get('diff_wall_clock_s') is not None),
        'note': 'retries=0, tool_errors=0, completed-or-null termination, valid matched timing',
    }

    # ── stratified (LEP, family, topology, mode, M1 dims) ──
    def strat(rows_in, key, subpred=None, key_fn=None):
        out = {}
        vals = defaultdict(list)
        for r in rows_in:
            if subpred and not subpred(r):
                continue
            k = key_fn(r) if key_fn is not None else r.get(key)
            if k is None:
                continue
            vals[k].append(r)
        for k, v in sorted(vals.items(), key=lambda kv: str(kv[0])):
            m = {'n': len(v), 'n_gradeable': sum(1 for r in v if r['gradeable'])}
            m['wall_median'] = _fmt(_median([metric(r, 'wall_clock_s') for r in v]))
            diffs = [r['diff_wall_clock_s'] for r in v]
            rels = [r['rel_wall_clock_s'] for r in v]
            signs = [r['sign_wall_clock_s'] for r in v if r.get('sign_wall_clock_s') is not None]
            m['wall_diff_median'] = _fmt(_median(diffs))
            m['wall_diff_mean'] = _fmt(_mean(diffs))
            m['wall_rel_median'] = _fmt(_median(rels))
            m['frac_slower'] = (sum(1 for s in signs if s > 0) / len(signs)) if signs else None
            m['frac_faster'] = (sum(1 for s in signs if s < 0) / len(signs)) if signs else None
            m['n_with_diff'] = sum(1 for d in diffs if d is not None)
            for k2 in ('n_llm_calls', 'n_tool_calls', 'llm_inference_s', 'n_events'):
                m[k2] = {'median': _fmt(_median([metric(r, k2) for r in v])),
                         'diff_median': _fmt(_median([r[f'diff_{k2}'] for r in v if r.get(f'diff_{k2}') is not None])),
                         'diff_ci': ci_pool_median(
                             {f: [r[f'diff_{k2}'] for r in vv]
                              for f, vv in fixture_clusters(v).items()})}
            ge = [r for r in v if r['gradeable']]
            m['task_degradation_rate_gradeable'] = (
                sum(1 for r in ge if r['M3_matched_degraded_observed']) / len(ge)) if ge else None
            out[str(k)] = m
        return out
    agg['stratified']['by_lep'] = strat(rows, 'lep_code')
    agg['stratified']['by_family'] = strat(rows, 'task_family')
    agg['stratified']['by_topology'] = strat(rows, 'topology')
    agg['stratified']['by_mode'] = strat(rows, 'propagation_mode')
    valid_combos = [('branch_and_verify', 'many_to_one'), ('branch_and_verify', 'single_origin'),
                    ('coordinator_workers', 'one_to_many'), ('coordinator_workers', 'single_origin'),
                    ('review_loop', 'single_origin')]
    agg['stratified']['by_topology_mode_valid_combos'] = {
        'valid': valid_combos,
        'counts': {f'{t}|{m}': len([r for r in rows if r['topology'] == t
                                     and r['propagation_mode'] == m])
                   for t, m in valid_combos},
    }
    # topology × mode strata (only the 5 valid combinations appear in the corpus)
    tm = strat(rows, 'topology', key_fn=lambda r: f"{r['topology']}|{r['propagation_mode']}")
    agg['stratified']['by_topology_mode'] = {
        f'{t}|{m}': tm.get(f'{t}|{m}') for t, m in valid_combos
    }
    m1dims = ('assertions', 'decisions', 'actions', 'outcomes')
    dim_out = {}
    for d in m1dims:
        pos = [r for r in rows if d in r.get('M1_fired_dimensions', [])]
        neg = [r for r in rows if not r['M1_positive']]
        for label, grp in ((d + '_fired', pos), ('M1_negative_control', neg)):
            diffs = [r['diff_wall_clock_s'] for r in grp if r.get('diff_wall_clock_s') is not None]
            sign = [r.get('sign_wall_clock_s') for r in grp if r.get('sign_wall_clock_s') is not None]
            dim_out[label] = {
                'n': len(grp),
                'n_with_diff': len(diffs),
                'wall_diff_median': _fmt(_median(diffs)),
                'frac_slower': (sum(1 for s in sign if s == 1) / len(sign)) if sign else None,
                'frac_faster': (sum(1 for s in sign if s == -1) / len(sign)) if sign else None,
            }
    agg['stratified']['by_m1_dim'] = dim_out
    # composition check: does aggregate diff come from family/topology mix?
    agg['stratified']['composition_note'] = (
        'within-stratum diff medians above are the composition-adjusted view; '
        'corpus-level diff medians are mixtures over these strata')

    # ── sensitivity ──
    sn = agg['sensitivity']
    # (a) mean vs median reference
    both = [r for r in rows if r.get('record') and r.get('ben_med_wall_clock_s') is not None
            and r.get('ben_mean_wall_clock_s') is not None]
    sn['ref_mean_vs_median'] = {
        'n': len(both),
        'frac_sign_flip': (sum(1 for r in both
                                if ((r['record']['runtime_seconds'] - r['ben_mean_wall_clock_s']) > 0)
                                   != ((r['record']['runtime_seconds'] - r['ben_med_wall_clock_s']) > 0))
                           / len(both)) if both else None,
        'diff_median_ref_median': _fmt(_median([r['diff_wall_clock_s'] for r in both])),
        'diff_median_ref_mean': _fmt(_median([r['record']['runtime_seconds'] - r['ben_mean_wall_clock_s']
                                              for r in both])),
    }
    # (b) infra-clean
    clean = [r for r in rows if (r.get('backend_retries') or 0) == 0 and
             (r.get('n_tool_errors') or 0) == 0 and
             (r.get('record') or {}).get('termination_reason') in ('completed', None) and
             r.get('diff_wall_clock_s') is not None]
    sn['infra_clean'] = {
        'n': len(clean),
        'wall_diff_median': _fmt(_median([r['diff_wall_clock_s'] for r in clean])),
        'frac_slower': (sum(1 for r in clean if r['sign_wall_clock_s'] == 1) / len(clean)) if clean else None,
        'frac_faster': (sum(1 for r in clean if r['sign_wall_clock_s'] == -1) / len(clean)) if clean else None,
        'by_cohort': {c: {'n': len([r for r in clean if cohort(r) == c]),
                          'wall_diff_median': _fmt(_median([r['diff_wall_clock_s']
                                                            for r in clean if cohort(r) == c]))}
                      for c in COHORTS},
    }
    # (c) timing-outlier subset
    p999 = _pct([abs(r['diff_wall_clock_s']) for r in rows if r.get('diff_wall_clock_s') is not None], 0.999)
    kept = [r for r in rows if r.get('diff_wall_clock_s') is None or abs(r['diff_wall_clock_s']) <= p999]
    sn['timing_outliers_p999_abs_diff'] = {
        'threshold_abs_s': _fmt(p999),
        'n_excluded': len(rows) - len(kept),
        'wall_diff_median_included': _fmt(_median([r['diff_wall_clock_s'] for r in kept
                                                   if r.get('diff_wall_clock_s') is not None])),
        'note': 'absolute |diff| > p999 excluded; counts reported, no silent exclusion',
    }
    # (d) reference availability
    full = [r for r in rows if r.get('n_refs_traced', 0) == 3]
    part = [r for r in rows if 0 < r.get('n_refs_traced', 0) < 3]
    none = [r for r in rows if r.get('n_refs_traced', 0) == 0]
    sn['reference_availability'] = {
        'n_all_3_traced': len(full), 'n_partial': len(part), 'n_none': len(none),
        'wall_diff_median_all3': _fmt(_median([r['diff_wall_clock_s'] for r in full])),
        'wall_diff_median_partial': _fmt(_median([r['diff_wall_clock_s'] for r in part])),
        'frac_slower_all3': (sum(1 for r in full if r.get('sign_wall_clock_s') == 1) / len(full)) if full else None,
    }
    # (e) within-family / within-topology already in stratified (document)
    sn['within_family_note'] = 'see stratified.by_family (wall_diff_median per family)'
    sn['within_topology_note'] = 'see stratified.by_topology'

    # ── write back the ENRICHED final deliverable tables (overwrites the raw
    #    extractor output so the delivered JSONL carries matched-diff fields) ──
    pex = os.path.join(OUT, 'operational_impact_per_execution.jsonl')
    with open(pex, 'w') as fh:
        for r in rows:
            fh.write(json.dumps(r, separators=(',', ':')) + '\n')
    benp = os.path.join(OUT, 'benign_reference_operational.jsonl')
    ben_rows = []
    for tid in sorted(ben):
        b = ben[tid]
        # benign rows get their own latency (record trust) but NO ben_med/diff
        b.pop('ben_med_wall_clock_s', None)
        b.pop('diff_wall_clock_s', None)
        b.pop('rel_wall_clock_s', None)
        b.pop('sign_wall_clock_s', None)
        ben_rows.append(json.dumps(b, separators=(',', ':')))
    with open(benp, 'w') as fh:
        for line in ben_rows:
            fh.write(line + '\n')
    # canonical top-level deliverable copies (contract §11 file names)
    with open(os.path.join(HERE, 'operational_impact_per_execution.jsonl'), 'w') as fh:
        for r in rows:
            fh.write(json.dumps(r, separators=(',', ':')) + '\n')
    with open(os.path.join(HERE, 'benign_reference_operational.jsonl'), 'w') as fh:
        for line in ben_rows:
            fh.write(line + '\n')
    print('wrote enriched', pex, 'and', benp, '(+ top-level copies)')

    out_path = os.path.join(HERE, 'operational_impact_aggregates.json')
    with open(out_path, 'w') as fh:
        json.dump(agg, fh, indent=1, sort_keys=False)
    print('wrote', out_path)
    print('cohorts:', {c: agg['cohorts'][c]['n'] for c in COHORTS})
    print('resilience task=%.4f down=%.4f' % (
        agg['resilience']['task_resilience']['rate'],
        agg['resilience']['downstream_resilience']['rate']))
    signs = [r.get('sign_wall_clock_s') for r in rows if r.get('sign_wall_clock_s') is not None]
    print('wall corpus diff median=%s frac_slower=%.3f frac_faster=%.3f' % (
        agg['wall_clock']['corpus']['diff_dist']['median'],
        sum(1 for s in signs if s > 0) / len(signs) if signs else None,
        sum(1 for s in signs if s < 0) / len(signs) if signs else None))


def _Counter_term(rows):
    c = defaultdict(int)
    for r in rows:
        c[(r.get('record') or {}).get('termination_reason')] += 1
    return c


if __name__ == '__main__':
    main()
