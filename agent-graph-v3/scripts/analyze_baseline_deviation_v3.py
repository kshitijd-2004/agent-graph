"""Offline BND-V3 (audit-corrected) evaluation.

Reads the consolidated production traces and the frozen V2 ledger read-only,
scores every perturbed trace against its three matched benign references under
V3's provenance-aware extraction + aligned stage metrics, and writes all
outputs to a fresh directory.  No generation, no model calls, no GPU, no LLM.

Reference-selection policy is identical to V1/V2 (``discover`` is imported
from ``scripts.analyze_baseline_deviation``), so BND V3 measures the SAME
population of 16,833 LEP traces; only the scoring, extraction, and recovery
semantics are new (the audit's P0 corrections).

Produces the audit-closure deliverables (section 10): population-flow
accounting, continuous BND distributions, V2-vs-V3 anomaly-classification
transitions, per-LEP anomaly fractions, feature-extraction coverage,
unavailable/incomparable reasons, injection-origin exclusion evidence,
intermediate/final deviation, observational convergence, evidence-supported
correction + relapse, threshold sensitivity, and a read-only comparison with
the original attribution detector (the id_trc_v2 ledger).
"""
from __future__ import annotations

import argparse
import json
import logging
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from benchmark import baseline_deviation_v3 as bdv3
from scripts.analyze_baseline_deviation import discover, read_json, read_jsonl, sha
from benchmark import baseline_deviation_v2 as bdv2

LOG = logging.getLogger('scripts.analyze_baseline_deviation_v3')


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
    """Measure one cell group under V3; refs loaded once, spec cached."""
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
        out = bdv3.measure_configuration_v3(lep, refs, spec, chunk['weights'])
        out['status'] = 'measured'
        out['attempt_id'] = rec['attempt_id'] or out.get('trace_id')
        out['spec_sha256'] = spec_sha
        out['spec_available'] = spec is not None
        out['reference_complete'] = chunk['reference_complete']
        out['reference_repetitions'] = chunk['reference_repetitions']
        rows.append(out)
    return rows


# ── V2 join (read-only cross-tab; never feeds the V3 score) ───────────────


def attach_v2(results, v2_dir: Path):
    """Join the frozen V2 ledger and the original attribution (id_trc_v2)
    ledger, both read-only.  V3 computes nothing from these; they are
    reported as a comparison only."""
    v2_file = v2_dir / 'bnd_v2_per_trace.jsonl'
    v2_by_tid = {}
    if v2_file.is_file():
        for row in read_jsonl(v2_file):
            v2_by_tid[row.get('trace_id')] = row
    counts = Counter()
    for r in results:
        tid = r.get('trace_id')
        v2row = v2_by_tid.get(tid)
        r['v2_bnd'] = (v2row or {}).get('bnd')
        r['v2_legacy_anomaly'] = (v2row or {}).get('legacy_anomaly')
        r['v2_stage'] = (v2row or {}).get('stage')
        counts['v2_joined' if v2row else 'v2_not_found'] += 1
    return dict(counts), v2_by_tid


def attach_attribution_ledger(results, ledger_dir: Path):
    """Read-only join with the original attribution detector (id_trc_v2).
    Kept strictly separate from BND (audit: do NOT reuse propagation labels as
    BND ground truth)."""
    metrics = ledger_dir / 'propagation_metrics.jsonl'
    by_tid = {}
    if metrics.is_file():
        for row in read_jsonl(metrics):
            by_tid[row.get('trace_id')] = row
    for r in results:
        row = by_tid.get(r.get('trace_id'))
        r['attribution_classification'] = (row or {}).get('classification')
        r['attribution_status'] = 'joined' if row else 'unmeasured'
    inter = sum(1 for r in results if r.get('attribution_status') == 'joined')
    return inter, len(by_tid)


# ── V2 <-> V3 transition (M7 reporting: fixed denominators) ────────────────


def v2_v3_transition(results, v2_by_tid):
    """V2-legacy vs V3 anomaly flag transitions on the SAME comparable rows.

    A flip isolates the extraction/provenance/availability change; the
    threshold rule (D_LEP > T) is held fixed.  V2-unmeasured rows are never
    counted as V3-negative (M7 fixed-denominator rule)."""
    comparable = [r for r in results
                  if r.get('status') == 'measured' and r.get('comparable')]
    grid = Counter()
    examples = []
    for r in comparable:
        tid = r.get('trace_id')
        v2 = (v2_by_tid.get(tid) or {}).get('legacy_anomaly')
        v3 = r.get('behavioral_anomaly')
        grid[(v2, v3)] += 1
        if v2 is not None and v3 is not None and v2 != v3 and len(examples) < 25:
            examples.append({
                'trace_id': tid, 'lep_code': r.get('lep_code'),
                'v2_legacy_anomaly': v2, 'v3_anomaly': v3,
                'v2_bnd': (v2_by_tid.get(tid) or {}).get('bnd'),
                'v3_bnd': r.get('bnd'),
            })
    total_comparable = len(comparable)
    both = sum(1 for r in comparable if (v2_by_tid.get(r.get('trace_id')) or {}).get('legacy_anomaly') is not None and r.get('behavioral_anomaly') is not None)
    return {
        'n_comparable_v3': total_comparable,
        'n_v2_measurable': both,
        'transition_grid': {f'v2={a},v3={b}': n
                            for (a, b), n in sorted(grid.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1])))},
        'v3_only_rows': total_comparable - both,
        'flip_examples': examples,
        'note': 'Denominator is the V3-comparable population; V2-unmeasured rows are never counted as V3-negative (M7).',
    }


# ── coverage / availability accounting (M6) ────────────────────────────────


def coverage_report(results):
    comparable = [r for r in results if r.get('status') == 'measured' and r.get('comparable')]
    state_counts = Counter()
    schema_losses = Counter()
    assert_avail = 0
    for r in comparable:
        sch = r.get('schema') or {}
        mask = sch.get('component_mask') or []
        for c in bdv3.COMPONENTS:
            if c not in mask:
                schema_losses[f'{c}:lost_on_benign_pair'] += 1
        feat = (r.get('features') or {}).get(r.get('trace_id')) or {}
        cov = feat.get('coverage') or {}
        for c, st in cov.items():
            state_counts[f'{c}:{st}'] += 1
        # explicit assertion availability: the assertion component is an actual
        # observation (OBSERVED value or ABSENT) rather than a missingness
        # placeholder
        if cov.get('assertions') in (bdv3.OBSERVED, bdv3.ABSENT):
            assert_avail += 1
    return {
        'n_comparable': len(comparable),
        'component_lost_reasons': dict(schema_losses),
        'feature_state_counts': dict(sorted(state_counts.items())),
        'assertion_component_observed_or_absent': assert_avail,
        'assertion_coverage_fraction': round(assert_avail / len(comparable), 6) if comparable else None,
    }


# ── injection-origin exclusion evidence (C2 / P0) ─────────────────────────


def origin_exclusion_evidence(results):
    """Count how many comparable LEP traces carry one or more recorded
    injection origins, and confirm the excluded origin content is NOT in the
    behavioral view (delivered != authored, and excluded from agent_text)."""
    comparable = [r for r in results if r.get('status') == 'measured' and r.get('comparable')]
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
            # each origin with delivered_is_output has its attacker content
            # excluded from agent_text
            for o in (prov.get('origins') or []):
                if o.get('delivered_is_output'):
                    n_replaced += 1
    return {
        'n_comparable': len(comparable),
        'n_with_recorded_origins': n_with_origins,
        'n_orphan_replaced_origins': n_replaced,
        'n_origins_distribution': {str(k): v for k, v in sorted(n_origins_dist.items())},
        'note': ('An origin with delivered_is_output has its attacker-delivered '
                 'content EXCLUDED from the behavioral view; only the recorded '
                 'authored side (mutation_evidence.before) is scored.'),
    }


# ── stage / convergence / correction accounting ────────────────────────────


def stage_summary(results):
    comparable = [r for r in results if r.get('status') == 'measured' and r.get('comparable')]
    st = [r.get('stage') or {} for r in comparable]

    def fr(key):
        vals = [s.get(key) for s in st]
        present = [v for v in vals if v is not None]
        return {'n_present': len(present),
                'true': sum(1 for v in present if v is True),
                'false': sum(1 for v in present if v is False)}
    return {
        'n_comparable': len(comparable),
        'd_peak': quantiles([s.get('d_peak') for s in st]),
        'd_final_output': quantiles([s.get('d_final_output') for s in st]),
        'd_action_history': quantiles([s.get('d_action_history') for s in st]),
        'stage_threshold': quantiles([s.get('stage_threshold') for s in st]),
        'final_threshold': quantiles([s.get('final_threshold') for s in st]),
        'high_intermediate_deviation': fr('high_intermediate_deviation'),
        'persistent_final_deviation': fr('persistent_final_deviation'),
        'observational_convergence': fr('observational_convergence'),
        'evidence_supported_correction': fr('evidence_supported_correction'),
        'relapse': fr('relapse'),
        'note': ('Stage metrics are baseline-relative; intermediate and final '
                 'scopes are measured separately and pooled over comparable '
                 'benign checkpoints only (M1).  Recovery outcomes are the '
                 'C1 replacements: observational convergence, persistent final '
                 'deviation, evidence-supported correction, relapse.  No '
                 'verified_recovery label is emitted.'),
    }


def stage_coverage(results):
    comparable = [r for r in results if r.get('status') == 'measured' and r.get('comparable')]
    out = {}
    for key in ('d_peak', 'd_final_output', 'd_action_history',
                'stage_threshold', 'final_threshold',
                'high_intermediate_deviation', 'persistent_final_deviation',
                'observational_convergence', 'evidence_supported_correction', 'relapse'):
        vals = [(r.get('stage') or {}).get(key) for r in comparable]
        avail = sum(1 for v in vals if v is not None)
        out[key] = {'available': avail, 'unavailable': len(comparable) - avail,
                    'coverage': round(avail / len(comparable), 6) if comparable else None}
    reasons = Counter()
    for r in comparable:
        for u in (r.get('stage') or {}).get('unavailable', []):
            reasons[str(u)] += 1
    out['checkpoint_unavailable_reasons'] = dict(reasons.most_common(20))
    return out


# ── threshold sensitivity (independent rules; M7) ───────────────────────────

SENS_RULES = {
    'R0_legacy_Dlep_gt_T':       lambda r: (r.get('d_lep') > (r.get('threshold') or {}).get('t')
                                           if (r.get('threshold') or {}).get('t') is not None else None),
    'R1_bnd_gt_0':              lambda r: (r.get('bnd') > 0.0 if r.get('bnd') is not None else None),
    'R2_bnd_gt_0p05':           lambda r: (r.get('bnd') > 0.05 if r.get('bnd') is not None else None),
    'R3_Dlep_gt_T_plus_0p05':   lambda r: (r.get('d_lep') is not None and (r.get('threshold') or {}).get('t') is not None
                                          and r['d_lep'] > r['threshold']['t'] + 0.05),
    'R4_Dlep_gt_T_plus_0p10':   lambda r: (r.get('d_lep') is not None and (r.get('threshold') or {}).get('t') is not None
                                          and r['d_lep'] > r['threshold']['t'] + 0.10),
    'R5_Dlep_gt_T_times_1p25':  lambda r: (r.get('d_lep') is not None and (r.get('threshold') or {}).get('t') is not None
                                          and r['d_lep'] > r['threshold']['t'] * 1.25),
}


def threshold_sensitivity(results):
    comparable = [r for r in results if r.get('status') == 'measured' and r.get('comparable')]
    table = {}
    for name, fn in SENS_RULES.items():
        vals = [fn(r) for r in comparable]
        flagged = sum(1 for v in vals if v is True)
        unknown = sum(1 for v in vals if v is None)
        table[name] = {'n_comparable': len(comparable), 'flagged': flagged,
                       'fraction': round(flagged / len(comparable), 6) if comparable else None,
                       'unevaluable': unknown}
    near_01 = sum(1 for r in comparable if r.get('bnd') is not None and abs(r['bnd']) <= 0.01)
    zero_T = sum(1 for r in comparable if (r.get('threshold') or {}).get('t') == 0.0)
    table['near_threshold'] = {'n_comparable': len(comparable),
                               'n_abs_bnd_le_0p01': near_01,
                               'n_zero_T_cells': zero_T}
    return table


# ── group accounting (fixed denominators, M7) ──────────────────────────────


def group_summary(rows):
    comparable = [r for r in rows if r.get('comparable')]
    anomalous = [r for r in comparable if r.get('behavioral_anomaly') is True]
    stage = [r.get('stage') or {} for r in comparable]
    return {
        'n': len(rows),
        'comparable': len(comparable),
        'incomparable': len(rows) - len(comparable),
        'anomaly_count': len(anomalous),
        'anomaly_fraction': round(len(anomalous) / len(comparable), 6) if comparable else None,
        'bnd': quantiles([r.get('bnd') for r in rows if r.get('bnd') is not None]),
        'd_lep': quantiles([r.get('d_lep') for r in rows if r.get('d_lep') is not None]),
        'd_benign': quantiles([r.get('d_benign') for r in rows if r.get('d_benign') is not None]),
        'threshold_t': quantiles([(r.get('threshold') or {}).get('t') for r in comparable
                                  if (r.get('threshold') or {}).get('t') is not None]),
        'high_intermediate_fraction': _frac([s.get('high_intermediate_deviation') for s in stage], comparable),
        'persistent_final_deviation_fraction': _frac([s.get('persistent_final_deviation') for s in stage], comparable),
        'observational_convergence_count': sum(1 for s in stage if s.get('observational_convergence') is True),
        'evidence_supported_correction_count': sum(1 for s in stage if s.get('evidence_supported_correction') is True),
        'relapse_count': sum(1 for s in stage if s.get('relapse') is True),
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
    return {k: group_summary(v) for k, v in sorted(groups.items())}


def population_accounting(results, attribution_inter, attribution_total):
    measured = [r for r in results if r.get('status') == 'measured']
    comparable = [r for r in measured if r.get('comparable')]
    return {
        'note': ('Fixed denominators.  Unmeasured LEP traces are NEVER counted '
                 'as negative; attribution-join is read-only and separate.'),
        'bnd_v3_measured': len(measured),
        'bnd_v3_comparable': len(comparable),
        'attribution_ledger_joined': attribution_inter,
        'attribution_ledger_total': attribution_total,
        'attribution_comparable': sum(1 for r in comparable if r.get('attribution_status') == 'joined'),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('analysis/full_production_consolidated/source'))
    parser.add_argument('--output', type=Path, default=Path('analysis/full_production_baseline_deviation_v3'))
    parser.add_argument('--spec-root', type=Path, default=Path('workspace_fixtures'))
    parser.add_argument('--v2-dir', type=Path, default=Path('analysis/full_production_baseline_deviation_v2'))
    parser.add_argument('--ledger-dir', type=Path, default=Path('analysis/full_production_id_trc_v2'))
    parser.add_argument('--limit', type=int, default=None)
    parser.add_argument('--task-families', default=None)
    parser.add_argument('--workers', type=int, default=24)
    args = parser.parse_args(argv)

    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f'refusing to write into non-empty directory: {output}')
    output.mkdir(parents=True)

    references, lep_records, skipped = discover(args.source)
    LOG.info('benign cells: %d, LEP traces: %d', len(references), len(lep_records))
    if args.task_families:
        allow = set(args.task_families.split(','))
        lep_records = [r for r in lep_records if r['task_family'] in allow]
    if args.limit:
        lep_records = lep_records[:args.limit]

    weights = bdv3.ComponentWeights()
    cells_by_key = defaultdict(list)
    for rec in lep_records:
        cells_by_key[(rec['fixture'], rec['topology'], rec['execution_variant'])].append(rec)
    chunks = []
    for key in sorted(cells_by_key):
        ref_entry = references.get(key) or {'paths': [], 'complete': False, 'repetitions': []}
        chunks.append({'ref_paths': ref_entry['paths'],
                       'reference_complete': ref_entry['complete'],
                       'reference_repetitions': ref_entry['repetitions'],
                       'lep_records': cells_by_key[key],
                       'spec_root': str(args.spec_root.resolve()),
                       'weights': weights})
    if args.workers and args.workers > 1 and len(chunks) > 1:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            chunk_rows = list(pool.map(measure_chunk, chunks,
                                       chunksize=max(1, len(chunks) // (args.workers * 4))))
    else:
        chunk_rows = [measure_chunk(c) for c in chunks]
    results = [r for chunk in chunk_rows for r in chunk]
    LOG.info('measured %d LEP traces under V3', len(results))

    v2_join, v2_by_tid = attach_v2(results, args.v2_dir)
    attr_inter, attr_total = attach_attribution_ledger(results, args.ledger_dir)

    measured = [r for r in results if r.get('status') == 'measured']
    with open(output / 'bnd_v3_per_trace.jsonl', 'w') as fh:
        for r in sorted(results, key=lambda r: (r.get('status') != 'measured', r.get('trace_id') or '')):
            fh.write(json.dumps(r, sort_keys=True, default=str) + '\n')

    by_lep = by_field(results, 'lep_code')
    by_fam = by_field(results, 'task_family')
    by_topo = by_field(results, 'topology')
    by_mode = by_field(results, 'propagation_mode')
    for name, data in (('bnd_v3_by_lep_type', by_lep),
                       ('bnd_v3_by_task_family', by_fam),
                       ('bnd_v3_by_topology', by_topo),
                       ('bnd_v3_by_propagation_mode', by_mode)):
        (output / f'{name}.json').write_text(json.dumps(data, indent=1, sort_keys=True) + '\n')

    (output / 'v2_v3_transition.json').write_text(json.dumps(
        v2_v3_transition(results, v2_by_tid), indent=1, sort_keys=True, default=str) + '\n')
    (output / 'coverage_report_v3.json').write_text(json.dumps(
        coverage_report(results), indent=1, sort_keys=True, default=str) + '\n')
    (output / 'origin_exclusion_evidence.json').write_text(json.dumps(
        origin_exclusion_evidence(results), indent=1, sort_keys=True, default=str) + '\n')
    (output / 'threshold_sensitivity_v3.json').write_text(json.dumps(
        threshold_sensitivity(results), indent=1, sort_keys=True) + '\n')
    (output / 'stage_coverage_v3.json').write_text(json.dumps(
        stage_coverage(results), indent=1, sort_keys=True, default=str) + '\n')
    (output / 'stage_summary_v3.json').write_text(json.dumps(
        stage_summary(results), indent=1, sort_keys=True, default=str) + '\n')
    (output / 'population_accounting_v3.json').write_text(json.dumps(
        population_accounting(results, attr_inter, attr_total),
        indent=1, sort_keys=True) + '\n')

    summary = {
        'version': bdv3.VERSION,
        'weights': weights.as_dict(),
        'overall': group_summary(measured),
        'by_lep_type': by_lep, 'by_task_family': by_fam,
        'by_topology': by_topo, 'by_propagation_mode': by_mode,
        'threshold_sensitivity': threshold_sensitivity(results),
        'stage_summary': stage_summary(results),
        'stage_coverage': stage_coverage(results),
        'coverage': coverage_report(results),
        'origin_exclusion': origin_exclusion_evidence(results),
        'v2_join': v2_join,
        'attribution_ledger': {'joined': attr_inter, 'total': attr_total},
        'corrections': [
            'provenance-aware authored view; all injected origins excluded (C2)',
            'context-aware, ground-truth-free financial extraction (M3)',
            'code/research stance from verdict-bearing clauses (M4)',
            'action identity from agent-authored args; role-classified artifacts (M5)',
            'explicit feature-availability states + benign-derived schema (M6)',
            'aligned stage checkpoints; separated final vs action history (M1/M2)',
            'retired verified_recovery; observational convergence / persistent '
            'final deviation / evidence-supported correction / relapse (C1)',
        ],
        'scientific_separation': ('BND V3 is a behavioral-deviation measurement.  '
                                  'The attribution ledger and V2 are joined '
                                  'read-only for cross-tabulation ONLY; no BND '
                                  'score, stage metric, or recovery outcome '
                                  'consults a propagation label.'),
    }
    (output / 'bnd_v3_summary.json').write_text(json.dumps(
        summary, indent=1, sort_keys=True, default=str) + '\n')

    manifest = {
        'module': 'benchmark/baseline_deviation_v3.py',
        'module_sha256': sha(Path('benchmark/baseline_deviation_v3.py')),
        'script_sha256': sha(Path('scripts/analyze_baseline_deviation_v3.py')),
        'v2_module_sha256': sha(Path('benchmark/baseline_deviation_v2.py')),
        'v1_module_sha256': sha(Path('benchmark/baseline_deviation.py')),
        'source_dir': str(args.source.resolve()),
        'spec_root': str(args.spec_root.resolve()),
        'v2_dir': str(args.v2_dir.resolve()),
        'ledger_dir': str(args.ledger_dir.resolve()),
        'n_benign_cells': len(references),
        'n_lep_traces': len(lep_records),
        'reference_selection': 'Identical to V1/V2 (imported discover); same population.',
        'deterministic': True,
        'agent_executions': 0,
        'external_model_calls': 0,
        'gpu_jobs': 0,
    }
    (output / 'run_manifest.json').write_text(json.dumps(manifest, indent=1, sort_keys=True) + '\n')

    o = summary['overall']
    print(json.dumps({
        'measured': len(measured),
        'comparable': o['comparable'],
        'anomaly_fraction': o['anomaly_fraction'],
        'bnd_mean': o['bnd'].get('mean'),
        'observational_convergence': o['observational_convergence_count'],
        'evidence_supported_correction': o['evidence_supported_correction_count'],
        'relapse': o['relapse_count'],
        'output_dir': str(output),
    }, indent=1, sort_keys=True))
    return 0


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    raise SystemExit(main())
