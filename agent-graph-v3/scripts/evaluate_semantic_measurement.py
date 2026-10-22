"""Offline authored alignment evaluation and blinded independent-review packet.

Does not call an extractor/model, scan traces, or create independent gold labels.
"""
import argparse
from collections import defaultdict
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from benchmark.semantic_mp_v2.extraction import Fact, alignment_key
from benchmark.semantic_mp_v2.measurement_contract import (
    ALIGNMENT_VERSION, VERSION, EntityRegistry, identity_relation,
)

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / 'tests/fixtures/semantic_measurement/alignment_development.json'
RUBRIC = ROOT / 'docs/semantic_measurement_contract.md'


def proposition(values):
    values = {'unit': '', 'scope': '', 'stance': 'asserted', 'kind': 'fact', **values}
    return Fact(**values, evidence=[], key=alignment_key(
        values['subject'], values['predicate'], values['unit'], values['scope'], values['kind']))


def metrics(rows):
    n = len(rows)
    same = [r for r in rows if r['expected_identity'] == 'same']
    different = [r for r in rows if r['expected_identity'] == 'different']
    scored = [r for r in rows if r['expected_identity'] != 'unresolved']
    assessed = [r for r in scored if r['prediction'] != 'unresolved']
    return {
        'pairs': n, 'gold_same': len(same), 'gold_different': len(different),
        'gold_unresolved': n - len(scored),
        'false_merges': sum(r['prediction'] == 'same' for r in different),
        'confident_false_splits': sum(r['prediction'] == 'different' for r in same),
        'missed_matches_including_abstention': sum(r['prediction'] != 'same' for r in same),
        'same_identity_recall': sum(r['prediction'] == 'same' for r in same) / len(same) if same else None,
        'abstentions': sum(r['prediction'] == 'unresolved' for r in rows),
        'resolved_gold_coverage': len(assessed) / len(scored) if scored else None,
        'accuracy_on_assessed_resolved_gold': sum(r['prediction'] == r['expected_identity'] for r in assessed) / len(assessed) if assessed else None,
        'unsupported_decisions_on_unresolved_gold': sum(r['prediction'] != 'unresolved' for r in rows if r['expected_identity'] == 'unresolved'),
    }


def evaluate(corpus):
    rows, packet, registries = [], [], {}
    ids = [c['id'] for c in corpus['cases']]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate case identity')
    for case in corpus['cases']:
        if case['expected_identity'] not in ('same', 'different', 'unresolved'):
            raise ValueError('invalid authored label')
        registry = EntityRegistry.build(case['task_source'])
        registries[case['id']] = asdict(registry)
        prediction = identity_relation(proposition(case['left']), proposition(case['right']), registry)
        rows.append({k: case[k] for k in ('id', 'task_family', 'stratum', 'expected_identity')} | {'prediction': prediction})
        # No proposed propositions, expected labels, predictions, or failure strata.
        packet.append({k: case[k] for k in ('id', 'task_family', 'task_source', 'left_text', 'right_text')} | {
            'reviewer_id': None, 'reviewed_at': None, 'identity': None, 'reason': None,
            'left_extraction': None, 'right_extraction': None,
            'extraction_coverage': None, 'source_attribution': None, 'supporting_quotes': [],
            'rubric_version': corpus['rubric_version'],
        })
    strata = defaultdict(list)
    for row in rows:
        strata[row['task_family'] + '|' + row['stratum']].append(row)
    summary = {'version': VERSION, 'alignment_version': ALIGNMENT_VERSION,
               'label_origin': corpus['label_origin'], 'independent_validation': False,
               'rubric_version': corpus['rubric_version'], 'overall': metrics(rows),
               'by_family_and_stratum': {key: metrics(value) for key, value in sorted(strata.items())},
               'extraction_accuracy': 'not measured: alignment input propositions are authored',
               'independent_key_stability': 'not measured'}
    same = [row for row in rows if row['expected_identity'] == 'same']
    summary['authored_key_stability'] = {
        'same_identity_pairs': len(same),
        'same_key_pairs': sum(row['prediction'] == 'same' for row in same),
        'fraction': sum(row['prediction'] == 'same' for row in same) / len(same) if same else None,
        'independent': False,
    }
    return rows, summary, packet, registries


def run(output):
    corpus = json.loads(CORPUS.read_text())
    rows, summary, packet, registries = evaluate(corpus)
    manifest = {'corpus_sha256': hashlib.sha256(CORPUS.read_bytes()).hexdigest(),
                'rubric_sha256': hashlib.sha256(RUBRIC.read_bytes()).hexdigest(),
                'rubric_version': corpus['rubric_version'],
                'code_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in (Path(__file__), ROOT / 'benchmark/semantic_mp_v2/measurement_contract.py',
                              ROOT / 'benchmark/semantic_mp_v2/extraction.py')}}
    output.mkdir(parents=True, exist_ok=False)
    for name, data in [('predictions.json', rows), ('summary.json', summary),
                       ('review_packet.json', packet), ('entity_registries.json', registries),
                       ('manifest.json', manifest)]:
        (output / name).write_text(json.dumps(data, indent=2) + '\n')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    print(json.dumps(run(parser.parse_args().output), indent=2))
