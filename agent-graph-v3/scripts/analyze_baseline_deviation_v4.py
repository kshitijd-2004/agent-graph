"""Offline BND-V4 (targeted audit-closure) evaluation.

Reads the consolidated production traces read-only and the frozen V3 ledger
(``analysis/full_production_baseline_deviation_v3/``) for a read-only
V3<->V4 classification transition, scores every perturbed trace against its
three matched benign references under V4's corrected measurement, and writes
all outputs to a fresh directory.  No generation, no model calls, no GPU, no
LLM.

Reference-selection policy is identical to V1/V2/V3 (``discover`` is imported
from ``scripts.analyze_baseline_deviation``), so BND V4 measures the SAME
population of 16,833 LEP traces; only the scoring, extraction, recovery, and
reporting semantics are new (the audit's P0-P4 corrections).

P4 reporting corrections vs the V3 closure report:
  * near-threshold sensitivity uses ``abs(D_LEP - T)`` (V3's closure report
    mislabeled ``abs(BND) <= .01`` as "near_threshold": 2,938 vs the actual
    2,068 rows with ``abs(D_LEP - T) <= .01``).
  * trace rows are distinguished from unique benign-reference cells (V3
    called 2,390 rows "zero_T_cells"; they represent 437 distinct
    reference-cells).  Both counts are emitted under explicit labels.
  * comparable-population summaries are restricted to comparable rows; any
    all-measured statistic is emitted under an explicit ``all_measured_*``
    block with its denominator (V3's group_summary mixed n=13,701/16,121
    continuous blocks with the 9,143 comparable denominator).
  * all output counts are reconciled (``reconciliation`` block ties every
    denominator to a named population).
"""
from __future__ import annotations

import argparse
import json
import logging
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from benchmark import baseline_deviation_v4 as bdv4
from scripts.analyze_baseline_deviation import discover, read_json, read_jsonl, sha

LOG = logging.getLogger('scripts.analyze_baseline_deviation_v4')


# ── IO helpers ──────────────────────────────────────────────────────────────


def quantiles(values):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return {'n': 0}

    def q(p):
        k = (len(vals) - 1) * p
        lo, hi = int(k), min(int(k) + 1, len(vals) - 1)
        return round(vals[lo] + (vals[hi] - vals[lo]) * (k - lo), 6)

    return {'n': len(vals), 'mean': round(statistics.fmean(vals), 6),
            'median': q(0.5), 'p10': q(0.10), 'p25': q(0.25),
            'p75': q(0.75), 'p90': q(0.90), 'min': round(vals[0], 6),
            'max': round(vals[-1], 6)}


# ── worker-side measurement ─────────────────────────────────────────────────

_SPEC_CACHE: dict = {}


def _load_spec(spec_root: str, fixture_id):
    path = str(Path(spec_root) / str(fixture_id) / 'manifest.json')
    if path not in _SPEC_CACHE:
        p = Path(path)
        spec = json.loads(p.read_text()) if p.is_file() else None
        _SPEC_CACHE[path] = (spec, sha(p) if p.is_file() else None)
    return _SPEC_CACHE[path]


def measure_chunk(chunk: dict) -> list:
    """Measure one cell group under V4; refs loaded once, spec cached."""
    refs = [read_json(p) for p in chunk['ref_paths']]
    rows = []
    for rec in chunk['lep_records']:
        try:
            lep = read_json(rec['path'])
        except Exception as exc:
            rows.append({'trace_id': rec.get('attempt_id') or rec.get('path'),
                         'status': 'unparseable_lep', 'reasons': [str(exc)]})
            continue
        spec, spec_sha = _load_spec(chunk['spec_root'], rec['fixture'])
        out = bdv4.measure_configuration_v4(lep, refs, spec, chunk['weights'])
        out['status'] = 'measured'
        out['attempt_id'] = rec['attempt_id'] or out.get('trace_id')
        out['spec_sha256'] = spec_sha
        out['spec_available'] = spec is not None
        out['reference_complete'] = chunk['reference_complete']
        out['reference_repetitions'] = chunk['reference_repetitions']
        rows.append(out)
    return rows


# ── V3 join (read-only; V3 numbers never feed the V4 score) ────────────────


def _reference_cell_id(row):
    """The unique benign-reference cell a trace row belongs to (the V4
    analogue of a "reference triple"): the identity of the matched
    reference set, NOT the trace row itself."""
    refs = tuple(row.get('references') or [])
    return (row.get('task_family'), row.get('topology'),
            row.get('propagation_mode'), refs)


def attach_v3(results, v3_dir: Path):
    """Read-only join with the frozen V3 ledger, for a V3<->V4 transition
    and before/after correction/relapse accounting only."""
    v3_file = v3_dir / 'bnd_v3_per_trace.jsonl'
    v3_by_tid = {}
    if v3_file.is_file():
        for row in read_jsonl(v3_file):
            v3_by_tid[row.get('trace_id')] = row
    counts = Counter()
    for r in results:
        tid = r.get('trace_id')
        v3row = v3_by_tid.get(tid)
        r['v3_bnd'] = (v3row or {}).get('bnd')
        r['v3_legacy_anomaly'] = (v3row or {}).get('behavioral_anomaly')
        r['v3_stage'] = (v3row or {}).get('stage')
        counts['v3_joined' if v3row else 'v3_not_found'] += 1
    return dict(counts), v3_by_tid


def v3_v4_transition(results, v3_by_tid):
    """V3 vs V4 anomaly-flag transition on the SAME comparable population.

    A flip isolates the extraction/provenance/availability/recovery change;
    the threshold rule (D_LEP > T) is held fixed.  V3-unmeasured rows are
    never counted as V4-negative (fixed-denominator rule)."""
    comparable = [r for r in results
                  if r.get('status') == 'measured' and r.get('comparable')]
    grid = Counter()
    examples = []
    for r in comparable:
        tid = r.get('trace_id')
        v3 = (v3_by_tid.get(tid) or {}).get('behavioral_anomaly')
        v4 = r.get('behavioral_anomaly')
        grid[(v3, v4)] += 1
        if v3 is not None and v4 is not None and v3 != v4 and len(examples) < 25:
            examples.append({
                'trace_id': tid, 'lep_code': r.get('lep_code'),
                'v3_anomaly': v3, 'v4_anomaly': v4,
                'v3_bnd': (v3_by_tid.get(tid) or {}).get('bnd'),
                'v4_bnd': r.get('bnd'),
            })
    both = sum(1 for r in comparable
               if (v3_by_tid.get(r.get('trace_id')) or {}).get(
                   'behavioral_anomaly') is not None
               and r.get('behavioral_anomaly') is not None)
    return {
        'n_comparable_v4': len(comparable),
        'n_v3_measurable': both,
        'transition_grid': {f'v3={a},v4={b}': n
                            for (a, b), n in sorted(
                                grid.items(), key=lambda kv: (str(kv[0][0]),
                                                              str(kv[0][1])))},
        'v4_only_rows': len(comparable) - both,
        'flip_examples': examples,
        'note': ('Denominator is the V4-comparable population; V3-unmeasured '
                 'rows are never counted as V4-negative.'),
    }


# ── coverage / availability accounting ──────────────────────────────────────


def coverage_report(results):
    comparable = [r for r in results
                   if r.get('status') == 'measured' and r.get('comparable')]
    state_counts = Counter()
    schema_losses = Counter()
    assert_avail = 0
    strict_basis_unavail = 0
    for r in comparable:
        sch = r.get('schema') or {}
        mask = sch.get('component_mask') or []
        for c in bdv4.COMPONENTS:
            if c not in mask:
                schema_losses[f'{c}:lost_on_benign_pair'] += 1
        feat = (r.get('features') or {}).get(r.get('trace_id')) or {}
        cov = feat.get('coverage') or {}
        for c, st in cov.items():
            state_counts[f'{c}:{st}'] += 1
        if cov.get('assertions') in (bdv4.OBSERVED, bdv4.ABSENT):
            assert_avail += 1
        # P4: rows whose strict basis (required key or schema component)
        # made at least one pair explicitly unavailable
        if (r.get('n_pairs_benign_unavailable') or 0) or \
           (r.get('n_pairs_lep_unavailable') or 0):
            strict_basis_unavail += 1
    return {
        'n_comparable': len(comparable),
        'component_lost_reasons': dict(schema_losses),
        'feature_state_counts': dict(sorted(state_counts.items())),
        'assertion_component_observed_or_absent': assert_avail,
        'assertion_coverage_fraction':
            round(assert_avail / len(comparable), 6) if comparable else None,
        'rows_with_strict_basis_unavailable_pair': strict_basis_unavail,
    }


# ── injection-origin exclusion evidence (C2 / P0) ──────────────────────────


def origin_exclusion_evidence(results):
    """Count how many comparable LEP traces carry one or more recorded
    injection origins, and confirm the excluded origin content is NOT in the
    behavioral view (delivered != authored, excluded from agent_text)."""
    comparable = [r for r in results
                   if r.get('status') == 'measured' and r.get('comparable')]
    n_origins_dist = Counter()
    n_with_origins = 0
    n_replaced = 0
    for r in comparable:
        feat = (r.get('features') or {}).get(r.get('trace_id')) or {}
        prov = feat.get('provenance') or {}
        n = prov.get('n_origins', 0)
        n_origins_dist[n] += 1
        if n:
            n_with_origins += 1
            for o in (prov.get('origins') or []):
                if o.get('delivered_is_output'):
                    n_replaced += 1
    return {
        'n_comparable': len(comparable),
        'n_with_recorded_origins': n_with_origins,
        'n_orphan_replaced_origins': n_replaced,
        'n_origins_distribution': {str(k): v
                                   for k, v in sorted(n_origins_dist.items())},
        'note': ('An origin with delivered_is_output has its attacker-'
                 'delivered content EXCLUDED from the behavioral view; only '
                 'the recorded authored side (mutation_evidence.before) is '
                 'scored.  P1.4: INPUT-DISREGARD origins whose output is the '
                 'agent\'s own authored response stay measurable.'),
    }


# ── stage / convergence / correction accounting ────────────────────────────


def _fr(vals):
    present = [v for v in vals if v is not None]
    return {'n_present': len(present),
            'true': sum(1 for v in present if v is True),
            'false': sum(1 for v in present if v is False),
            'none': len(vals) - len(present)}


def stage_summary(results):
    """Comparable-population stage summary (P4: restricted to comparable
    traces; denominators explicit)."""
    comparable = [r for r in results
                  if r.get('status') == 'measured' and r.get('comparable')]
    st = [r.get('stage') or {} for r in comparable]

    def vals(key):
        return [s.get(key) for s in st]

    return {
        'n_comparable': len(comparable),
        'denominator': 'comparable traces only (P4 fixed population)',
        'd_peak': quantiles(vals('d_peak')),
        'd_final_output': quantiles(vals('d_final_output')),
        'd_action_history': quantiles(vals('d_action_history')),
        'stage_threshold': quantiles(vals('stage_threshold')),
        'final_threshold': quantiles(vals('final_threshold')),
        'high_intermediate_deviation': _fr(vals('high_intermediate_deviation')),
        'persistent_final_deviation': _fr(vals('persistent_final_deviation')),
        'observational_convergence': _fr(vals('observational_convergence')),
        'evidence_supported_correction':
            _fr(vals('evidence_supported_correction')),
        'relapse': _fr(vals('relapse')),
        'correction_episodes_total': sum(
            len(r.get('correction_episodes') or []) for r in comparable),
        'note': ('P3 replacement outcomes: observational convergence, '
                 'persistent final deviation, evidence-supported correction, '
                 'and relapse are KEY-LEVEL and require actual observed '
                 'value/stance changes plus a recorded dependency path.  '
                 'Relapse is never inferred from an aggregate final '
                 'deviation.  Causal LEP attribution is not emitted.'),
    }


def stage_coverage(results):
    comparable = [r for r in results
                   if r.get('status') == 'measured' and r.get('comparable')]
    out = {}
    for key in ('d_peak', 'd_final_output', 'd_action_history',
                'stage_threshold', 'final_threshold',
                'high_intermediate_deviation', 'persistent_final_deviation',
                'observational_convergence', 'evidence_supported_correction',
                'relapse'):
        avail = sum(1 for r in comparable
                    if (r.get('stage') or {}).get(key) is not None)
        out[key] = {'available': avail,
                    'unavailable': len(comparable) - avail,
                    'coverage': round(avail / len(comparable), 6)
                    if comparable else None}
    reasons = Counter()
    for r in comparable:
        for u in (r.get('stage') or {}).get('unavailable', []):
            reasons[str(u)] += 1
    out['checkpoint_unavailable_reasons'] = dict(reasons.most_common(20))
    return out


# ── threshold sensitivity (P4: corrected near-threshold rule) ──────────────

SENS_RULES = {
    'R0_legacy_Dlep_gt_T':
        lambda r: (r.get('d_lep') > (r.get('threshold') or {}).get('t')
                   if (r.get('threshold') or {}).get('t') is not None
                   else None),
    'R1_bnd_gt_0':
        lambda r: (r.get('bnd') > 0.0 if r.get('bnd') is not None else None),
    'R2_bnd_gt_0p05':
        lambda r: (r.get('bnd') > 0.05 if r.get('bnd') is not None else None),
    'R3_Dlep_gt_T_plus_0p05':
        lambda r: (r.get('d_lep') is not None
                   and (r.get('threshold') or {}).get('t') is not None
                   and r['d_lep'] > r['threshold']['t'] + 0.05),
    'R4_Dlep_gt_T_plus_0p10':
        lambda r: (r.get('d_lep') is not None
                   and (r.get('threshold') or {}).get('t') is not None
                   and r['d_lep'] > r['threshold']['t'] + 0.10),
    'R5_Dlep_gt_T_times_1p25':
        lambda r: (r.get('d_lep') is not None
                   and (r.get('threshold') or {}).get('t') is not None
                   and r['d_lep'] > r['threshold']['t'] * 1.25),
}


def threshold_sensitivity(results):
    """P4: near-threshold = ``abs(D_LEP - T) <= 0.01`` (the distance to the
    threshold the flag rule actually uses), NOT ``abs(BND) <= 0.01``.  The
    V3 closure report's "near_threshold: 2,938 / zero_T_cells: 2,390" are
    the BND-based and row-based counts; both are reproduced under explicit
    labels and the corrected rule is primary."""
    comparable = [r for r in results
                  if r.get('status') == 'measured' and r.get('comparable')]
    table = {}
    for name, fn in SENS_RULES.items():
        vals = [fn(r) for r in comparable]
        flagged = sum(1 for v in vals if v is True)
        unknown = sum(1 for v in vals if v is None)
        table[name] = {'n_comparable': len(comparable), 'flagged': flagged,
                       'fraction': round(flagged / len(comparable), 6)
                       if comparable else None,
                       'unevaluable': unknown}
    near = 0
    near_undef = 0
    bnd_near = 0
    for r in comparable:
        t = (r.get('threshold') or {}).get('t')
        dl = r.get('d_lep')
        if t is None or dl is None:
            near_undef += 1
            continue
        if abs(dl - t) <= 0.01:
            near += 1
        if r.get('bnd') is not None and abs(r['bnd']) <= 0.01:
            bnd_near += 1
    # zero-T accounting: trace rows vs UNIQUE benign-reference cells
    zero_rows = [r for r in comparable
                 if (r.get('threshold') or {}).get('t') == 0.0]
    zero_cells = {_reference_cell_id(r) for r in zero_rows}
    all_cells = {_reference_cell_id(r) for r in comparable
                 if (r.get('threshold') or {}).get('t') is not None}
    table['near_threshold'] = {
        'rule': 'abs(D_LEP - T) <= 0.01',
        'n_comparable': len(comparable),
        'n_near_threshold_dlep': near,
        'n_unevaluable': near_undef,
        # the V3-report (defective) count, retained for reconciliation
        'n_abs_bnd_le_0p01_v3_rule': bnd_near,
        'note': ('V4 corrects the V3 closure report, which labeled '
                 'abs(BND) <= .01 as "near_threshold" (2,938); the '
                 'distance-to-threshold of the flag rule is '
                 'abs(D_LEP - T) (V3 actual: 2,068 on the frozen ledger).'),
    }
    table['zero_T'] = {
        'n_zero_T_rows': len(zero_rows),
        'n_zero_T_reference_cells': len(zero_cells),
        'n_all_reference_cells': len(all_cells),
        'note': ('A trace row and a benign-reference cell are different '
                 'units: many LEP traces share one reference cell.  V3\'s '
                 '"2,390 zero_T_cells" mixed the two; V4 reports rows and '
                 'unique reference cells separately.'),
    }
    return table


# ── group accounting (P4: fixed denominators) ──────────────────────────────


def group_summary(rows, population: str):
    """Summary over a NAMED population.  ``population`` is either
    'comparable' (default P4 restriction) or 'all_measured' (explicitly
    labeled broader denominator — no silent mixing)."""
    if population == 'comparable':
        base = [r for r in rows if r.get('comparable')]
        denom = base
    else:
        base = rows
        denom = [r for r in rows if r.get('comparable')]
    comparable = [r for r in base if r.get('comparable')]
    anomalous = [r for r in base
                 if r.get('behavioral_anomaly') is True]
    stage = [r.get('stage') or {} for r in base]
    return {
        'population': population,
        'n': len(base),
        'comparable': len(comparable),
        'incomparable': len(base) - len(comparable),
        'anomaly_count': len(anomalous),
        'anomaly_fraction': (round(len(anomalous) / len(comparable), 6)
                            if comparable else None),
        'denominator_note': (
            f'anomaly fraction denominator = comparable rows '
            f'({len(comparable)}); population = {population} '
            f'({len(base)} rows)'),
        # continuous blocks are labeled with THEIR denominator
        'bnd_comparable': quantiles([r.get('bnd') for r in
                                     comparable if r.get('bnd') is not None]),
        'd_lep_comparable': quantiles([r.get('d_lep') for r in
                                       comparable if r.get('d_lep') is not None]),
        'd_benign_comparable': quantiles([r.get('d_benign') for r in
                                          comparable
                                          if r.get('d_benign') is not None]),
        # all-measured blocks under explicit labels (P4 broader denominators)
        'all_measured': {
            'n': len(denom),
            'bnd': quantiles([r.get('bnd') for r in denom
                              if r.get('bnd') is not None]),
            'd_lep': quantiles([r.get('d_lep') for r in denom
                                if r.get('d_lep') is not None]),
            'd_benign': quantiles([r.get('d_benign') for r in denom
                                   if r.get('d_benign') is not None]),
            'note': 'Broader all-measured denominators, explicitly labeled; '
                    'never mixed with the comparable-population blocks.',
        } if population == 'comparable' else None,
        'high_intermediate_fraction': _frac(
            [s.get('high_intermediate_deviation') for s in stage], comparable),
        'persistent_final_deviation_fraction': _frac(
            [s.get('persistent_final_deviation') for s in stage], comparable),
        'observational_convergence_count': sum(
            1 for s in stage if s.get('observational_convergence') is True),
        'evidence_supported_correction_count': sum(
            1 for s in stage
            if s.get('evidence_supported_correction') is True),
        'relapse_count': sum(1 for s in stage if s.get('relapse') is True),
        'correction_episodes_count': sum(
            len(s.get('correction_episodes') or []) for s in stage),
    }


def _frac(vals, comparable):
    present = [v for v in vals if v is not None]
    if not present or not comparable:
        return None
    return round(sum(1 for v in present if v is True) / len(comparable), 6)


def by_field(results, field):
    groups = defaultdict(list)
    for r in results:
        if r.get('status') != 'measured':
            continue
        groups[r.get(field) or 'unknown'].append(r)
    return {k: group_summary(v, 'comparable')
            for k, v in sorted(groups.items())}


# ── P4 reconciliation ───────────────────────────────────────────────────────


def reconciliation(results, attribution_inter, attribution_total):
    measured = [r for r in results if r.get('status') == 'measured']
    comparable = [r for r in measured if r.get('comparable')]
    unparseable = [r for r in results if r.get('status') != 'measured']
    pairs_benign_unavail = sum(r.get('n_pairs_benign_unavailable') or 0
                               for r in comparable)
    pairs_lep_unavail = sum(r.get('n_pairs_lep_unavailable') or 0
                            for r in comparable)
    return {
        'note': ('Fixed denominators.  Unmeasured LEP traces are NEVER '
                 'counted as negative; the attribution join is read-only '
                 'and separate; the V3 join is a transition diagnostic '
                 'only.'),
        'bnd_v4_measured': len(measured),
        'bnd_v4_comparable': len(comparable),
        'bnd_v4_incomparable': len(measured) - len(comparable),
        'unparseable_lep': len(unparseable),
        'check': (len(comparable) + len(measured) - len(comparable)
                  == len(measured)),
        'pairs_benign_unavailable': pairs_benign_unavail,
        'pairs_lep_unavailable': pairs_lep_unavail,
        'rows_with_any_unavailable_pair': sum(
            1 for r in comparable if (r.get('n_pairs_benign_unavailable')
                                      or 0)
            or (r.get('n_pairs_lep_unavailable') or 0)),
        'strict_basis_pair_accounting': (
            'explicit pair unavailability is the P0.2 correction: a pair '
            'missing a required key or a schema component is recorded, '
            'never renormalized.'),
        'attribution_ledger_joined': attribution_inter,
        'attribution_ledger_total': attribution_total,
        'attribution_comparable': sum(
            1 for r in comparable
            if r.get('attribution_status') == 'joined'),
    }


def attach_attribution_ledger(results, ledger_dir: Path):
    """Read-only join with the original attribution detector (id_trc_v2);
    strictly separate from BND (no propagation label feeds a score)."""
    metrics = ledger_dir / 'propagation_metrics.jsonl'
    by_tid = {}
    if metrics.is_file():
        for row in read_jsonl(metrics):
            by_tid[row.get('trace_id')] = row
    for r in results:
        row = by_tid.get(r.get('trace_id'))
        r['attribution_classification'] = (row or {}).get('classification')
        r['attribution_status'] = 'joined' if row else 'unmeasured'
    inter = sum(1 for r in results
                if r.get('attribution_status') == 'joined')
    return inter, len(by_tid)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path,
                        default=Path('analysis/full_production_consolidated/source'))
    parser.add_argument('--output', type=Path,
                        default=Path('analysis/full_production_baseline_deviation_v4'))
    parser.add_argument('--spec-root', type=Path, default=Path('workspace_fixtures'))
    parser.add_argument('--v3-dir', type=Path,
                        default=Path('analysis/full_production_baseline_deviation_v3'))
    parser.add_argument('--ledger-dir', type=Path,
                        default=Path('analysis/full_production_id_trc_v2'))
    parser.add_argument('--limit', type=int, default=None)
    parser.add_argument('--task-families', default=None)
    parser.add_argument('--workers', type=int, default=24)
    args = parser.parse_args(argv)

    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f'refusing to write into non-empty directory: {output}')
    output.mkdir(parents=True)

    references, lep_records, skipped = discover(args.source)
    LOG.info('benign cells: %d, LEP traces: %d',
             len(references), len(lep_records))
    if args.task_families:
        allow = set(args.task_families.split(','))
        lep_records = [r for r in lep_records if r['task_family'] in allow]
    if args.limit:
        lep_records = lep_records[:args.limit]

    weights = bdv4.ComponentWeights()
    cells_by_key = defaultdict(list)
    for rec in lep_records:
        cells_by_key[(rec['fixture'], rec['topology'],
                      rec['execution_variant'])].append(rec)
    chunks = []
    for key in sorted(cells_by_key):
        ref_entry = (references.get(key)
                     or {'paths': [], 'complete': False, 'repetitions': []})
        chunks.append({'ref_paths': ref_entry['paths'],
                       'reference_complete': ref_entry['complete'],
                       'reference_repetitions': ref_entry['repetitions'],
                       'lep_records': cells_by_key[key],
                       'spec_root': str(args.spec_root.resolve()),
                       'weights': weights})
    if args.workers and args.workers > 1 and len(chunks) > 1:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            chunk_rows = list(pool.map(
                measure_chunk, chunks,
                chunksize=max(1, len(chunks) // (args.workers * 4))))
    else:
        chunk_rows = [measure_chunk(c) for c in chunks]
    results = [r for chunk in chunk_rows for r in chunk]
    LOG.info('measured %d LEP traces under V4', len(results))

    v3_join, v3_by_tid = attach_v3(results, args.v3_dir)
    attr_inter, attr_total = attach_attribution_ledger(results, args.ledger_dir)

    measured = [r for r in results if r.get('status') == 'measured']
    with open(output / 'bnd_v4_per_trace.jsonl', 'w') as fh:
        for r in sorted(results, key=lambda r:
                       (r.get('status') != 'measured',
                        r.get('trace_id') or '')):
            fh.write(json.dumps(r, sort_keys=True, default=str) + '\n')

    by_lep = by_field(results, 'lep_code')
    by_fam = by_field(results, 'task_family')
    by_topo = by_field(results, 'topology')
    by_mode = by_field(results, 'propagation_mode')
    for name, data in (('bnd_v4_by_lep_type', by_lep),
                       ('bnd_v4_by_task_family', by_fam),
                       ('bnd_v4_by_topology', by_topo),
                       ('bnd_v4_by_propagation_mode', by_mode)):
        (output / f'{name}.json').write_text(
            json.dumps(data, indent=1, sort_keys=True) + '\n')

    (output / 'v3_v4_transition.json').write_text(json.dumps(
        v3_v4_transition(results, v3_by_tid), indent=1, sort_keys=True,
        default=str) + '\n')
    (output / 'coverage_report_v4.json').write_text(json.dumps(
        coverage_report(results), indent=1, sort_keys=True, default=str)
        + '\n')
    (output / 'origin_exclusion_evidence.json').write_text(json.dumps(
        origin_exclusion_evidence(results), indent=1, sort_keys=True,
        default=str) + '\n')
    (output / 'threshold_sensitivity_v4.json').write_text(json.dumps(
        threshold_sensitivity(results), indent=1, sort_keys=True) + '\n')
    (output / 'stage_coverage_v4.json').write_text(json.dumps(
        stage_coverage(results), indent=1, sort_keys=True, default=str)
        + '\n')
    (output / 'stage_summary_v4.json').write_text(json.dumps(
        stage_summary(results), indent=1, sort_keys=True, default=str)
        + '\n')
    (output / 'reconciliation_v4.json').write_text(json.dumps(
        reconciliation(results, attr_inter, attr_total),
        indent=1, sort_keys=True) + '\n')

    overall = group_summary(measured, 'comparable')
    summary = {
        'version': bdv4.VERSION,
        'weights': weights.as_dict(),
        'overall': overall,
        'by_lep_type': by_lep, 'by_task_family': by_fam,
        'by_topology': by_topo, 'by_propagation_mode': by_mode,
        'threshold_sensitivity': threshold_sensitivity(results),
        'stage_summary': stage_summary(results),
        'stage_coverage': stage_coverage(results),
        'coverage': coverage_report(results),
        'origin_exclusion': origin_exclusion_evidence(results),
        'v3_join': v3_join,
        'attribution_ledger': {'joined': attr_inter, 'total': attr_total},
        'corrections': [
            'P0.1: relative-difference kernel restored (100 vs 101 -> 1/101)',
            'P0.2: required-key strict basis; missing required observations '
            'are unavailable pairs, never renormalized',
            'P1.1: final-OUTPUT scope excludes cumulative action history',
            'P1.2: recovery ordered by the recorded depends_on DAG',
            'P1.3: current assertion supersedes stale same-role prefixes',
            'P1.4: INPUT-DISREGARD authored responses retained',
            'P2: demonstrated extraction counterexamples (units, percent, '
            'year/value binding, issue-local stance, verdict polarity)',
            'P3: key-level recovery/relapse with observed-change + '
            'dependency-path evidence',
            'P4: corrected near-threshold rule, row-vs-cell zero-T '
            'accounting, fixed denominators, reconciled counts',
        ],
        'scientific_separation': ('BND V4 is a behavioral-deviation '
                                  'measurement.  The attribution ledger and '
                                  'V3 are joined read-only for transition '
                                  'diagnostics ONLY; no BND score, stage '
                                  'metric, or recovery outcome consults a '
                                  'propagation label.'),
        'not_a_tuning_target': ('The max-of-three-benign threshold remains '
                                'an exploratory heuristic; no threshold or '
                                'weight was tuned to preserve any historical '
                                'anomaly fraction.'),
    }
    (output / 'bnd_v4_summary.json').write_text(json.dumps(
        summary, indent=1, sort_keys=True, default=str) + '\n')

    manifest = {
        'module': 'benchmark/baseline_deviation_v4.py',
        'module_sha256': sha(Path('benchmark/baseline_deviation_v4.py')),
        'script_sha256': sha(Path('scripts/analyze_baseline_deviation_v4.py')),
        'v3_module_sha256': sha(Path('benchmark/baseline_deviation_v3.py')),
        'v1_module_sha256': sha(Path('benchmark/baseline_deviation.py')),
        'source_dir': str(args.source.resolve()),
        'spec_root': str(args.spec_root.resolve()),
        'v3_dir': str(args.v3_dir.resolve()),
        'ledger_dir': str(args.ledger_dir.resolve()),
        'n_benign_cells': len(references),
        'n_lep_traces': len(lep_records),
        'reference_selection':
            'Identical to V1/V2/V3 (imported discover); same population.',
        'deterministic': True,
        'agent_executions': 0,
        'external_model_calls': 0,
        'gpu_jobs': 0,
    }
    (output / 'run_manifest.json').write_text(
        json.dumps(manifest, indent=1, sort_keys=True) + '\n')

    o = summary['overall']
    print(json.dumps({
        'measured': len(measured),
        'comparable': o['comparable'],
        'anomaly_count': o['anomaly_count'],
        'anomaly_fraction': o['anomaly_fraction'],
        'bnd_mean': (o['bnd_comparable'] or {}).get('mean'),
        'observational_convergence':
            o['observational_convergence_count'],
        'evidence_supported_correction':
            o['evidence_supported_correction_count'],
        'relapse': o['relapse_count'],
        'correction_episodes': o['correction_episodes_count'],
        'near_threshold_dlep': summary['threshold_sensitivity']
            ['near_threshold']['n_near_threshold_dlep'],
        'output_dir': str(output),
    }, indent=1, sort_keys=True))
    return 0


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    raise SystemExit(main())
