#!/usr/bin/env python3.11
"""
Production analysis over all eligible consolidated standard-execution traces
with the SELECTED implementation's ledger (brief §7 / MEASUREMENT_SPEC §11).

Read-only over the repo; writes out/production_*.json + results/ only.
Inputs:
  * out/per_trace_<impl>.jsonl      — the selected implementation's frame ledger
  * analysis/consolidated_20261121_bnd_v41/bnd_v41_run/bnd_v41_per_trace.jsonl
                                     (frozen ledger; M2/M3/M4-historical fields)
  * out/per_trace_v6.jsonl, out/per_trace_v7*.jsonl (cross-impl disagreement)
  * out/sample_A_final.jsonl (design weights, when present)

Outputs:
  out/production_summary.json       — every headline metric, machine-readable
  out/production_breakdowns.json   — 108-cell family x LEP x topology x mode
  out/production_unavailable.json   — unavailable-measurement accounting
  out/production_cis.json          — Wilson + fixture-cluster-bootstrap CIs
  results/production_report.md      — publication-ready results summary

Definitions reused (brief requirement: "existing propagation definitions"):
  M1 = union divergence of the selected implementation (per row `union`).
  M2 = cross-agent downstream deviation: the frame's injection-boundary-gated,
       role-gated flag (recomputed by the selected extractor's stage gate is a
       separate variant; here we report the frame field, and candspec's own
       stage-gate result where available under distinct keys).
  M3 = dependency-supported: reach class verified/plausible.
  M4-historical = frame flag m4_task_failure; M4-matched = this ledger's
       five-way class 'degraded' (LEP failure AND >=1 compatible benign
       reference graded correct) — associational, NOT causal (spec §0.2.8).
"""
from __future__ import annotations

import json
import math
import os
import random
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import common

#: the frame's propagation fields (M2/M3/M4-historical) live in the
#: consolidated downstream per-trace ledger — the "existing propagation
#: definitions" input.  The raw bnd_v41_run ledger is the comparability
#: source only.
FRAME_FIELDS = os.path.join(common.CONS, 'downstream_results',
                            'per_trace.jsonl')
FROZEN_LEDGER = common.CONS_LEDGER
FAM = ('code_review', 'financial_analysis', 'research_synthesis')
LEP = ('LEP_INDIRECT_PROMPT_INJECTION', 'LEP_HANDOFF_CORRUPTION',
       'LEP_INPUT_DISREGARD', 'LEP_TOOL_RESULT_CORRUPTION')
TOPO = ('branch_and_verify', 'coordinator_workers', 'review_loop')
MODE = ('many_to_one', 'one_to_many', 'single_origin')
DIMS = common.DIMS


# ── CI machinery ─────────────────────────────────────────────────────────────
def wilson(k: int, n: int, z: float = 1.959964) -> Optional[List[float]]:
    if n <= 0:
        return None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [max(0.0, c - m), min(1.0, c + m)]


def cluster_bootstrap_ci(fixtures: Dict[str, List[float]],
                          n_iter: int = 2000,
                          seed: int = 20261207,
                          weight_by_row: Optional[Dict] = None
                          ) -> List[float]:
    """Fixture-cluster percentile bootstrap of a row-value mean (design-
    weighted when weights are supplied; otherwise plain)."""
    rng = random.Random(seed)
    keys = sorted(fixtures)
    if len(keys) < 5:
        vals = [v for row in fixtures.values() for v in row]
        return [min(vals), max(vals)] if vals else [0.0, 0.0]
    tot_w = {k: sum(weight_by_row.get(str(i), 1.0) for i in fixtures[k])
             for k in keys} if weight_by_row else {k: len(fixtures[k]) for k in keys}
    W = sum(tot_w.values())
    ests = []
    for _ in range(n_iter):
        Wb, Sb = 0.0, 0.0
        for k in keys:
            if rng.random() <= (tot_w[k] / W):      # cluster resampled w.p. share
                Wb += tot_w[k]
                Sb += sum((weight_by_row.get(str(i), 1.0) * v
                           if weight_by_row else v)
                          for i, v in enumerate(fixtures[k])
                          for _ in [0])
        ests.append(Sb / Wb if Wb else 0.0)
    ests.sort()
    # point estimate over the full set (plain mean when unweighted)
    num = (sum((weight_by_row.get(str(i), 1.0) * v)
               for k in keys for i, v in enumerate(fixtures[k]))
           if weight_by_row else
           sum(v for k in keys for v in fixtures[k]))
    den = (sum(weight_by_row.get(str(i), 1.0)
               for k in keys for i in range(len(fixtures[k])))
           if weight_by_row else W)
    point = num / den if den else 0.0
    return [ests[int(0.025 * (n_iter - 1))],
            ests[int(0.975 * (n_iter - 1))], point]


# ── loaders ───────────────────────────────────────────────────────────────────
def load_ledger(path: str) -> List[Dict[str, Any]]:
    return [json.loads(l) for l in open(path) if l.strip()]


def load_frozen_flags() -> Dict[str, Dict[str, Any]]:
    """Per-trace frame fields, joined across the two frame ledgers:
    comparability from bnd_v41_run, the M2/M3/M4-historical propagation
    fields from downstream_results (the 'existing propagation
    definitions' input)."""
    out: Dict[str, Dict[str, Any]] = {}
    for l in open(FROZEN_LEDGER):
        d = json.loads(l)
        out[d['trace_id']] = d
    _PROP_FIELDS = ('m2_cross_agent_positive', 'm2_nonzero_threshold',
                    'm2_scope', 'm3_reach_class', 'm4_task_failure',
                    'm1_whole_trace_positive', 'delivery_verified',
                    'origin_roles', 'evaluable', 'task_grade')
    for l in open(FRAME_FIELDS):
        d = json.loads(l)
        out.setdefault(d['trace_id'], {}).update(
            {k: d.get(k) for k in _PROP_FIELDS})
    return out


def cell_key(r: Dict) -> str:
    return f"{r.get('task_family')}/{r.get('lep_code')}/" \
           f"{r.get('topology')}/{r.get('propagation_mode')}"


def all_cells() -> List[str]:
    cells = []
    for f in FAM:
        for l in LEP:
            for t in TOPO:
                for m in MODE:
                    cells.append(f'{f}/{l}/{t}/{m}')
    return cells


# ── core ──────────────────────────────────────────────────────────────────────
def analyze(impl_name: str, ledger_path: str,
            cross_ledgers: Optional[List[tuple]] = None) -> Dict[str, Any]:
    rows = load_ledger(ledger_path)
    frozen = load_frozen_flags()

    def fr(tid: str) -> Dict[str, Any]:
        return frozen.get(tid) or {}

    # ── denominator accounting (P2 / P1 / P0 + frame reasons) ─────────────
    n_rows = len(rows)
    comparable = sum(1 for r in rows if (fr(r['trace_id']).get('comparable')
                                          is True))
    denom = {'n_ledger_rows': n_rows, 'n_comparable_frame': comparable,
             'note': 'ledger covers the P1 frame; frame comparability is the '
                     'frozen gate; P0/P2/P3 sizes are re-derived in '
                     'corpus_inventory()'}

    # ── per-dimension + union ───────────────────────────────────────────────
    dim_counts = Counter()
    dim_eval = Counter()
    dim_unknown = Counter()
    dim_zero_only = Counter()
    trigger_combos = Counter()
    overlap = Counter()
    union_pos = 0
    for r in rows:
        pd = r.get('per_dimension') or {}
        u = r.get('union') or {}
        trig = set(u.get('triggered_dimensions') or [])
        if u.get('divergent'):
            union_pos += 1
            trigger_combos[','.join(u.get('triggered_dimensions') or [])] += 1
        for d in DIMS:
            e = pd.get(d) or {}
            if e.get('evaluable'):
                dim_eval[d] += 1
            else:
                dim_unknown[d] += 1
            if e.get('divergent'):
                dim_counts[d] += 1
                if e.get('zero_threshold'):
                    dim_zero_only[d] += 1
        for i, a in enumerate(DIMS):
            for b in DIMS[i + 1:]:
                if a in trig and b in trig:
                    overlap[f'{a}+{b}'] += 1
    zero_only_pos = sum(
        1 for r in rows
        if (r.get('union') or {}).get('divergent') and
        len((r.get('union') or {}).get('zero_threshold_triggered') or [])
        == len((r.get('union') or {}).get('triggered_dimensions') or []))

    m1 = union_pos
    dim_rate = {d: (dim_counts[d] / n_rows, dim_counts[d]) for d in DIMS}

    # ── M2 / M3 / M4 (frame definitions; associational) ────────────────────
    m2_all = sum(1 for r in rows if fr(r['trace_id']).get(
        'm2_cross_agent_positive'))
    m2_nz = sum(1 for r in rows
                if fr(r['trace_id']).get('m2_cross_agent_positive') and
                fr(r['trace_id']).get('m2_nonzero_threshold'))
    m3_supp = [r for r in rows if (fr(r['trace_id']).get('m3_reach_class')
                                   in ('verified', 'plausible'))]
    m3_ver = sum(1 for r in m3_supp
                 if fr(r['trace_id']).get('m3_reach_class') == 'verified')
    m4_hist = sum(1 for r in rows if fr(r['trace_id']).get('m4_task_failure'))
    m4_classes = Counter(r.get('m4_class') for r in rows)
    m4_matched = sum(1 for r in m3_supp if r.get('m4_matched'))
    m4_matched_den = len(m3_supp)

    # ── benign no-LEP failure baseline (recomputed, per family) ────────────
    # Grade vocabulary: the frame ledgers carry the RAW grader vocabulary
    # {correct, incorrect, ungradeable}; the normalized m4_detail copy maps
    # 'incorrect' -> 'failure'.  A literal g=='failure' match against the
    # raw field silently gives 0 (string-mismatch), which mis-reported the
    # baseline as 0.0% (audit finding, agentprop-final verification).  Both
    # vocabularies' failure value are counted; verified per-family against
    # m4_detail.n_benign_failure (code 1293/fin 2662/res 9519, total 13474
    # / 39058 graded = 34.50%).
    ben_fail: Dict[str, List[int]] = defaultdict(list)
    ben_graded: Dict[str, List[int]] = defaultdict(list)
    for r in rows:
        fam = r.get('task_family')
        for g in (r.get('benign_grades') or []):
            if g == 'ungradeable':
                continue
            ben_graded[fam].append(1)
            ben_fail[fam].append(1 if g in ('failure', 'incorrect') else 0)
    ben_baseline = {f: (sum(ben_fail.get(f, [0])) /
                       sum(1 for _ in ben_graded.get(f, [1])),
                        sum(ben_fail.get(f, [0])), len(ben_graded.get(f, [0])))
                   for f in FAM}

    # ── breakdowns: 108 cells ───────────────────────────────────────────────
    cells: Dict[str, Dict] = {}
    by_cell = defaultdict(list)
    for r in rows:
        by_cell[cell_key(r)].append(r)
    for c in all_cells():
        rs = by_cell.get(c, [])
        cells[c] = {
            'n': len(rs),
            'm1': sum(1 for r in rs if (r.get('union') or {}).get('divergent')),
            'm2': sum(1 for r in rs if fr(r['trace_id']).get(
                'm2_cross_agent_positive')),
            'm3_supported': sum(1 for r in rs if (fr(r['trace_id']).get(
                'm3_reach_class') in ('verified', 'plausible'))),
            'm4_matched': sum(1 for r in rs if r.get('m4_matched')),
            'dims': {d: sum(1 for r in rs
                            if ((r.get('per_dimension') or {}).get(d) or {})
                            .get('divergent')) for d in DIMS},
        }
    empty_cells = [c for c in all_cells() if not by_cell.get(c)]

    # ── cross-implementation disagreement (when ledgers provided) ─────────
    cross: Dict[str, Any] = {}
    for name, path in (cross_ledgers or []):
        if not os.path.exists(path):
            cross[name] = {'available': False}
            continue
        other = {r['trace_id']: r for r in load_ledger(path)}
        mine = {r['trace_id']: bool(r.get('m1_union')) for r in rows}
        common_ids = [t for t in mine if t in other]
        mism = sum(1 for t in common_ids
                   if bool(other[t].get('m1_union')) != mine[t])
        cross[name] = {'n_common': len(common_ids), 'm1_disagreements': mism,
                       'rate': (mism / len(common_ids)) if common_ids else None}

    # ── unavailable-measurement accounting (spec §2) ────────────────────────
    unavail = {
        'per_dimension': {d: {
            'evaluable': dim_eval.get(d, 0),
            'not_evaluable': dim_unknown.get(d, 0),
            'zero_threshold_rows': sum(
                1 for r in rows
                if ((r.get('per_dimension') or {}).get(d) or {})
                .get('zero_threshold')),
        } for d in DIMS},
        'union_positive': m1,
        'union_positive_zero_threshold_only': zero_only_pos,
        'm4_not_m3_supported': n_rows - len(m3_supp),
        'gradeability': {},
    }
    for fam in FAM:
        g = [r for r in rows if r.get('task_family') == fam]
        gg = [r for r in g if r.get('lep_grade') != 'ungradeable']
        unavail['gradeability'][fam] = {
            'n_rows': len(g), 'n_lep_gradeable': len(gg),
            'rate': (len(gg) / len(g)) if g else None,
        }

    # ── CIs ─────────────────────────────────────────────────────────────────
    cis: Dict[str, Any] = {}
    cis['m1'] = {'point': m1 / n_rows, 'wilson95': wilson(m1, n_rows),
                 'n': n_rows}
    fix: Dict[str, List[float]] = defaultdict(list)
    wmap: Dict[str, float] = {}
    try:
        sa = load_ledger(os.path.join(common.OUT, 'sample_A_final.jsonl'))
        for r in sa:
            wmap[str((r.get('stratum') or '', r['trace_id']))] = \
                r.get('design_weight', 1.0)
    except FileNotFoundError:
        sa = []
    for r in rows:
        fix[common.fixture_id_of(r['trace_id'])].append(
            1.0 if (r.get('union') or {}).get('divergent') else 0.0)
    lo, hi, pt = cluster_bootstrap_ci(fix, seed=20261207)
    cis['m1_fixture_cluster_bootstrap95'] = [lo, hi, pt]
    for d in DIMS:
        k = dim_counts.get(d, 0)
        cis[d] = {'point': k / n_rows, 'wilson95': wilson(k, n_rows),
                  'den': n_rows}
    cis['m4_matched'] = {'point': m4_matched / m4_matched_den,
                         'wilson95': wilson(m4_matched, m4_matched_den),
                         'denominator': 'M3-supported', 'n': m4_matched_den}
    for fam, (rate, k, n) in ben_baseline.items():
        cis[f'benign_failure_{fam}'] = {'point': rate,
                                        'wilson95': wilson(k, n),
                                        'n_graded': n,
                                        'note': 'benign no-LEP grade rates, '
                                                'per family; context only, '
                                                'never a causal claim'}

    summary = {
        'implementation': impl_name,
        'ledger': os.path.abspath(ledger_path),
        'denominators': denom,
        'behavioral_divergence': {
            'union_m1_count': m1,
            'union_m1_rate': m1 / n_rows,
            'dimension_rates': {d: dim_rate[d][0] for d in DIMS},
            'dimension_counts': {d: dim_rate[d][1] for d in DIMS},
            'dimension_evaluable': {d: dim_eval.get(d, 0) for d in DIMS},
            'dimension_unknown': {d: dim_unknown.get(d, 0) for d in DIMS},
            'dimension_zero_threshold_only': dict(dim_zero_only),
            'trigger_combinations_top': dict(trigger_combos.most_common(12)),
            'pairwise_overlaps_among_union_positives': dict(overlap),
            'zero_threshold_only_positives': zero_only_pos,
            'note': 'union = logical OR of the four per-dimension indicators; '
                    'no weighted composite. Missing dimensions are unknowns, '
                    'never negatives (spec §0.2.3).',
        },
        'downstream_propagation': {
            'M2_cross_agent_all': m2_all,
            'M2_cross_agent_nonzero_threshold': m2_nz,
            'M3_supported': len(m3_supp),
            'M3_verified': m3_ver,
            'M3_plausible': len(m3_supp) - m3_ver,
            'M4_historical': m4_hist,
            'M4_matched': m4_matched,
            'M4_matched_denominator': m4_matched_den,
            'M4_class_counts': dict(m4_classes),
            'note': 'M2/M3/M4-historical are the FRAME (frozen) definitions '
                    'reused verbatim; M4-matched is this implementation\'s '
                    'five-way class. All associational, not causal.',
        },
        'task_degradation': {
            'M4_matched': m4_matched,
            'M4_matched_rate_of_M3_supported':
                (m4_matched / m4_matched_den) if m4_matched_den else None,
            'M4_historical': m4_hist,
            'benign_failure_baseline': dict(
                (f, {'rate': b[0], 'k': b[1], 'n_graded': b[2]})
                for f, b in ben_baseline.items()),
            'note': 'M4-matched = LEP failure AND >=1 compatible benign '
                    'reference graded correct. Benign failures on the same '
                    'fixture are NOT perturbation-caused; persistent '
                    'failure is reported separately (M4 class counts).',
        },
        'confidence_intervals': cis,
        'cross_implementation_m1_disagreement': cross,
        'breakdowns': cells,
        'empty_cells': empty_cells,
        'unavailable_measurement_accounting': unavail,
    }
    return summary


def corpus_inventory() -> Dict[str, Any]:
    """P2/P1/P0/P3 sizes from the authoritative 20261121 consolidation."""
    p1 = load_ledger(FROZEN_LEDGER)
    n_p1 = len(p1)
    comp = sum(1 for r in p1 if r.get('comparable') is True)
    # P2: LEP standard-execution population = rows of the frame ledger +
    # non-comparable
    inv = {
        'P3_canonical_traces': 26965,
        'P3_benign': 9028, 'P3_lep': 17937,
        'P2_lep_standard': 17937,
        'P1_comparable_frame': comp,
        'P0_non_comparable': 17937 - comp,
        'frozen_ledger_rows': n_p1,
        'source': 'analysis/consolidated_20261121_bnd_v41 (AUDIT_REPORT.md '
                  'dedup accounting: 28,001 raw -> 26,965 accepted)',
        'memory_excluded': 'LEP_MEMORY_POISONING + memory_enabled executions '
                          'are outside every population above',
    }
    return inv


def run(impl_name: str = 'candspec',
        cross: Optional[List[tuple]] = None) -> Dict[str, Any]:
    ledger = os.path.join(common.OUT, f'per_trace_{impl_name}.jsonl')
    if not os.path.exists(ledger):
        raise SystemExit(f'no ledger for {impl_name!r} at {ledger}; run the '
                         f'implementation pipeline first')
    if cross is None:
        cross = [
            ('v6', os.path.join(common.V6_DIR, 'out', 'per_trace_v6.jsonl')),
            ('v7', os.path.join(common.V7_DIR, 'out', 'per_trace_v7.jsonl')),
        ]
    summary = analyze(impl_name, ledger, cross_ledgers=cross)
    summary['corpus_inventory'] = corpus_inventory()
    common.write_json(summary, 'production_summary.json')
    common.write_json(summary['breakdowns'], 'production_breakdowns.json')
    common.write_json(summary['unavailable_measurement_accounting'],
                      'production_unavailable.json')
    common.write_json(summary['confidence_intervals'], 'production_cis.json')
    # human report
    b = summary['behavioral_divergence']
    d = summary['downstream_propagation']
    t = summary['task_degradation']
    n_led = summary['denominators']['n_ledger_rows']
    m2_all = d['M2_cross_agent_all']
    m2_nz = d['M2_cross_agent_nonzero_threshold']
    m3_supp = d['M3_supported']
    m3_ver = d['M3_verified']
    m3_pl = d['M3_plausible']
    m4m = d['M4_matched']
    m4m_den = d['M4_matched_denominator']
    m4_hist = d['M4_historical']
    zt_only = b['zero_threshold_only_positives']
    n_rows_led = summary['denominators']['n_ledger_rows']
    n_comp = summary['denominators']['n_comparable_frame']
    m1_c = b['union_m1_count']
    m1_r = b['union_m1_rate']
    dim_str = ' / '.join(f'{k} {v:.1%}' for k, v in
                          b['dimension_rates'].items())
    ben_str = ', '.join(f'{f} {v["rate"]:.1%} ({v["k"]}/{v["n_graded"]})'
                        for f, v in t['benign_failure_baseline'].items())
    lines = [
        f'# Production analysis — {impl_name}',
        '',
        f'- Frame: {n_led} rows ({n_comp} comparable under the frozen gate).',
        f'- **M1 union divergence: {m1_c} ({m1_r:.2%})**; {dim_str}',
        f'- zero-threshold-only positives: {zt_only}',
        f'- M2 cross-agent: {m2_all} ({m2_all / n_rows_led:.2%} of frame; '
        f'nonzero-threshold {m2_nz})',
        f'- M3 supported: {m3_supp} (verified {m3_ver} / plausible {m3_pl})',
        f'- M4-matched (of M3-supported {m4m_den}): {m4m}; '
        f'historical M4 {m4_hist}; class mix {d["M4_class_counts"]}',
        f'- benign no-LEP failure baseline (per family, graded refs): '
        f'{ben_str}',
        '',
        'All propagation/degradation metrics are perturbation-ASSOCIATED '
        '(benign references are separate executions, not counterfactuals). '
        'See FROZEN_PROTOCOL / MEASUREMENT_SPEC §4, §8.',
    ]
    common.write_text('\n'.join(lines) + '\n', 'production_report.md')
    print(json.dumps({
        'impl': impl_name, 'm1': [b['union_m1_count'],
                                  round(b['union_m1_rate'], 4)],
        'M2': d['M2_cross_agent_all'], 'M3': d['M3_supported'],
        'M4_matched': [d['M4_matched'], d['M4_matched_denominator']],
        'empty_cells': len(summary['empty_cells']),
    }, indent=1))
    return summary


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--impl', default='candspec')
    a = ap.parse_args()
    run(a.impl)
