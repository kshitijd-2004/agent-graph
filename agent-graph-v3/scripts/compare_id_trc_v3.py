"""Stable-attempt V2/V3 audit. Reads saved outputs; never runs experiments."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from benchmark.id_trc_v3 import VERSION, TRC, numeric_label
from benchmark.production_propagation import write_csv, write_json, write_jsonl


def rows(path):
    with Path(path).open() as f:
        for line in f:
            yield json.loads(line)


def quality(row):
    if row.get('lifecycle', {}).get('adoption_event_ids'):
        return 'boundary_proven_adoption'
    if row['classification'] == 'positive':
        return 'legacy_only_behavioral_positive_adoption_unresolved'
    return row['classification']


def counts(records):
    result = dict(Counter(r['classification'] for r in records))
    for status in ('positive', 'negative', 'unknown', 'incomparable'):
        result.setdefault(status, 0)
    result['measured'] = len(records)
    trc = [r for r in records if r['lep_code'] == TRC]
    result.update({'trc_' + k: v for k, v in Counter(quality(r) for r in trc).items()})
    for name, key in [('adoption', 'adoption_event_ids'), ('forwarding', 'forwarding_event_ids'),
                      ('cross_agent', 'cross_agent_event_ids')]:
        result[name + '_traces'] = sum(bool(r.get('lifecycle', {}).get(key)) for r in records)
        result[name + '_events'] = sum(len(set(r.get('lifecycle', {}).get(key, []))) for r in records)
    result['recovery_traces'] = sum(bool(r.get('recovery_events')) for r in records)
    result['recovered_adoption_events'] = sum(len({e['seed_event_id'] for e in r.get('recovery_events', []) if 'seed_event_id' in e}) for r in records)
    for field in ('final_answer_impact', 'transient_propagation', 'persistent_effect'):
        for status in ('detected', 'not_detected', 'unknown', 'not_assessed'):
            result[field + ':' + status] = sum(r.get(field) == status for r in records)
    return result


def table(records, cols):
    return '\n'.join(['| ' + ' | '.join(cols) + ' |', '| ' + ' | '.join('---' for _ in cols) + ' |'] +
                     ['| ' + ' | '.join(str(r.get(c, 0)) for c in cols) + ' |' for r in records])


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--before', type=Path, default=Path('analysis/full_production_id_trc_v2'))
    p.add_argument('--after', type=Path, default=Path('analysis/full_production_id_trc_v3'))
    p.add_argument('--protected-hashes', type=Path, default=Path('analysis/id_trc_v3_development/before_hashes.json'))
    args = p.parse_args(argv)
    out = args.after / 'review'
    out.mkdir(parents=True, exist_ok=True)
    old = {r['attempt_id']: r for r in rows(args.before / 'propagation_metrics.jsonl')}
    new = {r['attempt_id']: r for r in rows(args.after / 'propagation_metrics.jsonl')}
    assert old.keys() == new.keys(), 'Measured population changed'
    unaffected = [aid for aid, r in new.items() if r['lep_code'] != TRC]
    unexpected = {aid: {'old': old[aid], 'new': new[aid]} for aid in unaffected if old[aid] != new[aid]}
    write_json(out / 'unexpected_non_trc_differences.json', unexpected)
    assert not unexpected, 'Non-TRC metric rows changed'
    before_native = {r['attempt_id']: r for r in rows(args.before / 'detector_evidence.jsonl') if r['lep_code'] == TRC}
    old_proven = {aid for aid, r in old.items() if r['lep_code'] == TRC and quality(r) == 'boundary_proven_adoption'}
    assert all(set(old[aid]['lifecycle']['adoption_event_ids']) <= set(new[aid]['lifecycle']['adoption_event_ids']) for aid in old_proven)
    assert all(new[aid]['classification'] == 'incomparable' for aid, r in old.items()
               if r['lep_code'] == 'LEP_INPUT_DISREGARD' and r['classification'] == 'incomparable')
    write_jsonl(out / 'original_v2_boundary_proven_traces.jsonl',
                ({'attempt_id': aid, 'trace_id': new[aid]['trace_id'], 'v2_event_ids': old[aid]['lifecycle']['adoption_event_ids'],
                  'v3_event_ids': new[aid]['lifecycle']['adoption_event_ids']} for aid in sorted(old_proven)))
    transitions = Counter((r['lep_code'], old[aid]['classification'], r['classification']) for aid, r in new.items())
    write_csv(out / 'classification_transitions.csv',
              [{'lep_code': k[0], 'v2': k[1], 'v3': k[2], 'count': n} for k, n in sorted(transitions.items())])
    groups = []
    dimensions = [('lep_code',), ('task_family',), ('topology',), ('propagation_mode',),
                  ('lep_code', 'task_family', 'topology', 'propagation_mode')]
    for fields in dimensions:
        grouped = defaultdict(list)
        for aid, row in new.items():
            grouped[tuple(row[f] for f in fields)].append(aid)
        records = []
        for keys, ids in sorted(grouped.items()):
            entry = dict(zip(fields, keys))
            for label, data in [('v2', old), ('v3', new)]:
                entry.update({label + '_' + k: v for k, v in counts([data[aid] for aid in ids]).items()})
            records.append(entry)
        write_csv(out / ('comparison_by_' + '_'.join(fields) + '.csv'), records)
        if fields == ('lep_code',):
            groups = records
    cohorts = defaultdict(list)
    for aid, native in before_native.items():
        r = old[aid]; report = native['result']
        if r['classification'] == 'unknown' and r['task_family'] == 'code_review':
            cohorts['code_review_unknown'].append(aid)
        if r['task_family'] != 'financial_analysis':
            continue
        spec = json.loads((Path('workspace_fixtures') / r['fixture_id'] / 'manifest.json').read_text())
        marker = any(numeric_label(f.get('label', '')) for k, f in spec.get('required_facts', {}).items() if k != 'answer')
        if r['classification'] == 'unknown':
            cohorts['financial_all_unknown'].append(aid)
            # Fable's approximate 119 cohort includes unresolved boundaries in
            # traces that may also have recognized changes at another boundary.
            if any(b['verified'] and not b['changes'] for b in report['boundaries']):
                cohorts['financial_unresolved_boundary_unknown'].append(aid)
        if marker:
            cohorts['financial_numeric_marker_all'].append(aid)
            if r['classification'] == 'unknown':
                cohorts['financial_numeric_marker_unknown'].append(aid)
    membership = defaultdict(list)
    for name, ids in cohorts.items():
        for aid in ids:
            membership[aid].append(name)
    cohort_stats = {name: Counter(total_examined=len(ids), fixtures=len({old[aid]['fixture_id'] for aid in ids})) for name, ids in cohorts.items()}
    new_events, changed, samples, audit_rows, lifecycle_changes = [], [], [], [], []
    sample_counts = Counter()
    new_proofs = 0
    for native in rows(args.after / 'detector_evidence.jsonl'):
        if native['lep_code'] != TRC:
            continue
        aid = native['attempt_id']; r = new[aid]; report = native['result']; before = before_native[aid]['result']
        lifecycle_fields = ('lifecycle', 'recovery_events', 'final_answer_impact', 'persistent_effect', 'transient_propagation')
        delta = {key: {'v2': old[aid].get(key), 'v3': r.get(key)} for key in lifecycle_fields if old[aid].get(key) != r.get(key)}
        if delta:
            lifecycle_changes.append({'attempt_id': aid, 'trace_id': r['trace_id'], 'changes': delta})
        old_effects = {(e['event_id'], p['origin_id'], p['key']) for e in before['events'] for p in e['evidence'] if p['rule'] == 'boundary_provenance_adoption'}
        added = []
        for event in report['events']:
            proofs = [p for p in event['evidence'] if p['rule'] == 'boundary_provenance_adoption'
                      and (event['event_id'], p['origin_id'], p['key']) not in old_effects]
            if not proofs:
                continue
            for proof in proofs:
                assert event['status'] == 'positive' and event['event_type'] in ('reasoning', 'tool_call', 'agent_handoff', 'final_response', 'memory_write')
                assert proof.get('recognizer') in ('qualified_source_element', 'numeric_header_marker')
                assert proof['observed']['status'] == 'asserted'
                assert proof['observed']['value'] == proof['boundary_change']['after']['value']
                assert proof['boundary_change']['before']['value'] != proof['boundary_change']['after']['value']
                assert proof['path'][0] == proof['origin_id'] and proof['path'][-1] == event['event_id'] and len(proof['path']) > 1
                assert proof['receiver_exposure_ids'] or proof['transport_ids'] or any(
                    p['origin_id'] == proof['origin_id'] and p['key'] == proof['key'] for e in report['events']
                    if e['event_id'] in proof['path'][1:-1] for p in e['evidence'])
                assert any(b['origin_id'] == proof['origin_id'] and b['verified'] for b in report['boundaries'])
                new_proofs += 1
            added.append(event['event_id'])
            new_events.append({'attempt_id': aid, 'trace_id': r['trace_id'], 'trace_path': r['trace_path'],
                **{k: r[k] for k in ('task_family', 'topology', 'propagation_mode')},
                'v2_classification': old[aid]['classification'], 'v3_classification': r['classification'],
                'event': event, 'new_proofs': proofs, 'boundaries': report['boundaries'],
                'recovery_events': report['recovery_events'], 'final_answer_impact': report['final_answer_impact']})
        if old[aid]['classification'] != r['classification'] or quality(old[aid]) != quality(r):
            changed.append({'attempt_id': aid, 'trace_id': r['trace_id'], 'trace_path': r['trace_path'],
                'v2_classification': old[aid]['classification'], 'v3_classification': r['classification'],
                'v2_evidence_quality': quality(old[aid]), 'v3_evidence_quality': quality(r),
                'new_adoption_event_ids': added, 'result': report})
        parsed = [b for b in report['boundaries'] if b.get('v3_recognizer', {}).get('status') == 'parsed']
        mutated = [b for b in report['boundaries'] if b.get('v3_added_keys')]
        consumed = [b for b in mutated if b['exposure_ids'] and b['after'].strip()]
        ambiguity = any(b.get('v3_recognizer', {}).get('status') == 'ambiguous' for b in report['boundaries']) or any(
            'ambiguous' in s for e in report['events'] for s in e['reasons'])
        detail = {'attempt_id': aid, 'trace_id': r['trace_id'], 'cohorts': membership[aid],
                  'parsed_boundaries': len(parsed), 'verified_source_mutations': len(mutated),
                  'consumed_mutations': len(consumed), 'new_adoption_event_ids': added,
                  'rejected_for_ambiguity': ambiguity, 'v2': old[aid]['classification'], 'v3': r['classification']}
        if membership[aid]:
            audit_rows.append(detail)
        for name in membership[aid]:
            stats = cohort_stats[name]
            stats['successfully_parsed_boundaries_traces'] += bool(parsed)
            stats['successfully_parsed_boundaries'] += len(parsed)
            stats['verified_source_mutations_traces'] += bool(mutated)
            stats['verified_source_mutations_boundaries'] += len(mutated)
            stats['downstream_consumption_established'] += bool(consumed)
            stats['boundary_proven_adoption_established'] += bool(report['lifecycle']['adoption_event_ids'])
            stats['new_boundary_proven_adoption_traces'] += bool(added)
            stats['converted_unknown_to_positive'] += old[aid]['classification'] == 'unknown' and r['classification'] == 'positive'
            stats['converted_unknown_to_negative'] += old[aid]['classification'] == 'unknown' and r['classification'] == 'negative'
            stats['remaining_unknown'] += r['classification'] == 'unknown'
            stats['rejected_for_ambiguity'] += ambiguity
            kind = 'new_adoption' if added else 'ambiguity' if ambiguity else 'parsed_without_adoption' if mutated else 'boundary_unresolved'
            if sample_counts[name, kind] < 2:
                samples.append(detail | {'sample_cohort': name, 'sample_kind': kind, 'trace_path': r['trace_path'], 'result': report})
                sample_counts[name, kind] += 1
    # Changes to supported V2 classes require investigation, never silent acceptance.
    supported_changes = [c for c in changed if c['v2_classification'] != 'unknown' and c['v2_classification'] != c['v3_classification']]
    write_json(out / 'supported_classification_changes.json', supported_changes)
    write_jsonl(out / 'new_trc_adoption_event_evidence.jsonl', new_events)
    write_jsonl(out / 'changed_trace_evidence.jsonl', changed)
    write_jsonl(out / 'lifecycle_trace_changes.jsonl', lifecycle_changes)
    write_jsonl(out / 'representative_cases.jsonl', samples)
    write_jsonl(out / 'cohort_attempt_audit.jsonl', audit_rows)
    write_json(out / 'cohort_members.json', cohorts)
    write_json(out / 'cohort_outcomes.json', cohort_stats)
    write_csv(out / 'cohort_outcomes.csv', [{'cohort': k, **v} for k, v in sorted(cohort_stats.items())])
    write_csv(out / 'trc_evidence_quality.csv', [{'version': version, 'evidence_quality': k, 'traces': n}
              for version, data in [('v2', old), ('v3', new)]
              for k, n in sorted(Counter(quality(r) for r in data.values() if r['lep_code'] == TRC).items())])
    protected = json.loads(args.protected_hashes.read_text())
    allowed = {'benchmark/production_propagation.py', 'scripts/analyze_propagation.py'}
    violations, allowed_changes = [], []
    for path, digest in protected.items():
        pth = Path(path)
        actual = hashlib.sha256(pth.read_bytes()).hexdigest() if pth.is_file() else None
        if actual != digest:
            (allowed_changes if path in allowed else violations).append(path)
    inventories_equal = list(rows(args.before / 'input_inventory.jsonl')) == list(rows(args.after / 'input_inventory.jsonl'))
    manifest = json.loads((args.after / 'run_manifest.json').read_text())
    code_matches = all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest for path, digest in manifest['code_sha256'].items())
    write_json(out / 'protected_artifacts_verification.json', {'checked_files': len(protected),
        'violations': violations, 'allowed_integration_changes': allowed_changes,
        'source_inventories_identical': inventories_equal, 'run_manifest_code_hashes_match': code_matches})
    assert not violations and inventories_equal and code_matches, 'Protected input/artifact or run code changed'
    verification = {'detector_suite': VERSION, 'measured_denominator': len(new),
        'non_trc_metric_rows_identical': len(unaffected), 'id_incomparable_preserved': sum(r['lep_code'] == 'LEP_INPUT_DISREGARD' and r['classification'] == 'incomparable' for r in old.values()),
        'original_boundary_proven_traces_preserved': len(old_proven),
        'supported_trc_classification_changes': len(supported_changes),
        'new_adoption_assertion_proofs': new_proofs, 'events_with_new_adoption_proofs': len(new_events),
        'traces_with_new_adoption_proofs': len({e['attempt_id'] for e in new_events}),
        'new_positive_traces': sum(old[aid]['classification'] != 'positive' and r['classification'] == 'positive' for aid, r in new.items()),
        'source_inventory_identical': inventories_equal, 'protected_artifact_violations': violations,
        'independently_validated_propagation_probability': None}
    write_json(out / 'verification.json', verification)
    lifecycle = {version: counts([r for r in data.values() if r['lep_code'] == TRC]) for version, data in [('v2', old), ('v3', new)]}
    write_json(out / 'trc_lifecycle.json', lifecycle)
    lines = ['# TRC V3 offline comparison',
        'Same measured population: ' + str(len(new)) + '. Non-TRC rows are exactly identical by stable attempt ID: ' + str(len(unaffected)) + '.',
        'These are deterministic evidence classifications, not validated propagation probabilities. Unknown and incomparable are not negatives. Legacy-only positives do not establish corrupted-information adoption.',
        '## Classifications', table(groups, ['lep_code', 'v2_measured', 'v2_positive', 'v3_positive', 'v2_negative', 'v3_negative', 'v2_unknown', 'v3_unknown', 'v2_incomparable', 'v3_incomparable']),
        '## Evidence quality', table([{'version': version, **data} for version, data in lifecycle.items()],
          ['version', 'measured', 'trc_boundary_proven_adoption', 'trc_legacy_only_behavioral_positive_adoption_unresolved', 'negative', 'unknown', 'incomparable']),
        '## Cohort reconciliation',
        'Fable reported approximately 328 code unknowns and 119 financial unknowns involving 24 numeric-marker fixtures. The saved V2 rows must be re-derived: see the cohorts below. The broader financial unresolved-boundary cohort is not synonymous with numeric-marker labels. Numeric header cells (e.g. $12,000 in a first data row rendered as a header) do not establish a scale or unit; no multiplier is inferred.',
        table([{'cohort': k, **v} for k, v in sorted(cohort_stats.items())], ['cohort', 'total_examined', 'fixtures', 'successfully_parsed_boundaries_traces', 'verified_source_mutations_traces', 'downstream_consumption_established', 'boundary_proven_adoption_established', 'converted_unknown_to_positive', 'converted_unknown_to_negative', 'remaining_unknown', 'rejected_for_ambiguity']),
        'Cohorts overlap and must not be summed. The main table counts each trace once per endpoint. The CSV/JSON also retain boundary counts. Consumption means the recognized corrupted bytes occur in recorded receiver input; it does not itself establish adoption.',
        '## Recovery and final impact', table([{'version': k, **v} for k, v in lifecycle.items()],
            ['version', 'adoption_traces', 'adoption_events', 'forwarding_traces', 'cross_agent_traces', 'cross_agent_events', 'recovery_traces', 'recovered_adoption_events', 'transient_propagation:detected', 'final_answer_impact:detected', 'final_answer_impact:not_detected', 'final_answer_impact:unknown']),
        'Lifecycle endpoints overlap. Trace counts use Boolean membership and event counts use distinct IDs; recovery links are not added to trace counts. Final restoration concerns recognized assertions only, not complete answer correctness.',
        '## Verification', '```json\n' + json.dumps(verification, indent=2) + '\n```',
        '## Review files',
        '- `new_trc_adoption_event_evidence.jsonl`: every new assertion proof, full emitted text, boundary, path, exposure/transport, recovery and final impact.\n- `original_v2_boundary_proven_traces.jsonl`: the original adoption cohort and its event IDs.\n- `changed_trace_evidence.jsonl`: every classification or evidence-quality transition.\n- `cohort_attempt_audit.jsonl`, `cohort_members.json`: auditable cohort membership and per-attempt outcomes.\n- `representative_cases.jsonl`: supported effects and intentionally unresolved cases.\n- `comparison_by_*.csv`: classification, evidence quality and lifecycle by LEP, family, topology, mode and complete configuration.\n- `protected_artifacts_verification.json`: before/after artifact hashes and source inventory equality.',
        '## Methodological limitations',
        'The source recognizer only interprets parsable Python elements anchored in the attack specification and byte-verified diff. Qualified identifiers are necessary when names have multiple owners. A missing function, generic repair suggestion, or unchanged defect analysis is not adoption. Unparsable snippets, ambiguous scopes, and unrecorded source use remain unresolved. Function-body changes are supported through actual source artifacts, not inferred semantic paraphrases.',
        'Financial alias extraction is enabled only after exact numeric-header cell mapping at a verified boundary. It requires a unique entity label and explicit value/figure field or table cell; coincidental numbers and inferred calculations do not suffice. Numeric selectors are structural, not proof of units. Other label/qualifier grammars remain out of scope.',
        'Recorded ancestry is necessary but insufficient: independent sources, missing content transport and intervening clean rereads/corrections block new attribution. Clean rereads alone do not prove behavioral recovery. Negative means no supported effect with explicit final restoration within the recognized assertion set; it is not a causal null result. Research prose corruption, HC/IPI control instability and IPI attribution gaps are unchanged. No independent human labels or new executions were introduced.']
    (out / 'comparison.md').write_text('\n\n'.join(lines) + '\n')
    print(json.dumps(verification, indent=2))


if __name__ == '__main__':
    main()
