"""Explicitly selected SciFact pilot. Default validates/prints plan, never infers."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
FIXTURES = Path(__file__).parent / 'fixtures'
PROTOCOL = 'existing_record_poisoning_v1'


def build_plan():
    from generation.existing_record_protocol import validate_fixture
    inventory = json.loads((FIXTURES.parent / 'inventory.json').read_text())
    actual = {str(p.relative_to(FIXTURES)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in FIXTURES.rglob('*') if p.is_file()}
    if actual != inventory['files'] or inventory['protocol'] != PROTOCOL:
        raise ValueError('Frozen pilot fixture inventory changed')
    entries = []
    for path in sorted(FIXTURES.glob('*/manifest.json')):
        fixture = json.loads(path.read_text())
        validate_fixture(fixture['memory_protocol'], path.parent)
        fid = fixture['fixture_id']
        for condition, repetitions in [('benign', 3), ('single_lep', 1)]:
            for rep in range(repetitions):
                sid = f'{fid}_{PROTOCOL}_{condition}_{rep:02d}'
                entries.append(dict(run_id=f'pilot-{len(entries):03d}', scenario_id=sid,
                    fixture_id=fid, task_family='research_synthesis',
                    task_variant='scifact_cached_briefing_v1', condition=condition,
                    topology='coordinator_workers', propagation_mode='one_to_many',
                    memory_protocol=PROTOCOL, execution_variant=PROTOCOL,
                    fixture_manifest_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    lep_codes=[] if condition == 'benign' else ['LEP_MEMORY_POISONING'],
                    lep_code='' if condition == 'benign' else 'LEP_MEMORY_POISONING',
                    repetition_index=rep, is_baseline=condition == 'benign',
                    pair_tag=f'{fid}_{PROTOCOL}_benign_{rep:02d}', valid_trace_quota=1))
    if len(entries) != 40:
        raise ValueError('Pilot requires exactly ten validated fixtures')
    return entries


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute', action='store_true', help='Explicitly launch model-backed pilot')
    p.add_argument('--output-dir', type=Path, default=ROOT/'benchmark_output/scifact_memory_v1')
    p.add_argument('--model', default='claude-sonnet-5')
    p.add_argument('--backend', choices=['api', 'vllm'], default='api')
    p.add_argument('--vllm-url', default='http://localhost:8000/v1')
    args = p.parse_args()
    entries = build_plan()
    if not args.execute:
        print(json.dumps({'protocol': PROTOCOL, 'benign': 30, 'perturbed': 10, 'entries': entries}, indent=2))
        return
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise ValueError('Use a fresh pilot output directory; no automatic reruns or replenishment')
    from benchmark.benchmark_runner import BenchmarkManifest, BenchmarkRunner
    from benchmark.propagation_analysis import PropagationAnalyzer
    manifest = BenchmarkManifest(fixture_root=FIXTURES, output_dir=args.output_dir,
        num_benign_repetitions=3, num_repetitions=1, model_name=args.model,
        backend_name=args.backend, vllm_url=args.vllm_url, dry_run=False,
        topologies=['coordinator_workers'], task_families=['research_synthesis'],
        propagation_modes=['one_to_many'])
    BenchmarkRunner(manifest).run(plan=entries)
    PropagationAnalyzer(args.output_dir).analyze()
    # Anonymous detector input sidecars, deliberately separate from audit data.
    from generation.existing_record_protocol import detector_events
    analyzer = PropagationAnalyzer(args.output_dir)
    export = args.output_dir / 'detector_inputs'
    export.mkdir()
    import uuid
    export_index = {}
    for path in sorted((args.output_dir / 'traces').glob('*_trace.json')):
        trace = analyzer._trace_from_dict(json.loads(path.read_text()), path)
        name = uuid.uuid4().hex + '.json'
        (export / name).write_text(json.dumps(detector_events(trace)))
        export_index[name] = str(path)
    (args.output_dir / 'detector_export_index_private.json').write_text(json.dumps(export_index, indent=2))


if __name__ == '__main__':
    main()
