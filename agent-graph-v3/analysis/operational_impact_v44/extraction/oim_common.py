#!/usr/bin/env python3.11
"""Shared helpers for the V4.4 operational-impact analysis (deterministic).

Conventions (canonical — see extract_operational.py docstring for REC/RECON
classification of every metric):
  * primary unit = individual perturbed execution vs its matched benign references
    (identities REUSED from the frozen V4.4 ledger, never re-matched).
  * benign reference statistic = MEDIAN across available refs (primary); MEAN
    as named sensitivity.
  * independent observation unit = fixture (or fixture×topology cell where a
    fixture appears under multiple topologies). Bootstrap = fixture-cluster.
  * no metric ever treats missing as zero.
"""
from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, 'out')
SEED = 20270301          # same pinned seed as the V4.4 fixture-cluster bootstrap
N_BOOT = 4000

# metric accessors: (row, side) -> float|None ; side in {'lep','ben'}
# 'ben' uses the row's benign aggregate (median/mean) computed by callers.
METRIC_DEFS: Dict[str, Dict[str, Any]] = {
    # name: {source: REC|RECON|N/A, getter: callable(row, key=None)->value}
}


def g(row: Dict[str, Any], dotted: str) -> Any:
    cur: Any = row
    for p in dotted.split('.'):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def num(row: Dict[str, Any], dotted: str) -> Optional[float]:
    v = g(row, dotted)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return None


def record(row: Dict[str, Any], field: str) -> Optional[float]:
    """Recorded metric from the matched benchmark record (M0/M-record fields)."""
    return num(row, f'record.{field}')


def trace(row: Dict[str, Any], field: str) -> Optional[float]:
    """Trace-derived metric (REC from trace events / RECON from gaps)."""
    return num(row, f'trace.{field}')


# ── metric table ─────────────────────────────────────────────────────────────
# Each entry: (display name, category REC/RECON, key path(s))
METRICS: List[Tuple[str, str, List[str]]] = [
    # (key, kind, [field paths in row])
    ('wall_clock_s',          'REC',   ['record.runtime_seconds']),
    ('trace_span_s',          'REC',   ['trace.trace_span_s']),
    ('llm_inference_s',       'RECON', ['trace.llm_inference_s_reconstructed']),
    ('tool_execution_s',      'RECON', ['trace.tool_execution_s_reconstructed']),
    ('orchestration_wait_s',  'RECON', ['trace.orchestration_wait_s_reconstructed']),
    ('n_events',              'REC',   ['trace.n_events']),
    ('n_llm_calls',          'REC',   ['trace.n_reasoning']),
    ('n_tool_calls',        'REC',   ['trace.n_tool_calls']),
    ('n_tool_results',      'REC',   ['trace.n_tool_results']),
    ('n_tool_errors',       'REC',   ['trace.n_tool_errors']),
    ('n_agent_handoffs',    'REC',   ['trace.n_agent_handoffs']),
    ('n_topology_transitions', 'REC',['trace.n_topology_transitions']),
    ('n_memory_ops',        'REC',   ['trace.n_memory_write', 'trace.n_memory_retrieval']),
    ('n_finalization',      'REC',   ['trace.n_finalization_handoff']),
    ('dup_tool_groups',     'RECON', ['trace.duplicate_tool_call_groups']),
    ('repeated_tool_excess','RECON', ['trace.repeated_tool_call_excess']),
    ('backend_retries',     'REC',   ['trace.metadata.backend_retry_count']),
    ('chars_in_proxy',      'RECON', ['trace.chars_input_reasoning_proxy']),
    ('chars_out_proxy',     'RECON', ['trace.chars_output_reasoning_proxy']),
    ('n_stages_completed',  'REC',   ['trace.metadata.stage_count']),
    ('review_cycles',       'REC',   ['trace.metadata.review_cycle_count']),
]
# NOTE: token consumption is NOT in this table — it is unavailable in this
# corpus (no recorded token fields; see TELEMETRY_AUDIT.md).

METRIC_INDEX = {k: (kind, paths) for k, kind, paths in METRICS}


def metric_value(row: Dict[str, Any], key: str) -> Optional[float]:
    kind, paths = METRIC_INDEX[key]
    if key == 'n_memory_ops':
        a = num(row, paths[0]); b = num(row, paths[1])
        if a is None and b is None:
            return None
        return (a or 0.0) + (b or 0.0)
    if key == 'n_stages_completed':
        v = num(row, paths[0])
        if v is None:
            rec = record(row, 'num_stages')
            return rec
        return v
    return num(row, paths[0])


def record_field(row: Dict[str, Any], field: str) -> Optional[float]:
    return num(row, f'record.{field}')


def benign_median(rows: List[Dict[str, Any]], key: str) -> Optional[float]:
    vals = [metric_value(r, key) for r in rows]
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    vals.sort()
    n = len(vals)
    if n % 2 == 1:
        return vals[n // 2]
    return (vals[n // 2 - 1] + vals[n // 2]) / 2.0


def benign_mean(rows: List[Dict[str, Any]], key: str) -> Optional[float]:
    vals = [v for v in (metric_value(r, key) for r in rows) if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


# ── cohorts (frozen counts asserted by callers) ─────────────────────────────
def cohort_of(row: Dict[str, Any]) -> str:
    m1 = row['M1_positive']
    m2 = row['M2_positive']
    if not m1 and not m2:
        return 'neither'
    if m1 and not m2:
        return 'm1_only'
    if m2 and not m1:
        return 'm2_only'
    return 'both'


# ── statistics ───────────────────────────────────────────────────────────────
def median(xs: List[float]) -> Optional[float]:
    if not xs:
        return None
    xs = sorted(xs)
    n = len(xs)
    if n % 2 == 1:
        return xs[n // 2]
    return (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def mean(xs: List[float]) -> Optional[float]:
    if not xs:
        return None
    return sum(xs) / len(xs)


def pct(xs: List[float], q: float) -> Optional[float]:
    """Linear-interpolated percentile (numpy 'linear' convention)."""
    if not xs:
        return None
    xs = sorted(xs)
    if len(xs) == 1:
        return xs[0]
    idx = q * (len(xs) - 1)
    lo = int(math.floor(idx))
    hi = min(lo + 1, len(xs) - 1)
    frac = idx - lo
    return xs[lo] + (xs[hi] - xs[lo]) * frac


def quantile_ci(xs: List[float], q: float, boot_fn, n_boot: int = N_BOOT,
                seed: int = SEED) -> Tuple[Optional[float], Optional[float]]:
    """Fixture-cluster bootstrap CI for a quantile.

    xs: per-fixture statistic list ; boot_fn(stat_by_fixture, rng) -> scalar.
    """
    import random
    rng = random.Random(seed)
    fixtures = list(xs.keys()) if isinstance(xs, dict) else None
    if fixtures is None:
        return (None, None)
    n = len(fixtures)
    ests = []
    for _ in range(n_boot):
        picks = [rng.randrange(n) for _ in range(n)]
        sub = [fixtures[i] for i in picks]
        try:
            v = boot_fn({f: xs[f] for f in set(sub)}, rng)
        except (ZeroDivisionError, KeyError):
            continue
        if v is not None:
            ests.append(v)
    if not ests:
        return (None, None)
    ests.sort()
    return (pct(ests, 0.025), pct(ests, 0.975))


def cluster_mean_ci(values_by_fixture: Dict[str, List[float]],
                    statistic: str = 'median', n_boot: int = N_BOOT,
                    seed: int = SEED, alpha: float = 0.05
                    ) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Fixture-cluster bootstrap CI for the mean/median of per-execution values.

    values_by_fixture: fixture -> list of execution-level values.
    Returns (point, lo, hi). Point = cluster-weighted (equal fixture weight)
    statistic of per-execution values pooled across fixtures.
    """
    import random
    rng = random.Random(seed)
    fixtures = [f for f, v in values_by_fixture.items() if v]

    def stat(pooled: List[float]) -> Optional[float]:
        return median(pooled) if statistic == 'median' else mean(pooled)

    allv = [v for f in fixtures for v in values_by_fixture[f]]
    point = stat(allv)
    if not fixtures:
        return (None, None, None)
    n = len(fixtures)
    ests = []
    for _ in range(n_boot):
        picks = [rng.randrange(n) for _ in range(n)]
        pooled = []
        for i in picks:
            pooled.extend(values_by_fixture[fixtures[i]])
        if not pooled:
            continue
        v = stat(pooled)
        if v is not None:
            ests.append(v)
    if not ests:
        return (point, None, None)
    ests.sort()
    lo = ests[int(0.025 * len(ests))] if len(ests) >= 40 else ests[0]
    hi = ests[min(int(0.975 * len(ests)), len(ests) - 1)]
    return (point, lo, hi)


# ── loaders ──────────────────────────────────────────────────────────────────
def load_per_execution() -> List[Dict[str, Any]]:
    p = os.path.join(OUT, 'operational_impact_per_execution.jsonl')
    return [json.loads(l) for l in open(p) if l.strip()]


def load_benign() -> Dict[str, Dict[str, Any]]:
    p = os.path.join(OUT, 'benign_reference_operational.jsonl')
    return {json.loads(l)['trace_id']: json.loads(l) for l in open(p) if l.strip()}


def attach_benign_aggregates(rows: List[Dict[str, Any]],
                             benign: Dict[str, Dict[str, Any]],
                             keys: List[str]) -> List[Dict[str, Any]]:
    """For each perturbed row, attach ben_median / ben_mean per metric over
    available refs, plus reference availability. Idempotent."""
    for r in rows:
        refs = [benign[b] for b in r.get('benign_refs', []) if b in benign]
        refs = [b for b in refs if b.get('trace') is not None]
        r['n_refs_traced'] = len(refs)
        r['n_refs_total'] = len(r.get('benign_refs', []))
        for k in keys:
            med = benign_median(refs, k)
            r[f'ben_median_{k}'] = med
            r[f'ben_mean_{k}'] = benign_mean(refs, k)
        for k in keys:
            v = metric_value(r, k)
            bm = r.get(f'ben_median_{k}')
            r[f'diff_{k}'] = (v - bm) if (v is not None and bm is not None) else None
            r[f'rel_diff_{k}'] = ((v - bm) / bm) if (v is not None and bm not in (None, 0.0)) else None
            r[f'slower_than_ben_median_{k}'] = (
                v > bm) if (v is not None and bm is not None) else None
    return rows


if __name__ == '__main__':
    # quick self-check on extracted data
    rows = load_per_execution()
    print('rows:', len(rows))
    print('rows with trace:', sum(1 for r in rows if r.get('trace')))
    print('rows with record:', sum(1 for r in rows if r.get('record')))
    ben = load_benign()
    print('benign refs:', len(ben), 'traced:', sum(1 for b in ben.values() if b.get('trace')))
