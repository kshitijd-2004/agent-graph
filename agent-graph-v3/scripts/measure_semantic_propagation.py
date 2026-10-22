"""Offline MP measurement. Does not run generation or structural analysis.

Input trees must be immutable for the duration of a run. Recovery directories
require an explicit successful-array-task snapshot; archive directories are never
included implicitly. Outputs and resumable results are content-addressed.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from benchmark.propagation_analysis import PropagationAnalyzer
from benchmark.semantic_mp_v2.measurement import evaluate_trace
from benchmark.semantic_mp_v2.measurement_contract import VERSION, fingerprint
from benchmark.semantic_mp_v2.measurement_reporting import summarize_measurements
from benchmark.semantic_mp_v2.production_extraction import ProductionExtractor
from benchmark.semantic_mp_v2.sources import resolve_sources
from generation.admission import validate_trace
from benchmark.semantic_mp.evaluator import ENDPOINTS

ROOT = Path(__file__).resolve().parents[1]
MECHANISM = 'LEP_MEMORY_POISONING'


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    temporary.replace(path)


def completed_shards(path):
    """Read a saved sacct JobID|State|ExitCode snapshot (allocation rows only)."""
    selected = set()
    for line in Path(path).read_text().splitlines():
        columns = [s.strip() for s in line.split('|')]
        if len(columns) < 3:
            raise ValueError('completion snapshot needs JobID|State|ExitCode')
        job, state, exitcode = columns[:3]
        match = re.fullmatch(r'\d+_(\d+)', job)
        if match and state == 'COMPLETED' and exitcode == '0:0':
            selected.add(int(match[1]))
    if not selected:
        raise ValueError('no successfully completed array tasks in snapshot')
    return sorted(selected)


def source_directories(sources, rerun_dir=None, completion=None):
    directories = []
    for value in sources:
        root = Path(value).resolve()
        if not root.is_dir():
            raise ValueError('missing source directory: ' + str(root))
        children = sorted(p for p in root.glob('node_*') if p.is_dir())
        if children:
            if any('_failed_' in p.name for p in children):
                raise ValueError('archive directories present; use --rerun-dir with --completed-shards')
            # Original numeric shards and explicit cumulative staging names.
            directories.extend(p for p in children if re.fullmatch(r'node_(?:original_|rerun_)?\d+', p.name))
        elif (root / 'traces').is_dir():
            directories.append(root)
        else:
            raise ValueError('no trace shards in source directory: ' + str(root))
    if bool(rerun_dir) != bool(completion):
        raise ValueError('--rerun-dir and --completed-shards must be supplied together')
    if rerun_dir:
        for index in completed_shards(completion):
            path = Path(rerun_dir).resolve() / ('node_' + str(index))
            if not path.is_dir():
                raise ValueError('completed shard missing: ' + str(path))
            directories.append(path)
    return list(dict.fromkeys(p.resolve() for p in directories))


def read_trace(path):
    raw = path.read_bytes()
    data = json.loads(raw)
    trace = PropagationAnalyzer._trace_from_dict(data, path)
    return trace, hashlib.sha256(raw).hexdigest()


def load_jobs(directories, plan=None):
    """Admission + identity matching, with no implicit frozen-plan auto-loading.

    plan=None means no plan restriction. An explicit empty plan is an error, not
    an instruction to reject the entire experiment. Outcome denominators here
    are eligible executions; generation attempt quotas remain structural output.
    """
    if plan is not None and not plan:
        raise ValueError('empty plan is invalid; omit --plan for eligibility-only measurement')
    planned = None
    if plan is not None:
        planned = {}
        for entry in plan:
            sid = entry['scenario_id']
            if sid in planned and planned[sid] != entry:
                raise ValueError('conflicting plan scenario: ' + sid)
            planned[sid] = entry
    clean, perturbed, seen, accounting, inventory = {}, [], {}, [], []
    paths = sorted({p for d in directories for folder in ('traces', 'rejected_traces')
                    for p in (d / folder).glob('*_trace.json')})
    for path in paths:
        try:
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            inventory.append({'path': str(path), 'sha256': digest})
            data = json.loads(raw)
            meta = data.get('metadata', {})
            if meta.get('lep_codes') not in ([], [MECHANISM]):
                accounting.append({'path': str(path), 'status': 'excluded', 'reason': 'other_mechanism'})
                continue
            if meta.get('memory_protocol') == 'existing_record_poisoning_v1':
                accounting.append({'path': str(path), 'status': 'excluded', 'reason': 'separate_protocol'})
                continue
            identity = meta.get('attempt_id') or data['trace_id']
            canonical = fingerprint(data)
            if identity in seen:
                if seen[identity] != canonical:
                    raise RuntimeError('conflicting duplicate attempt: ' + identity)
                accounting.append({'path': str(path), 'attempt_id': identity, 'status': 'excluded', 'reason': 'duplicate_attempt'})
                continue
            seen[identity] = canonical
            trace = PropagationAnalyzer._trace_from_dict(data, path)
            admission = validate_trace(trace)
            base = {'path': str(path), 'attempt_id': identity, 'trace_id': trace.trace_id}
            if not admission.admitted:
                accounting.append(dict(base, status='excluded', reason='not_admitted', admission=admission.to_dict()))
                continue
            if meta.get('lep_codes') == [MECHANISM] and planned is not None:
                entry = planned.get(meta.get('scenario_id'))
                if entry is None or any(entry.get(k) != meta.get(k) for k in
                        ('fixture_id', 'task_family', 'condition', 'topology', 'propagation_mode', 'lep_codes')):
                    accounting.append(dict(base, status='excluded', reason='plan_identity_mismatch'))
                    continue
            key = (meta['fixture_id'], meta['task_family'], meta['topology'])
            record = (trace, path, digest)
            if not meta['lep_codes']:
                repetition = meta.get('repetition_index')
                if meta.get('execution_variant') != 'memory_enabled' or repetition is None:
                    accounting.append(dict(base, status='excluded', reason='unmatched_control_variant_or_identity'))
                    continue
                group = clean.setdefault(key, {})
                if repetition in group:
                    accounting.append(dict(base, status='excluded', reason='duplicate_clean_repetition',
                                           selected_path=str(group[repetition][1])))
                else:
                    group[repetition] = record
            else:
                perturbed.append((key, record))
        except RuntimeError:
            raise
        except Exception as error:
            accounting.append({'path': str(path), 'status': 'ingestion_error', 'error': repr(error)})
    jobs = []
    for key, record in perturbed:
        controls = list(clean.get(key, {}).values())
        if len(controls) < 3:
            accounting.append({'path': str(record[1]), 'trace_id': record[0].trace_id,
                'status': 'excluded', 'reason': 'insufficient_distinct_clean_runs', 'clean_runs': len(controls)})
        else:
            jobs.append((record, controls))
    return jobs, accounting, inventory


def implementation_identity():
    paths = [Path(__file__), *sorted((ROOT / 'benchmark/semantic_mp_v2').glob('*.py')),
             ROOT / 'benchmark/memory_delivery_audit.py', ROOT / 'generation/admission.py',
             ROOT / 'generation/injection_origins.py', ROOT / 'benchmark/mp_retrospective.py',
             ROOT / 'benchmark/semantic_mp/extraction.py', ROOT / 'benchmark/semantic_mp/evaluator.py']
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def run(args):
    if args.mechanism != MECHANISM:
        raise ValueError('only write-interception LEP_MEMORY_POISONING is supported')
    directories = source_directories(args.source_dir, args.rerun_dir, args.completed_shards)
    output = args.output.resolve()
    if any(output == d or output.is_relative_to(d) for d in directories):
        raise ValueError('output must be separate from input shards')
    plan = json.loads(args.plan.read_text()) if args.plan else None
    if isinstance(plan, dict):
        if plan.get('schema') != 'agentprop-frozen-plan-v1':
            raise ValueError('unsupported plan schema')
        plan = plan['entries']
    jobs, accounting, inventory = load_jobs(directories, plan)
    if not jobs:
        if not output.exists():
            output.mkdir(parents=True)
            with (output / 'accounting.jsonl').open('w') as handle:
                for row in accounting:
                    handle.write(json.dumps(row) + '\n')
            atomic_json(output / 'run_failure.json', {'error': 'zero_eligible_traces',
                'input_files': inventory, 'eligible': 0})
        raise ValueError('zero eligible admitted MP traces with three distinct matched controls')
    jobs.sort(key=lambda j: fingerprint(j[0][0].metadata.get('attempt_id') or j[0][0].trace_id))
    population = len(jobs)
    if args.max_traces:
        jobs = jobs[:args.max_traces]
    identity = {'version': VERSION, 'implementation': implementation_identity(),
        'inventory_sha256': fingerprint(inventory), 'plan_sha256': fingerprint(plan),
        'directories': [str(d) for d in directories], 'fixture_root': str(args.fixture_root.resolve()),
        'max_traces': args.max_traces, 'mechanism': args.mechanism,
        'completion_snapshot_sha256': hashlib.sha256(args.completed_shards.read_bytes()).hexdigest()
            if args.completed_shards else None}
    # Freeze source evidence before consulting resumable per-attempt results.
    bundles, source_errors = {}, {}
    for fixture in sorted({job[0][0].metadata['fixture_id'] for job in jobs}):
        try:
            bundles[fixture] = resolve_sources(fixture, args.fixture_root)
        except Exception as error:
            source_errors[fixture] = repr(error)
    identity['source_bundles'] = {k: fingerprint(v) for k, v in bundles.items()}
    identity['source_errors'] = source_errors
    if output.exists():
        if not args.resume or not (output / 'run_manifest.json').exists():
            raise ValueError('output exists; use a new directory or --resume')
        if json.loads((output / 'run_manifest.json').read_text())['identity'] != identity:
            raise ValueError('resume input/configuration changed; use a new output directory')
    else:
        output.mkdir(parents=True)
    atomic_json(output / 'run_manifest.json', {'identity': identity,
        'input_files': inventory, 'eligible_population': population, 'selected': len(jobs),
        'development_only': True, 'independent_validation': False,
        'population_definition': 'admitted write-interception MP attempts with >=3 distinct matched controls'})
    (output / 'results').mkdir(exist_ok=True)
    (output / 'sources').mkdir(exist_ok=True)
    for fixture, bundle in bundles.items():
        atomic_json(output / 'sources' / (fixture + '.json'), bundle)
    extractor = ProductionExtractor(output / 'extraction_cache')
    results, rows, failures = [], [], []
    for (trace, path, digest), controls in jobs:
        attempt = trace.metadata.get('attempt_id') or trace.trace_id
        destination = output / 'results' / (fingerprint(attempt) + '.json')
        dependencies = {'trace_sha256': digest, 'clean_sha256': [c[2] for c in controls],
                        'run_identity': fingerprint(identity)}
        row = {'attempt_id': attempt, 'trace_id': trace.trace_id, 'trace_path': str(path),
               'metadata': trace.metadata, 'dependencies': dependencies,
               'clean_references': [{'trace_id': c.trace_id, 'attempt_id': c.metadata.get('attempt_id') or c.trace_id,
                   'repetition_index': c.metadata['repetition_index'], 'path': str(p), 'sha256': h} for c, p, h in controls]}
        try:
            if destination.exists():
                saved = json.loads(destination.read_text())
                if saved['dependencies'] != dependencies or saved['attempt_id'] != attempt:
                    raise ValueError('resumable result identity mismatch')
                checksum = saved.pop('row_sha256')
                if fingerprint(saved) != checksum:
                    raise ValueError('resumable result content mismatch')
                row = saved
            else:
                fixture = trace.metadata['fixture_id']
                if fixture in source_errors:
                    raise ValueError(source_errors[fixture])
                bundle = bundles[fixture]
                result = evaluate_trace(trace, [c[0] for c in controls],
                    authoritative_text=bundle['authoritative_text'], extractor=extractor, enabled=True)
                row.update(status='measured', source_bundle_sha256=fingerprint(bundle), measurement=result)
                atomic_json(destination, dict(row, row_sha256=fingerprint(row)))
            results.append(row['measurement'])
            rows.append(row)
        except Exception as error:
            failures.append(dict(row, status='measurement_error', error=repr(error)))
    with (output / 'semantic_by_trace.jsonl').open('w') as handle:
        for row in rows:
            handle.write(json.dumps(row) + '\n')
    with (output / 'accounting.jsonl').open('w') as handle:
        for row in accounting + failures:
            handle.write(json.dumps(row) + '\n')
    summary = summarize_measurements(results)
    summary['runner_accounting'] = {'eligible_population': population, 'selected': len(jobs),
        'completed': len(results), 'measurement_errors': len(failures),
        'exclusions_and_ingestion_errors': dict(Counter(r.get('reason', r['status']) for r in accounting)),
        'endpoint_denominator': 'completed measurements; execution errors are separately unassessed',
        'extraction_cache_hits': extractor.disk_hits, 'network_calls': 0}
    measured_endpoints = {ep: summary['endpoints'].get(ep,
        {'positive': 0, 'negative': 0, 'unknown': 0}) for ep in ENDPOINTS}
    summary['eligible_endpoint_counts'] = {ep: dict(v, eligible=len(jobs),
        unknown=v['unknown'] + len(failures),
        assessment_coverage=(v['positive'] + v['negative']) / len(jobs))
        for ep, v in measured_endpoints.items()}
    atomic_json(output / 'semantic_summary.json', summary)
    if not results:
        raise RuntimeError('zero completed measurements; inspect accounting.jsonl')
    if failures:
        raise RuntimeError(f'{len(failures)} measurement errors; results preserved, inspect accounting.jsonl')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mechanism', choices=[MECHANISM], default=MECHANISM)
    parser.add_argument('--rerun-dir', type=Path)
    parser.add_argument('--completed-shards', type=Path)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--fixture-root', type=Path, default=ROOT / 'workspace_fixtures')
    parser.add_argument('--max-traces', type=int)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if args.max_traces is not None and args.max_traces < 1:
        parser.error('--max-traces must be positive')
    summary = run(args)
    print(json.dumps({'endpoints': summary['endpoints'], 'accounting': summary['runner_accounting']}, indent=2))


if __name__ == '__main__':
    main()
