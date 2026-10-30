"""Audit two saved unified analyses; no executions or external services."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from benchmark.production_propagation import write_csv, write_jsonl
from benchmark.id_trc_v2 import ID, TRC, VERSION


def rows(path):
    with path.open() as f:
        for line in f:
            yield json.loads(line)


def classification_table(old, new, fields):
    groups = defaultdict(list)
    for aid, r in new.items():
        groups[tuple(r[k] for k in fields)].append((old[aid], r))
    result = []
    for keys, pairs in sorted(groups.items()):
        n = len(pairs)
        op = sum(a['classification'] == 'positive' for a,b in pairs)
        np = sum(b['classification'] == 'positive' for a,b in pairs)
        result.append(dict(zip(fields, keys)) | {'measured': n, 'old_positive': op, 'new_positive': np,
            'old_rate_pct': round(100*op/n, 4), 'new_rate_pct': round(100*np/n, 4),
            'added': sum(a['classification'] != 'positive' and b['classification'] == 'positive' for a,b in pairs),
            'removed': sum(a['classification'] == 'positive' and b['classification'] != 'positive' for a,b in pairs),
            'negative': sum(b['classification'] == 'negative' for a,b in pairs),
            'unknown': sum(b['classification'] == 'unknown' for a,b in pairs),
            'incomparable': sum(b['classification'] == 'incomparable' for a,b in pairs)})
    return result


def markdown(records, columns):
    return '\n'.join(['| ' + ' | '.join(columns) + ' |', '| ' + ' | '.join('---' for _ in columns) + ' |'] +
        ['| ' + ' | '.join(str(r.get(k, '')) for k in columns) + ' |' for r in records])


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--before', type=Path, default=Path('analysis/full_production_propagation'))
    p.add_argument('--after', type=Path, default=Path('analysis/full_production_id_trc_v2'))
    args = p.parse_args(argv)
    out = args.after / 'review'
    out.mkdir(parents=True, exist_ok=True)
    old = {r['attempt_id']: r for r in rows(args.before/'propagation_metrics.jsonl')}
    new = {r['attempt_id']: r for r in rows(args.after/'propagation_metrics.jsonl')}
    if old.keys() != new.keys():
        raise AssertionError('Measured attempt population changed')
    unaffected = [aid for aid,r in new.items() if r['lep_code'] not in (ID,TRC)]
    diffs = [aid for aid in unaffected if old[aid] != new[aid]]
    if diffs:
        (out/'unexpected_unchanged_lep_differences.json').write_text(json.dumps(
            {aid: {k: {'old': old[aid].get(k), 'new':new[aid].get(k)} for k in old[aid].keys() | new[aid].keys()
                   if old[aid].get(k) != new[aid].get(k)} for aid in diffs}, indent=2))
        raise AssertionError(f'{len(diffs)} unaffected metric rows differ')
    total = classification_table(old, new, ['lep_code'])
    configs = classification_table(old, new, ['lep_code','task_family','topology','propagation_mode'])
    configs = [r for r in configs if r['lep_code'] in (ID,TRC)]
    write_csv(out/'before_after_by_lep.csv', total)
    write_csv(out/'before_after_by_configuration.csv', configs)
    transitions = Counter((r['lep_code'],old[aid]['classification'],r['classification']) for aid,r in new.items())
    transition_rows = [{'lep_code':k[0],'old':k[1],'new':k[2],'count':v} for k,v in sorted(transitions.items())]
    write_csv(out/'classification_transitions.csv', transition_rows)
    stats = defaultdict(Counter)
    evidence_samples = defaultdict(list)
    added_events = []
    changed_count = 0
    # Every changed positive status includes full before and after evidence,
    # including unknown/incomparable events, never only a binary delta.
    with (out/'changed_trace_evidence.jsonl').open('w') as changes:
        for native in rows(args.after/'detector_evidence.jsonl'):
            aid = native['attempt_id']; r = new[aid]; before = old[aid]
            if r['lep_code'] not in (ID,TRC): continue
            report = native['result']
            grouping = [r['lep_code'], '|'.join(r[k] for k in ('lep_code','task_family','topology','propagation_mode'))]
            lifecycle = report.get('lifecycle', {})
            adoption_ids = set(lifecycle.get('adoption_event_ids', []))
            added = adoption_ids - set(before['anomalous_event_ids'])
            adopted = [e for e in report['events'] if e['event_id'] in adoption_ids]
            new_adopted = [e for e in adopted if e['event_id'] in added]
            matched_legacy = [e for e in report['events'] if any(x['rule']=='legacy_matched_slot_effect' for x in e['evidence'])]
            for key in grouping:
                s = stats[key]; s['measured'] += 1
                s['classification:'+r['classification']] += 1
                s['comparability:'+report['clean_reference_comparability']['status']] += 1
                if r['lep_code'] == ID:
                    s['shape_matched_controls:'+str(report['coverage']['shape_matched_controls'])] += 1
                s['verified_boundary_traces'] += any(b['verified'] for b in report['boundaries'])
                s['delivered_traces'] += any(b['exposure_ids'] for b in report['boundaries'])
                s['recognized_boundary_traces'] += any(b.get('changes') for b in report['boundaries'])
                s['adoption_traces'] += bool(adoption_ids)
                s['adoption_events'] += len(adoption_ids)
                s['new_adoption_events'] += len(added)
                s['traces_with_new_adoption_events'] += bool(added)
                s['adoption_events_in_new_positive_traces'] += len(adoption_ids) if before['classification'] != 'positive' and r['classification']=='positive' else 0
                s['unmatched_slot_adoption_events'] += report['coverage'].get('unmatched_slot_adoption_events',0)
                s['forwarding_traces'] += bool(lifecycle.get('forwarding_event_ids'))
                s['forwarding_events'] += len(lifecycle.get('forwarding_event_ids',[]))
                s['cross_agent_traces'] += bool(lifecycle.get('cross_agent_event_ids'))
                s['cross_agent_events'] += len(lifecycle.get('cross_agent_event_ids',[]))
                s['recovered_traces'] += bool(report['recovery_events'])
                s['recovered_adoption_events'] += len({e['seed_event_id'] for e in report['recovery_events']})
                s['recovery_assertion_links'] += len(report['recovery_events'])
                s['transient:'+report['transient_propagation']] += 1
                s['final_impact:'+report['final_answer_impact']] += 1
                s['persistent:'+report['persistent_effect']] += 1
                s['legacy_matched_effect_events'] += len(matched_legacy)
                s['legacy_only_positive_traces'] += r['classification']=='positive' and not adoption_ids and r['lep_code']==TRC
                if r['classification']=='positive':
                    s['event_depth:'+str(r['event_depth'])] += 1
                    paths = [x['path'] for e in report['events'] if e['status']=='positive' for x in e['evidence']]
                    s['max_supported_path_depth:'+str(max((len(p)-1 for p in paths), default=0))] += 1
                    s['handoff_depth:'+str(r['handoff_depth'])] += 1
                    s['downstream_agent_count:'+str(r['num_downstream_agents'])] += 1
                    for agent in r['downstream_agents_affected']:
                        s['downstream_agent:'+agent] += 1
            for event in new_adopted:
                added_events.append({'attempt_id':aid,'trace_id':r['trace_id'],'trace_path':r['trace_path'],
                    'task_family':r['task_family'],'topology':r['topology'],'propagation_mode':r['propagation_mode'],
                    'old_trace_classification':before['classification'],'new_trace_classification':r['classification'],
                    'event':event,'boundaries':report['boundaries'],'recovery_events':report['recovery_events'],
                    'final_answer_impact':report['final_answer_impact']})
            transition = before['classification'] + '->' + r['classification']
            if (before['classification']=='positive') != (r['classification']=='positive'):
                changes.write(json.dumps({'attempt_id':aid, 'trace_id':r['trace_id'],'lep_code':r['lep_code'],
                    'transition':transition,'before':before,'after':r,'detector_evidence':native}, sort_keys=True)+'\n')
                changed_count += 1
            sample_key = r['lep_code']+'|'+transition
            if len(evidence_samples[sample_key]) < 3:
                evidence_samples[sample_key].append({'attempt_id':aid,'trace_id':r['trace_id'],'trace_path':r['trace_path'],
                    'old_event_ids':before['anomalous_event_ids'],'new_event_ids':r['anomalous_event_ids'],
                    'clean_comparability':report['clean_reference_comparability']['status'],
                    'coverage':report['coverage'],'final_impact':report['final_answer_impact']})
    write_jsonl(out/'new_trc_adoption_event_evidence.jsonl', added_events)
    (out/'lifecycle_and_coverage.json').write_text(json.dumps(stats, indent=2,sort_keys=True)+'\n')
    write_csv(out/'lifecycle_and_coverage.csv', [{'configuration': k, **dict(v)} for k,v in sorted(stats.items())])
    (out/'transition_examples.json').write_text(json.dumps(evidence_samples,indent=2,sort_keys=True)+'\n')
    summary = {'detector_version':VERSION,'measured_denominator_unchanged':len(new),
        'unchanged_lep_rows_identical':len(unaffected),'changed_positive_status_evidence_records':changed_count,
        'new_trc_adoption_events':len(added_events),'before_after':total,'transitions':transition_rows}
    (out/'verification.json').write_text(json.dumps(summary,indent=2)+'\n')
    cols = ['lep_code','measured','old_positive','new_positive','old_rate_pct','new_rate_pct','added','removed','negative','unknown','incomparable']
    focus = [r for r in configs if r['topology']=='coordinator_workers' and
             (r['lep_code']==ID and r['propagation_mode']=='one_to_many' or r['lep_code']==TRC and r['task_family']=='financial_analysis')]
    report = ['# ID/TRC v2 offline production comparison',
        'Measured population: '+str(len(new))+'. HC/IPI/MP metric rows are identical: '+str(len(unaffected))+'. No eligibility changes.',
        '**Interpretation:** mixed-detector candidate detection fractions, not independently validated propagation probabilities. Unknown and incomparable cases remain in the measured denominator; neither is a negative. “Not detected” final impact means restoration of the recognized boundary assertions, not independently judged answer correctness.',
        '## All LEPs', markdown(total,cols), '## Priority configurations',
        markdown(focus,['task_family','topology','propagation_mode']+cols),
        '## All ID/TRC configurations',markdown(configs,['task_family','topology','propagation_mode']+cols),
        '## Transitions',markdown([r for r in transition_rows if r['lep_code'] in (ID,TRC)],['lep_code','old','new','count']),
        '## Lifecycle and coverage',
        'Event counts count each event once, even when it contains several corrupted facts. A new adoption event is absent from the old anomalous-event set; it can occur in a previously positive trace. Writes are forwarding actions; cross-agent transmission additionally requires an actual receiver and recorded content transport. Recovery counts are assertion-level restoration, not human correctness judgments.',
        '```json\n'+json.dumps({k:dict(stats[k]) for k in (ID,TRC)},indent=2,sort_keys=True)+'\n```',
        '## Evidence files',
        '- `changed_trace_evidence.jsonl`: all added/removed positive traces, old and new event evidence, intervention boundaries, controls and downstream paths.\n- `new_trc_adoption_event_evidence.jsonl`: every newly identified adoption event, affected assertion and original/corrupted values, source boundary, downstream path, recovery.\n- `lifecycle_and_coverage.json`: lifecycle, comparability, event depth, handoff depth and agent-count distributions by LEP and configuration.\n- `classification_transitions.csv`, `before_after_by_configuration.csv`: complete configuration comparisons.\n- `transition_examples.json`: trace pointers for review.\n- `../detector_evidence.jsonl`: complete evidence for every measured trace.']
    (out/'comparison.md').write_text('\n\n'.join(report)+'\n')
    print(json.dumps(summary,indent=2))


if __name__ == '__main__':
    main()
