"""Offline BND (baseline-normalized behavioral deviation) evaluation.

Independent of the propagation detectors: reads the consolidated production
traces and the id_trc_v2 classification ledger read-only, scores every
perturbed trace against its three matched benign references, and writes all
outputs to a fresh directory.  No generation, no model calls, no GPU.

Reference-selection policy is deliberately identical to the existing pipeline
(first admitted benign attempt per fixture/topology/execution-variant/
repetition, in sorted source-path order), so BND reuses the same reference
identities; only the scoring is new.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from benchmark import baseline_deviation as bd

LOG = logging.getLogger('scripts.analyze_baseline_deviation')


# ── IO helpers ──────────────────────────────────────────────────────────────


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_bytes())


def read_jsonl(path):
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]


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
            'p75': q(0.75), 'p90': q(0.90), 'min': round(vals[0], 6), 'max': round(vals[-1], 6)}


# ── discovery (mirrors the legacy grouping policy, read-only) ─────────────


def discover(source: Path):
    """Group traces by cell; returns (benign_refs, lep_records, skipped).

    benign_refs: {(fixture, topology, execution_variant): {'repetitions': [...],
               'paths': [..3..], 'complete': bool, 'extra': int}}
    lep_records: [{'path', 'fixture', 'topology', 'execution_variant',
               'task_family', 'lep_code', 'propagation_mode', 'attempt_id'}]
    """
    trace_dir = source / 'traces'
    files = sorted(p for p in trace_dir.glob('*_trace.json') if p.is_file())
    LOG.info('discovered %d trace files under %s', len(files), trace_dir)
    benign: dict = defaultdict(dict)
    lep: list = []
    skipped: Counter = Counter()
    for path in files:
        try:
            m = read_json(path)['metadata']
        except Exception as exc:
            skipped['unparseable'] += 1
            LOG.warning('unparseable trace %s: %s', path.name, exc)
            continue
        fixture = m.get('fixture_id')
        cell = (fixture, m.get('topology'), m.get('execution_variant', 'standard'))
        if m.get('condition') in ('benign', 'counterfactual') or not m.get('lep_codes'):
            rep = m.get('repetition_index')
            if not fixture or rep is None:
                skipped['benign_missing_identity'] += 1
                continue
            group = benign[cell]
            if rep not in group:
                group[rep] = str(path)
            continue
        if m.get('condition') != 'single_lep':
            skipped['unknown_condition'] += 1
            continue
        if not m.get('dataset_eligible', True):
            skipped['lep_not_dataset_eligible'] += 1
            continue
        lep.append({'path': str(path), 'fixture': fixture, 'topology': cell[1],
                    'execution_variant': cell[2], 'task_family': m.get('task_family'),
                    'lep_code': (m.get('lep_codes') or [None])[0],
                    'propagation_mode': m.get('propagation_mode'),
                    'attempt_id': m.get('attempt_id')})
    references = {}
    for cell, reps in sorted(benign.items()):
        ordered = [reps[r] for r in sorted(reps)[:3]]
        references[cell] = {'repetitions': sorted(reps)[:3], 'paths': ordered,
                            'complete': len(reps) >= 3,
                            'extra': max(0, len(reps) - 3)}
    return references, lep, dict(skipped)


# ── worker-side measurement (process pool; per-worker spec cache) ──────────

_SPEC_CACHE: dict = {}


def _load_spec(spec_root: str, fixture_id) -> tuple:
    path = str(Path(spec_root) / str(fixture_id) / 'manifest.json')
    if path not in _SPEC_CACHE:
        p = Path(path)
        spec = json.loads(p.read_text()) if p.is_file() else None
        _SPEC_CACHE[path] = (spec, sha(p) if p.is_file() else None)
    return _SPEC_CACHE[path]


def _component_deltas(result, weights) -> dict:
    out = {}
    for c in bd.COMPONENTS:
        a, b = result.component_lep.get(c), result.component_benign.get(c)
        out[c] = {'lep': a, 'benign': b,
                  'delta': (a - b) if a is not None and b is not None else None}
    return out


def _feature_payload(feats: 'bd.Features') -> dict:
    """Compact, JSON-friendly view of the extracted features (the raw evidence
    behind each component distance; no truncation of the extracted values)."""
    out: dict = {
        'coverage': feats.coverage(),
        'diagnostics': feats.diagnostics,
        'n_actions': sum(feats.action_counters.values()),
    }
    if feats.financial_facts:
        out['financial_facts'] = feats.financial_facts
        out['final_answer_value'] = feats.final_answer_value
    if feats.claim_verdicts:
        out['claim_verdicts'] = feats.claim_verdicts
        out['overall_verdict'] = feats.overall_verdict
    if feats.repair_targets:
        out['repair_targets'] = feats.repair_targets
        out['decision'] = feats.decision
        out['patch_files'] = feats.patch_files
    return out


def measure_chunk(chunk: dict) -> list:
    """Measure one cell group: refs loaded once, spec cached per worker."""
    refs = [read_json(p) for p in chunk['ref_paths']]
    result_rows = []
    for rec in chunk['lep_records']:
        try:
            lep = read_json(rec['path'])
        except Exception as exc:
            result_rows.append({'trace_id': rec.get('attempt_id'), 'status': 'unparseable_lep',
                                'reasons': [str(exc)]})
            continue
        spec, spec_sha = _load_spec(chunk['spec_root'], rec['fixture'])
        result = bd.score_bnd(lep, refs, spec, chunk['weights'])
        lep_feats = bd.extract_features(lep, spec)
        features_map = {lep.get('trace_id', ''): _feature_payload(lep_feats)}
        for rf in refs:
            features_map[rf.get('trace_id', '')] = _feature_payload(bd.extract_features(rf, spec))
        payload = result.payload(chunk['weights'], features=features_map)
        payload['status'] = 'measured'
        payload['attempt_id'] = rec['attempt_id']
        payload['component_deltas'] = _component_deltas(result, chunk['weights'])
        payload['spec_sha256'] = spec_sha
        payload['spec_available'] = spec is not None
        payload['reference_complete'] = chunk['reference_complete']
        payload['reference_repetitions'] = chunk['reference_repetitions']
        result_rows.append(payload)
    return result_rows


# ── cross-tabulation against the id_trc_v2 ledger ──────────────────────────


def attach_v2_classifications(results, v2_dir: Path):
    metrics = v2_dir / 'propagation_metrics.jsonl'
    if not metrics.is_file():
        for r in results:
            r['v2_classification'] = None
            r['v2_status'] = 'v2_ledger_missing'
        return Counter({'v2_ledger_missing': len(results)})
    ledger = {row.get('attempt_id'): row for row in read_jsonl(metrics)}
    counts: Counter = Counter()
    for r in results:
        row = ledger.get(r.get('attempt_id'))
        if row is None:
            r['v2_classification'] = None
            r['v2_status'] = 'unmeasured_by_v2'
        else:
            r['v2_classification'] = row.get('classification')
            r['v2_status'] = 'joined'
        counts[r['v2_status']] += 1
    return counts


def cross_tab(results):
    """Anomaly flag x v2 classification grid, plus high/low-BND exemplars.

    Exemplars are ranked by observed BND values per v2 class; no universal
    BND cutoff is assumed anywhere.
    """
    counts: Counter = Counter()
    by_class: dict = defaultdict(list)
    for r in results:
        if r.get('status') != 'measured':
            continue
        counts[(r.get('behavioral_anomaly'), r.get('v2_classification'))] += 1
        if r.get('bnd') is not None:
            by_class[r.get('v2_classification')].append(r)
    table = {f'anomaly={a},v2={c}': n
             for (a, c), n in sorted(counts.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1])))}
    examples = {}
    for cls in sorted(k for k in by_class if k is not None):
        rows = sorted(by_class[cls], key=lambda r: -r['bnd'])
        examples[str(cls)] = {
            'n': len(rows),
            'highest_bnd': rows[0],
            'lowest_bnd': rows[-1],
        }
    return table, examples, {str(k): len(v) for k, v in sorted(by_class.items(), key=lambda kv: str(kv[0]))}


# ── aggregation ────────────────────────────────────────────────────────────


def group_summary(rows):
    comparable = [r for r in rows if r.get('comparable')]
    anomalous = [r for r in comparable if r.get('behavioral_anomaly') is True]
    return {
        'n': len(rows),
        'comparable': len(comparable),
        'incomparable': len(rows) - len(comparable),
        'anomaly_fraction': round(len(anomalous) / len(comparable), 6) if comparable else None,
        'anomaly_count': len(anomalous),
        'bnd': quantiles([r.get('bnd') for r in rows if r.get('bnd') is not None]),
        'd_lep': quantiles([r.get('d_lep') for r in rows if r.get('d_lep') is not None]),
        'd_benign': quantiles([r.get('d_benign') for r in rows if r.get('d_benign') is not None]),
        'threshold_t': quantiles([r.get('threshold', {}).get('t') for r in comparable
                                  if r.get('threshold', {}).get('t') is not None]),
    }


def by_field(results, field):
    groups: dict = defaultdict(list)
    for r in results:
        if r.get('status') != 'measured':
            continue
        groups[r.get(field) or 'unknown'].append(r)
    return {k: group_summary(v) for k, v in sorted(groups.items())}


def coverage_report(results, skipped, v2_join):
    incomparable_reasons: Counter = Counter()
    unavailable_components: Counter = Counter()
    for r in results:
        if r.get('status') != 'measured':
            continue
        if not r.get('comparable'):
            for reason in r.get('reasons', []):
                incomparable_reasons[reason] += 1
        for s in r.get('soft_diagnostics', []):
            incomparable_reasons['soft:' + s] += 1
        for p in r.get('pair_lep', []):
            for u in p.get('unavailable', []):
                unavailable_components[u] += 1
    return {
        'skipped_at_discovery': dict(skipped),
        'measured_traces': sum(1 for r in results if r.get('status') == 'measured'),
        'unparseable_lep': sum(1 for r in results if r.get('status') == 'unparseable_lep'),
        'specs_available': sum(1 for r in results if r.get('spec_available')),
        'specs_missing': sum(1 for r in results if r.get('status') == 'measured'
                             and not r.get('spec_available')),
        'reference_complete': sum(1 for r in results if r.get('reference_complete')),
        'reference_incomplete': sum(1 for r in results if r.get('status') == 'measured'
                                    and not r.get('reference_complete')),
        'v2_ledger_join': dict(v2_join),
        'incomparable_reasons': dict(sorted(incomparable_reasons.items())),
        'unavailable_components_in_lep_pairs': dict(sorted(unavailable_components.items())),
    }


def reference_calibration(results):
    """Reference-only (leave-one-out) spread diagnostics, aggregated.

    Three benign runs are a tiny baseline: these numbers are sanity checks
    on how much deviation the references themselves exhibit. They are NOT
    p-values and no calibrated significance is claimed.
    """
    loo_values = []
    exceeded_all = 0
    evaluated = 0
    for r in results:
        if r.get('status') != 'measured' or r.get('d_lep') is None:
            continue
        evaluated += 1
        loo = r.get('reference_only') or {}
        vals = [v for v in loo.values() if v is not None]
        loo_values.extend(vals)
        if vals and all(r['d_lep'] >= v for v in vals):
            exceeded_all += 1
    return {
        'note': ('Diagnostic leave-one-out reference-only scores from three-run '
                 'baselines; they bound observed reference spread but do not '
                 'support statistical significance claims.'),
        'evaluated_traces': evaluated,
        'reference_only_scores': quantiles(loo_values),
        'd_lep_exceeds_all_reference_only': {
            'count': exceeded_all,
            'fraction': round(exceeded_all / evaluated, 6) if evaluated else None,
        },
    }


# ── main ────────────────────────────────────────────────────────────────────


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path,
                        default=Path('analysis/full_production_consolidated/source'))
    parser.add_argument('--output', type=Path,
                        default=Path('analysis/full_production_baseline_deviation_v1'))
    parser.add_argument('--spec-root', type=Path, default=Path('workspace_fixtures'))
    parser.add_argument('--v2-dir', type=Path, default=Path('analysis/full_production_id_trc_v2'))
    parser.add_argument('--weights', default=None,
                        help="JSON component weights, e.g. '{\"assertions\":0.5,...}'. "
                             'Default: module DEFAULT_WEIGHTS (experimental, not validated).')
    parser.add_argument('--limit', type=int, default=None)
    parser.add_argument('--task-families', default=None)
    parser.add_argument('--workers', type=int, default=24)
    args = parser.parse_args(argv)

    weights = (bd.ComponentWeights.from_kwargs(**json.loads(args.weights))
               if args.weights else None)
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f'refusing to write into non-empty directory: {output}')
    output.mkdir(parents=True)

    references, lep_records, skipped = discover(args.source)
    LOG.info('benign cells: %d (%d complete with 3 repetitions), LEP traces: %d',
             len(references),
             sum(1 for r in references.values() if r['complete']), len(lep_records))
    if args.task_families:
        allow = set(args.task_families.split(','))
        lep_records = [r for r in lep_records if r['task_family'] in allow]
        LOG.info('task-family filter leaves %d LEP traces', len(lep_records))
    if args.limit:
        lep_records = lep_records[:args.limit]

    # Deterministic chunking: one chunk per reference group (refs load once).
    cells_by_key = {}
    for rec in lep_records:
        key = (rec['fixture'], rec['topology'], rec['execution_variant'])
        cells_by_key.setdefault(key, []).append(rec)
    ordered_cells = sorted(cells_by_key)
    chunk_index = []
    for key in ordered_cells:
        ref_entry = references.get(key)
        if ref_entry is None:
            ref_entry = {'paths': [], 'complete': False, 'repetitions': []}
        chunk = {'ref_paths': ref_entry['paths'],
                 'reference_complete': ref_entry['complete'],
                 'reference_repetitions': ref_entry['repetitions'],
                 'lep_records': cells_by_key[key],
                 'spec_root': str(args.spec_root.resolve()),
                 'weights': weights}
        chunk_index.append((key, chunk, len(cells_by_key[key])))

    chunks = [c for _, c, _ in chunk_index]
    if args.workers and args.workers > 1 and len(chunks) > 1:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            chunk_rows = list(pool.map(
                measure_chunk, chunks,
                chunksize=max(1, len(chunks) // (args.workers * 4))))
    else:
        chunk_rows = [measure_chunk(c) for c in chunks]
    results = [r for chunk in chunk_rows for r in chunk]
    LOG.info('measured %d LEP traces', len(results))

    v2_join = attach_v2_classifications(results, args.v2_dir)
    xtab, examples, class_sizes = cross_tab(results)
    cov = coverage_report(results, skipped, v2_join)
    calib = reference_calibration(results)

    measured = [r for r in results if r.get('status') == 'measured']
    with open(output / 'bnd_per_trace.jsonl', 'w') as fh:
        for r in sorted(results, key=lambda r: (r.get('status') != 'measured', r.get('attempt_id') or '')):
            fh.write(json.dumps(r, sort_keys=True, default=str) + '\n')

    by_lep = by_field(results, 'lep_code')
    by_fam = by_field(results, 'task_family')
    by_topo = by_field(results, 'topology')
    by_mode = by_field(results, 'propagation_mode')
    by_config: dict = defaultdict(list)
    for r in measured:
        key = '|'.join(str(r.get(f) or '?') for f in ('lep_code', 'task_family', 'topology', 'propagation_mode'))
        by_config[key].append(r)
    by_config = {k: group_summary(v) for k, v in sorted(by_config.items())}

    for name, data in (('bnd_by_lep_type', by_lep), ('bnd_by_task_family', by_fam),
                       ('bnd_by_topology', by_topo), ('bnd_by_propagation_mode', by_mode),
                       ('bnd_by_configuration', by_config)):
        (output / f'{name}.json').write_text(json.dumps(data, indent=1, sort_keys=True) + '\n')

    (output / 'cross_tabulation.json').write_text(json.dumps({
        'anomaly_x_v2_classification': xtab,
        'v2_classification_sizes': class_sizes,
        'high_low_bnd_examples_by_v2_class': examples,
        'interpretation': ('BND anomalies are baseline-relative excess deviation; '
                           'v2 classifications are attribution-based detector '
                           'statuses. Disagreement is complementary signal, not '
                           'an error in either analyzer.'),
    }, indent=1, sort_keys=True, default=str) + '\n')

    def measurable():
        return [r for r in measured if r.get('bnd') is not None]

    (output / 'examples_high_deviation.json').write_text(
        json.dumps(sorted(measurable(), key=lambda r: -r['bnd'])[:20],
                   indent=1, sort_keys=True, default=str) + '\n')
    (output / 'examples_low_deviation.json').write_text(
        json.dumps(sorted(measurable(), key=lambda r: r['bnd'])[:20],
                   indent=1, sort_keys=True, default=str) + '\n')
    (output / 'examples_meaningful_vs_benign.json').write_text(
        json.dumps({'per_v2_class': examples,
                    'note': 'Representative extracted features and component deltas '
                            'per v2 class; raw values preserved.'},
                   indent=1, sort_keys=True, default=str) + '\n')
    (output / 'coverage_report.json').write_text(json.dumps(cov, indent=1, sort_keys=True) + '\n')
    (output / 'reference_calibration.json').write_text(
        json.dumps(calib, indent=1, sort_keys=True) + '\n')

    summary = {
        'version': bd.VERSION,
        'weights': (weights or bd.ComponentWeights()).as_dict(),
        'threshold_rule': ('behavioral_anomaly = D_LEP > T where T is the max '
                           'pairwise benign distance (comparable references only; '
                           'exploratory heuristic, not a validated threshold)'),
        'bnd_definition': ('BND = D_LEP - D_benign: excess deviation relative to the '
                           'observed benign spread. Signed, unbounded above; a '
                           'difference of mean distances, NOT a scale-normalized '
                           'effect size. Positive BND does not establish causal '
                           'propagation.'),
        'overall': group_summary(measured),
        'by_lep_type': by_lep, 'by_task_family': by_fam,
        'by_topology': by_topo, 'by_propagation_mode': by_mode,
        'cross_tabulation': xtab,
        'v2_classification_sizes': class_sizes,
        'reference_calibration': calib,
        'v2_join': dict(v2_join),
    }
    (output / 'bnd_summary.json').write_text(json.dumps(
        summary, indent=1, sort_keys=True, default=str) + '\n')

    manifest = {
        'module': 'benchmark/baseline_deviation.py',
        'module_sha256': sha(Path('benchmark/baseline_deviation.py')),
        'script_sha256': sha(Path('scripts/analyze_baseline_deviation.py')),
        'source_dir': str(args.source.resolve()),
        'spec_root': str(args.spec_root.resolve()),
        'v2_dir': str(args.v2_dir.resolve()),
        'v2_propagation_metrics_sha256': (sha(args.v2_dir / 'propagation_metrics.jsonl')
                                          if (args.v2_dir / 'propagation_metrics.jsonl').is_file()
                                          else None),
        'n_benign_cells': len(references),
        'n_lep_traces': len(lep_records),
        'reference_selection': ('First benign attempt per fixture/topology/execution-variant/'
                                'repetition, sorted source-path order; HC/IPI-style first '
                                'three distinct repetitions.'),
        'deterministic': True,
        'agent_executions': 0,
        'external_model_calls': 0,
    }
    (output / 'run_manifest.json').write_text(
        json.dumps(manifest, indent=1, sort_keys=True) + '\n')

    LOG.info('wrote outputs to %s', output)
    print(json.dumps({
        'measured': cov['measured_traces'],
        'comparable': summary['overall']['comparable'],
        'anomaly_fraction': summary['overall']['anomaly_fraction'],
        'bnd': summary['overall']['bnd'],
        'anomaly_fraction_by_task_family': {k: v['anomaly_fraction'] for k, v in by_fam.items()},
        'cross_tabulation': xtab,
        'v2_join': dict(v2_join),
        'output_dir': str(output),
    }, indent=1, sort_keys=True))
    return 0


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    raise SystemExit(main())
