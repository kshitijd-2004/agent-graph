"""Replay at most 64 preselected development cases; emit a blinded review packet.

The input selection records existing baseline results. Selection is frozen before
replay; this tool never searches for examples with favorable new predictions.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from benchmark.semantic_mp_v2.extraction import Extractor
from benchmark.semantic_mp_v2.production_extraction import ProductionExtractor
from benchmark.semantic_mp_v2.measurement import evaluate_trace
from benchmark.semantic_mp_v2.measurement_reporting import summarize_measurements
from benchmark.semantic_mp_v2.sources import resolve_sources
from scripts.measure_semantic_propagation import ROOT, atomic_json, read_trace, implementation_identity


def visible_events(trace):
    fields = ('event_id', 'event_index', 'event_type', 'agent_id', 'agent_role', 'depends_on',
              'tool_name', 'tool_call_id', 'tool_arguments', 'tool_result', 'input_text', 'output_text', 'observable')
    return [{k: e.to_dict().get(k) for k in fields} for e in trace.events]


def run(sample, output, fixture_root):
    corpus = json.loads(sample.read_text())
    entries = corpus['entries']
    if not 1 <= len(entries) <= 64:
        raise ValueError('diagnostic replay is restricted to 1..64 frozen cases')
    output.mkdir(parents=True, exist_ok=False)
    extractor = ProductionExtractor(output / 'extraction_cache')
    predictions, packet, ablations, counts, coverage = [], [], [], Counter(), Counter()
    for entry in entries:
        trace, trace_hash = read_trace(Path(entry['trace_path']))
        controls = [read_trace(Path(p)) for p in entry['clean_paths']]
        bundle = resolve_sources(trace.metadata['fixture_id'], fixture_root)
        baseline = entry['baseline']['measurement']
        result = evaluate_trace(trace, [t for t, _ in controls],
            authoritative_text=bundle['authoritative_text'], extractor=extractor, enabled=True)
        # New decision rules + correct sources, old extraction. This makes any
        # endpoint coverage improvement due solely to rules visible.
        ablation = evaluate_trace(trace, [t for t, _ in controls],
            authoritative_text=bundle['authoritative_text'], extractor=Extractor(), enabled=True)
        ablations.append(ablation)
        attempt = trace.metadata.get('attempt_id') or trace.trace_id
        predictions.append({'attempt_id': attempt, 'trace_id': trace.trace_id, 'stratum': entry['stratum'],
            'baseline_endpoints': baseline['endpoints'], 'measurement': result,
            'original_unknown_reasons': {ep: dict(Counter(g['reason'] for g in v.get('gaps', [])))
                                         for ep, v in baseline['endpoints'].items()},
            'diagnosis': {
                'incomplete_execution': not result['delivery']['complete_execution'],
                'provenance_issues': result['delivery']['issues'],
                'undelivered_origin_ids': [m['origin_event_id'] for m in result['delivery']['mutations']
                    if not any(d['origin_event_id'] == m['origin_event_id'] and d['changed_information_returned']
                               and d['model_context_event_ids'] for d in result['delivery']['deliveries'])],
                'truncated_delivery_ids': [d['retrieval_event_id'] for d in result['delivery']['deliveries']
                    if any(m['origin_event_id'] == d['origin_event_id'] and len(m['after']) > len(d['returned_prefix'])
                           for m in result['delivery']['mutations'])],
                'source_limit': bundle['provenance_limit'],
                'unsupported_relations': result['configuration']['unsupported']}})
        counts.update(x['result']['method'] for x in result.get('extractions', {}).values())
        coverage.update('complete' if x['result']['complete'] else 'partial' if x['result']['propositions']
                        else 'unparsed' for x in result.get('extractions', {}).values())
        delivery = result['delivery']
        packet.append({'attempt_id': attempt, 'trace_id': trace.trace_id,
            'task_family': trace.metadata['task_family'], 'topology': trace.metadata['topology'],
            'propagation_mode': trace.metadata['propagation_mode'], 'trace_path': entry['trace_path'],
            'trace_sha256': trace_hash, 'events': visible_events(trace),
            'mutations': [{k: v for k, v in m.items() if k != 'injection_success'} for m in delivery['mutations']],
            'recorded_deliveries': delivery['deliveries'],
            'clean_references': [{'trace_id': c.trace_id, 'attempt_id': c.metadata.get('attempt_id') or c.trace_id,
                'repetition_index': c.metadata['repetition_index'], 'sha256': h, 'path': p,
                'events': visible_events(c)} for p, (c, h) in zip(entry['clean_paths'], controls)],
            'task_sources': bundle})
    with (output / 'predictions.jsonl').open('w') as handle:
        for row in predictions:
            handle.write(json.dumps(row) + '\n')
    rubric = (ROOT / 'docs/semantic_production_review.md').read_text()
    atomic_json(output / 'review_packet.json', {'rubric_version': 'semantic-endpoint-review-v1',
        'rubric_text': rubric, 'rubric_sha256': hashlib.sha256(rubric.encode()).hexdigest(),
        'development_selection': True, 'independent_labels': False, 'cases': packet})
    atomic_json(output / 'diagnostic_summary.json', {
        'sample_sha256': hashlib.sha256(sample.read_bytes()).hexdigest(),
        'selection': corpus['selection'], 'size': len(entries), 'development_only': True,
        'independent_precision': 'unmeasured', 'independent_recall': 'unmeasured',
        'independent_extraction_accuracy': 'unmeasured',
        'baseline_endpoints': {ep: dict(Counter(e['baseline']['measurement']['endpoints'][ep]['state'] for e in entries))
                               for ep in entries[0]['baseline']['measurement']['endpoints']},
        'new_rules_and_sources_old_extraction': summarize_measurements(ablations),
        'after': summarize_measurements([r['measurement'] for r in predictions]),
        'extraction_methods': dict(counts), 'extraction_coverage': dict(coverage),
        'implementation': implementation_identity(),
        'review_packet_sha256': hashlib.sha256((output / 'review_packet.json').read_bytes()).hexdigest()})
    return {'cases': len(entries), 'output': str(output)}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sample', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--fixture-root', type=Path, default=ROOT / 'workspace_fixtures')
    a = p.parse_args()
    print(json.dumps(run(a.sample, a.output, a.fixture_root)))
