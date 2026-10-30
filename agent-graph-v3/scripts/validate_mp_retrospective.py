"""Bounded, opt-in validation only; never invokes full-dataset analyze()."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from benchmark.mp_retrospective import evaluate_mp, VERSION
from benchmark.propagation_analysis import PropagationAnalyzer, CleanReference
from generation.event_graph_builder import DependsOnGraphBuilder


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    return PropagationAnalyzer._trace_from_dict(json.loads(path.read_text()), path)


def excerpt(event):
    return {'event_id': event.event_id, 'type': event.event_type.value, 'role': event.agent_role,
            'depends_on': event.depends_on,
            'content': event.tool_arguments if event.tool_arguments else event.output_text}


APPROVED_SAMPLE_SHA256 = '5c3bb8239214d776729acd6fe5c1a3a7e51c73d9bfd5167edff0ad0134505c57'


def validate(sample_path, output):
    if digest(sample_path) != APPROVED_SAMPLE_SHA256:
        raise ValueError('Only the frozen phase-1 sample is authorized by this runner')
    for protected in ('production_run', 'preliminary_analysis_oct16', 'memory_poisoning_delivery_audit_oct16'):
        if output.resolve().is_relative_to(Path(protected).resolve()):
            raise ValueError('Output must not be inside an existing production/audit directory')
    manifest = json.loads(sample_path.read_text())
    if len(manifest['entries']) > 64:
        raise ValueError('Only the approved bounded sample (<=64 entries) is supported')
    if output.exists():
        raise ValueError('Refusing to overwrite an output directory')
    for name, expected in manifest['input_sha256'].items():
        if digest(Path(name)) != expected:
            raise ValueError(f'Frozen input changed: {name}')
    baseline = json.loads((sample_path.parent / 'baseline/files.sha256.json').read_text())
    changed = [p for p, expected in baseline.items() if not Path(p).exists() or digest(Path(p)) != expected]
    if changed:
        raise ValueError(f'Frozen legacy/config/results inputs changed: {changed}')
    output.mkdir(parents=True)
    results, legacy_checks = [], []
    analyzer = PropagationAnalyzer.__new__(PropagationAnalyzer)
    for entry in manifest['entries']:
        trace = load(Path(entry['path']))
        controls = [load(Path(p)) for p in entry['clean_paths']]
        fixture = json.loads(Path(entry['fixture_path']).read_text())
        revised = evaluate_mp(trace, controls, fixture, enabled=True)
        old = entry['legacy']
        if old:
            ref = CleanReference(trace.metadata['fixture_id'], trace.metadata['task_family'],
                                 trace.metadata['topology'], 'memory_enabled', controls,
                                 [c.metadata['repetition_index'] for c in controls])
            replay = analyzer._analyze_single_trace(trace, ref, 'LEP_MEMORY_POISONING',
                        trace.metadata['propagation_mode'], DependsOnGraphBuilder(),
                        fixture_id=trace.metadata['fixture_id']).to_dict()
            keys = ['propagation_occurred', 'anomalous_event_ids', 'anomaly_details',
                    'event_depth', 'handoff_depth', 'num_downstream_agents', 'num_benign_reference_runs']
            differences = {k: {'saved': old[k], 'replayed': replay[k]} for k in keys if old[k] != replay[k]}
            legacy_checks.append({'trace_id': trace.trace_id, 'matched': not differences, 'differences': differences})
        relevant = set((old or {}).get('anomalous_event_ids', []))
        for m in revised['delivery']['mutations']:
            relevant.update([m['origin_event_id'], m['call_event_id'], m['overwritten_by_event_id']])
        for d in revised['delivery']['deliveries']:
            relevant.add(d['retrieval_event_id'])
        comparisons = {c['event_id']: c for c in revised['event_comparisons']}
        clean_evidence = []
        affected_slots = {tuple(comparisons[id]['record_aware_slot']) for id in relevant
                          if id in comparisons and comparisons[id]['record_aware_slot']}
        from benchmark.mp_retrospective import record_slot
        for c in controls:
            clean_evidence.append({'trace_id': c.trace_id, 'path': c.file_path,
                                   'repetition_index': c.metadata['repetition_index'],
                                   'task_evaluation': c.metadata.get('evaluation'),
                                   'events_matching_flagged_slots': [excerpt(e) for e in c.events if record_slot(e) in affected_slots],
                                   'final_events': [excerpt(e) for e in c.events if e.event_type.value == 'final_response']})
        new_state = revised['endpoints']['behavioral_propagation']['state']
        old_state = ('positive' if old['propagation_occurred'] else 'negative') if old else 'not_analysis_ready'
        results.append({'trace_id': trace.trace_id, 'path': entry['path'],
                        'selection_reason': entry['selection_reason'], 'stratum': entry['stratum'],
                        'legacy': old, 'legacy_state': old_state, 'revised_state': new_state,
                        'changed_classification': bool(old) and old_state != new_state,
                        'change_explanation': revised['endpoints']['behavioral_propagation']['reason'],
                        'revised': revised,
                        'event_evidence': [excerpt(e) for e in trace.events if e.event_id in relevant],
                        'final_events': [excerpt(e) for e in trace.events if e.event_type.value == 'final_response'],
                        'task_evaluation': trace.metadata.get('evaluation'), 'clean_evidence': clean_evidence})
    eligible = [r for r in results if r['legacy']]
    endpoint_counts = {ep: dict(Counter(r['revised']['endpoints'][ep]['state'] for r in eligible))
                       for ep in ('exposure', 'adoption', 'rejection', 'behavioral_propagation', 'final_answer_impact')}
    summary = {'version': VERSION, 'sample_sha256': digest(sample_path),
               'legacy_mp_analysis_ready_population': manifest['legacy_mp_analysis_ready_population'],
               'bounded_legacy_ready_sample': len(eligible), 'noneligible_controls': len(results)-len(eligible),
               'legacy_ready_not_reanalyzed': manifest['legacy_mp_analysis_ready_population']-len(eligible),
               'legacy_positive_count_in_sample': sum(r['legacy_state']=='positive' for r in eligible),
               'endpoint_counts_within_legacy_ready_sample': endpoint_counts,
               'revised_evaluable_sample': sum(r['revised']['evaluable_behavioral_propagation'] for r in eligible),
               'changed_classifications': sum(r['changed_classification'] for r in eligible),
               'legacy_replay_checks': legacy_checks,
               'frozen_files_verified_unchanged': len(baseline),
               'implementation_sha256': {p: digest(Path(p)) for p in (
                   'benchmark/memory_delivery_audit.py', 'benchmark/mp_retrospective.py',
                   'scripts/validate_mp_retrospective.py', 'tests/test_mp_retrospective.py',
                   'docs/memory_propagation_measurement.md')},
               'interpretation': 'Bounded diagnostic validation, not replacement production results or a propagation-rate estimate.'}
    (output/'cases.jsonl').write_text(''.join(json.dumps(r, sort_keys=True)+'\n' for r in results))
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    lines = ['# Bounded MP validation', '', summary['interpretation'], '',
             f"Legacy MP population: {summary['legacy_mp_analysis_ready_population']}; reviewed eligible: {len(eligible)}; unassessed: {summary['legacy_ready_not_reanalyzed']}; noneligible controls: {len(results)-len(eligible)}.",
             f"Revised behavior-evaluable sample: {summary['revised_evaluable_sample']}. Unknowns remain in the sample denominator.",
             f"Legacy replay: {sum(c['matched'] for c in legacy_checks)}/{len(legacy_checks)} match.", '',
             'Endpoint counts: `'+json.dumps(endpoint_counts)+'`', '',
             'Full exact contents, numeric comparisons and clean-slot evidence are in cases.jsonl.', '']
    for r in results:
        lines += ['## '+r['trace_id'], '', f"Path: `{r['path']}`", '',
                  f"Selection: {r['selection_reason']}", '',
                  f"Legacy **{r['legacy_state']}** → revised **{r['revised_state']}**: {r['change_explanation']}.", '',
                  'Endpoints: `'+json.dumps(r['revised']['endpoints'])+'`', '',
                  'Mutation/extraction evidence: `'+json.dumps(r['revised']['mutation_fact_evidence'])+'`', '']
        for e in r['event_evidence']:
            lines += [f"- Event {e['event_id']} ({e['type']}, {e['role']}): {json.dumps(e['content'],ensure_ascii=False)}"]
        lines += ['', 'Flagged-event record-aware comparisons:', '']
        ids = set((r['legacy'] or {}).get('anomalous_event_ids', []))
        for c in r['revised']['event_comparisons']:
            if c['event_id'] in ids:lines += ['- `'+json.dumps(c)+'`']
        lines += ['', 'Final response:', '']
        lines += ['- '+json.dumps(e,ensure_ascii=False) for e in r['final_events']]
        lines += ['', 'Matched clean references (full aligned intermediate events in cases.jsonl):', '']
        for c in r['clean_evidence']:
            lines += [f"- `{c['path']}`, repetition {c['repetition_index']}, task success: {(c['task_evaluation'] or {}).get('task_success')}. Final: "+json.dumps(c['final_events'],ensure_ascii=False)]
        lines += ['']
    (output/'report.md').write_text('\n'.join(lines)+'\n')
    if not all(c['matched'] for c in legacy_checks):
        raise RuntimeError('Legacy replay mismatch; inspect report, do not release')
    print(json.dumps({k:v for k,v in summary.items() if k!='legacy_replay_checks'},indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sample', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    validate(args.sample, args.output)
