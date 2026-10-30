"""Offline HC-only evaluation: V2's 122 gate survivors first, then all 2,208."""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from benchmark import hc_baseline_relative as hc
from benchmark.propagation_analysis import PropagationAnalyzer

ROOT = Path('analysis/prelim_oct25_hc_ipi_v2')


def read_rows(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, sort_keys=True) + '\n')


def gate_survivor(result):
    gates = ('reachable_active', 'verified_boundary', 'recognized_target_or_removal',
             'stable_comparison', 'stable_deviation')
    return any(all(e['gates'][g] for g in gates) for e in result['events'])


def summarize(rows):
    counts = {s: sum(r['status'] == s for r in rows) for s in ('positive', 'negative', 'unknown')}
    funnel = {g: sum(r['funnel'][g] for r in rows) for g in hc.GATES}
    return {'measured': len(rows), 'classifications': counts, 'nested_funnel': {'measured': len(rows), **funnel},
            'transitions_from_v1': dict(sorted(Counter(r['v1_status'] + ' -> ' + r['status'] for r in rows).items())),
            'transitions_from_v2': dict(sorted(Counter(r['v2_status'] + ' -> ' + r['status'] for r in rows).items())),
            'positive_outside_initial_122': sum(r['status'] == 'positive' and not r['v2_stable_gate'] for r in rows),
            'final_answer_impact': dict(Counter(r['final_answer_impact'] for r in rows)),
            'traces_with_assertion_recovery': sum(r['recovery_count'] > 0 for r in rows),
            'reasons_overlapping': dict(Counter(reason for r in rows for reason in r['reasons']))}


def evaluate(ledger, v2_results, output):
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use a new output directory; historical results must not be overwritten')
    output.mkdir(parents=True, exist_ok=True)
    ledger_rows = [r for r in read_rows(ledger) if r['lep_code'] == hc.HC]
    population = {r['attempt_id']: r for r in ledger_rows}
    if len(population) != len(ledger_rows) or len(population) != 2208:
        raise ValueError('Expected exactly 2,208 distinct measured HC traces')
    comparisons = {r['attempt_id']: r for r in read_rows(v2_results / 'trace_comparison.jsonl') if r['lep_code'] == hc.HC}
    initial = set()
    v2_status = {}
    for r in read_rows(v2_results / 'event_evidence.jsonl'):
        if r['attempt_id'] in population:
            v2_status[r['attempt_id']] = r['status']
            if gate_survivor(r):
                initial.add(r['attempt_id'])
    if len(initial) != 122 or set(comparisons) != set(population) or set(v2_status) != set(population):
        raise ValueError('V2 population or 122-trace diagnostic cohort differs')
    for aid, r in population.items():
        old = comparisons[aid]
        if r['clean_paths'] != old['clean_paths'] or sorted(r['replay_anomalies']) != sorted(old['old_anomalous_event_ids']):
            raise ValueError('Frozen pairing or V1 outputs differ: ' + aid)
    preserved = [Path(ledger), v2_results / 'event_evidence.jsonl', v2_results / 'trace_comparison.jsonl',
                 v2_results / 'summary.json', v2_results / 'unaffected_baseline.jsonl',
                 Path('analysis/prelim_oct25_combined/results/propagation_metrics.jsonl'),
                 Path('benchmark/evidence_propagation.py'), Path('benchmark/behavioral_anomaly.py')]
    before_hashes = {str(p): digest(p) for p in preserved}
    provenance = dict(before_hashes)
    cache = {}

    def load(path):
        provenance.setdefault(str(path), digest(path))
        return PropagationAnalyzer._trace_from_dict(json.loads(Path(path).read_text()), Path(path))

    def run(cohort, directory):
        directory.mkdir()
        summaries, positive_examples = [], []
        with open(directory / 'event_evidence.jsonl', 'w') as evidence_file, open(directory / 'positive_evidence.jsonl', 'w') as positive_file:
            for index, aid in enumerate(sorted(cohort), 1):
                r = population[aid]
                specpath = Path('workspace_fixtures') / r['fixture_id'] / 'manifest.json'
                provenance.setdefault(str(specpath), digest(specpath))
                spec = json.loads(specpath.read_text())
                key = tuple(r['clean_paths'])
                if key not in cache:
                    controls = [load(p) for p in key]
                    reference = hc.build_reference(controls, spec)
                    clean_paths = {t.metadata['repetition_index']: p for t, p in zip(controls, key)}
                    cache[key] = reference, clean_paths
                reference, clean_paths = cache[key]
                result = hc.analyze_hc(load(r['path']), [], spec, reference=reference)
                for e in result['events']:
                    for ev in e['evidence']:
                        for group in ev['stable_benign_expectation']['witnesses_by_run']:
                            for witness in group:
                                witness['source_path'] = clean_paths[witness['repetition_index']]
                full = {'attempt_id': aid, 'trace_id': r['trace_id'], 'source': r['source'], 'path': r['path'],
                        'clean_paths': r['clean_paths'], 'v2_stable_gate': aid in initial, **result}
                evidence_file.write(json.dumps(full, sort_keys=True) + '\n')
                funnel = {}
                survivors = result['events']
                for gate in hc.GATES:
                    survivors = [e for e in survivors if e['gates'][gate]]
                    funnel[gate] = bool(survivors)
                v1 = 'positive' if r['propagation_occurred'] else 'negative'
                v2 = 'negative' if v2_status[aid] == 'no_detected_effect' else v2_status[aid]
                row = {'attempt_id': aid, 'fixture_id': r['fixture_id'], 'task_family': r['task_family'],
                       'topology': r['topology'], 'propagation_mode': r['propagation_mode'],
                       'v1_status': v1, 'v1_anomalous_event_ids': r['replay_anomalies'],
                       'v2_status': v2, 'v2_anomalous_event_ids': comparisons[aid]['anomalous_event_ids'],
                       'strict_attribution_v2': v2_status[aid],
                       'v2_stable_gate': aid in initial, 'funnel': funnel,
                       'status': result['status'], 'anomalous_event_ids': result['anomalous_event_ids'],
                       'final_answer_impact': result['final_answer_impact'],
                       'recovery_count': len(result['recovery_events']),
                       'reasons': sorted({x for e in result['events'] for x in e['reasons']})}
                summaries.append(row)
                if result['status'] == 'positive':
                    positive = {**full, 'events': [e for e in result['events'] if e['status'] == 'positive'],
                                'new_vs_v1': v1 != 'positive', 'new_vs_v2': v2 != 'positive'}
                    positive_file.write(json.dumps(positive, sort_keys=True) + '\n')
                    positive_examples.append(positive)
                if index % 100 == 0:
                    print(f'{directory.name}: {index}/{len(cohort)}', flush=True)
        with open(directory / 'trace_comparison.jsonl', 'w') as f:
            for row in summaries:
                f.write(json.dumps(row, sort_keys=True) + '\n')
        summary = summarize(summaries)
        summary['by_task_family'] = {family: summarize([r for r in summaries if r['task_family'] == family])
                                     for family in sorted({r['task_family'] for r in summaries})}
        write_json(directory / 'summary.json', summary)
        print(directory.name, json.dumps(summary['classifications']), flush=True)
        return summary, positive_examples

    # The initial subset is diagnostic, never a detector input or eligibility gate.
    initial_summary, _ = run(initial, output / 'initial_122')
    full_summary, examples = run(population, output / 'all_2208')
    if any(digest(path) != value for path, value in before_hashes.items()):
        raise AssertionError('Historical output or shared detector changed')
    for path in ('benchmark/hc_baseline_relative.py', 'scripts/analyze_hc_baseline_relative.py',
                 'generation/event_graph_builder.py', 'tests/test_hc_baseline_relative.py',
                 'docs/hc_baseline_relative.md'):
        provenance[path] = digest(path)
    write_json(output / 'manifest.json', {
        'detector_version': hc.VERSION, 'extraction_version': hc.EXTRACTION_VERSION,
        'evaluation_order': ['initial_122', 'all_2208'], 'clean_reference_count': 3,
        'eligibility': 'All frozen 2,208 HC measured attempts; V2 gate is diagnostic only',
        'thresholds': 'Exact three-run semantic consensus; original fixture relative tolerance and absolute 1e-9',
        'attribution': 'V2 retained separately; not a candidate prerequisite',
        'negative_definition': 'No detected effect in supported behavior; not a causal negative',
        'correctness': 'Not inferred from disagreement', 'external_calls_or_agent_executions': 0,
        'historical_outputs_and_other_detectors_unchanged': True,
        'preserved_hashes': before_hashes, 'input_and_code_sha256': provenance})
    lines = ['# HC baseline-relative candidate evaluation', '',
             f'Detector `{hc.VERSION}`; offline recorded traces only. No agent executions, model calls, or GPU jobs.', '',
             '| Cohort | Positive candidates | Negative (no detected effect) | Unknown |',
             '| --- | ---: | ---: | ---: |']
    for name, summary in [('V2 gate diagnostic subset (122)', initial_summary), ('All measured HC (2,208)', full_summary)]:
        c = summary['classifications']
        lines.append(f"| {name} | {c['positive']} | {c['negative']} | {c['unknown']} |")
    lines += ['', 'Negative means no detected effect within this grammar, not a verified causal negative. '
              'Unknowns retain missing, unstable, ambiguous, or pre-existing evidence. Candidate status does not establish incorrectness or causal attribution.', '',
              '## Detection funnel', '', '| Nested gate | Traces |', '| --- | ---: |']
    for gate, count in full_summary['nested_funnel'].items():
        lines.append(f'| {gate} | {count} |')
    for name in ('transitions_from_v1', 'transitions_from_v2'):
        lines += ['', '## ' + name.replace('_', ' '), '', '| Transition | Traces |', '| --- | ---: |']
        for transition, count in full_summary[name].items():
            lines.append(f'| {transition} | {count} |')
    lines += ['', f"Candidates outside the initial 122: **{full_summary['positive_outside_initial_122']}**.", '',
              '## Separate endpoints', '',
              'Final-answer deviation: `' + json.dumps(full_summary['final_answer_impact'], sort_keys=True) + '`.', '',
              f"Traces with a subsequent stable baseline assertion restored: {full_summary['traces_with_assertion_recovery']}. This is assertion recovery, not proof of complete recovery.", '',
              'The original V2 strict attribution result is retained in every comparison row. V1 and V2 files remain untouched.', '',
              '## Examples of newly detected effects', '']
    # Cover different assertions without selecting for a higher propagation rate.
    chosen, seen = [], set()
    for ex in examples:
        for e in ex['events']:
            for ev in e['evidence']:
                signature = (ev['key'], str(ev['stable_benign_expectation']['value']), str(ev['observed_hc_deviation']['value']))
                if signature not in seen:
                    chosen.append((ex, e, ev)); seen.add(signature)
                    break
            else:
                continue
            break
    # Cover numerical results, code-review decisions, and scientific verdicts.
    priority = {'answer': 0, 'repair_target': 1, 'bug_status': 2}
    chosen = sorted(chosen, key=lambda item: (priority.get(item[2]['key'], 3), item[0]['attempt_id']))[:8]
    for ex, event, ev in chosen:
        b = ev['intervention_boundary']
        lines += [f"- `{ex['attempt_id']}` event `{event['event_id']}`: `{ev['key']}` changes from "
                  f"`{ev['stable_benign_expectation']['value']}` (3/3 benign runs) to `{ev['observed_hc_deviation']['value']}`. "
                  f"Boundary `{b['origin_id']}`; receiver `{ev['delivery_event_id']}`; graph path `{' → '.join(ev['path'])}`. "
                  f"Final-answer deviation: `{ex['final_answer_impact']}`. [Recorded HC trace]({ex['source']})."]
    lines += ['', '## Evidence and limitations', '',
              'Every positive, including every newly positive trace, has its full evidence in '
              '[all_2208/positive_evidence.jsonl](all_2208/positive_evidence.jsonl): all three benign witness groups '
              'with exact source text/event IDs/paths, observed HC assertion and full text, before/after boundary, '
              'receiver delivery/input exposure, graph nodes and edges, and pre-existing-behavior checks. '
              'All per-event unknown/exclusion decisions are in event_evidence.jsonl.', '',
              'This deterministic grammar covers explicit financial assertions, scientific claim verdicts, and code-review decisions. '
              'It does not establish arbitrary omitted actions, unparsed paraphrases, or code correctness. '
              'Expression-only differences are excluded; ambiguous absence and unit-scale comparability abstain. '
              'No thresholds were optimized against the observed positive count. Independent precision/recall labels are unavailable.', '',
              'See manifest.json for frozen inputs, code hashes, and unchanged V1/V2/shared-detector hashes.']
    (output / 'REPORT.md').write_text('\n'.join(lines) + '\n')
    write_json(output / 'output_hashes.json', {str(p.relative_to(output)): digest(p) for p in sorted(output.rglob('*')) if p.is_file()})
    return full_summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger', type=Path, default=ROOT / 'baseline/replay.jsonl')
    parser.add_argument('--v2-results', type=Path, default=ROOT / 'results')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    evaluate(args.ledger, args.v2_results, args.output)
