"""Separate development summaries; never overwrites production propagation totals."""
from collections import Counter

from benchmark.semantic_mp_v2.measurement_contract import VERSION, fingerprint


def summarize_measurements(results):
    if any(r['version'] != VERSION for r in results):
        raise ValueError('cannot pool different measurement contracts')
    pipeline_configs = {}
    for result in results:
        config = result.get('configuration')
        if config:
            pipeline = {k: v for k, v in config.items() if k != 'registry'}
            pipeline_configs[fingerprint(pipeline)] = pipeline
    if len(pipeline_configs) > 1:
        raise ValueError('cannot pool different extractor or alignment configurations')
    endpoint_names = sorted({ep for r in results for ep in r['endpoints']})
    endpoints = {}
    for ep in endpoint_names:
        states = Counter(r['endpoints'].get(ep, {'state': 'unknown'})['state'] for r in results)
        measured = states['positive'] + states['negative']
        endpoints[ep] = {
            'eligible': len(results), 'positive': states['positive'], 'negative': states['negative'],
            'unknown': states['unknown'], 'assessment_coverage': measured / len(results) if results else None,
            # Development counts only; no headline propagation probability.
        }
    profiles = [row['clean_comparison'] for r in results for row in r.get('claim_assessments', [])]
    reasons = Counter(g['reason'] for r in results for ep in r['endpoints'].values() for g in ep.get('gaps', []))
    return {'version': VERSION, 'development_only': True, 'independent_validation': False,
            'pipeline_configurations': pipeline_configs, 'endpoints': endpoints,
            'claim_comparison_count': len(profiles),
            'clean_comparison_counts': dict(sum((Counter(p['counts']) for p in profiles), Counter())),
            'clean_support_tiers': dict(Counter(p['tier'] for p in profiles)),
            'gap_counts': dict(reasons),
            'registry_snapshots': {fingerprint(r['configuration']['registry']): r['configuration']['registry']
                                   for r in results if 'configuration' in r},
            'configuration_hashes': sorted({r['configuration_sha256'] for r in results if 'configuration_sha256' in r}),
            'alignment_accuracy': 'not independently measured',
            'extraction_accuracy': 'not independently measured'}
