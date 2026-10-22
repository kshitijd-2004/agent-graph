"""Score separately supplied independent endpoint reviews; never invent labels.

Two reviewers must independently label every case/endpoint. Agreement is gold;
disagreements require a separately identified adjudicator. All source annotation
files are copied verbatim, with hashes, alongside the scored output.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

from benchmark.semantic_mp.evaluator import ENDPOINTS
from scripts.measure_semantic_propagation import atomic_json

STATES = {'positive', 'negative', 'unknown'}


def prepare_template(packet_path, reviewer_id, output):
    if output.exists():
        raise ValueError('refusing to overwrite reviewer annotations')
    packet = json.loads(packet_path.read_text())
    template = {'reviewer_id': reviewer_id, 'independent_of_development': False,
        'rubric_version': 'semantic-endpoint-review-v1',
        'rubric_sha256': packet['rubric_sha256'],
        'packet_sha256': hashlib.sha256(packet_path.read_bytes()).hexdigest(),
        'instructions': 'An independent human must attest independence and fill every label. '
                        'Null is unreviewed, not unknown. Keep original annotations immutable.',
        'annotations': [{'attempt_id': c['attempt_id'], 'endpoint': ep, 'state': None,
                         'rationale': '', 'evidence': []} for c in packet['cases'] for ep in ENDPOINTS]}
    atomic_json(output, template)
    return {'template': str(output), 'independent_metrics': 'unmeasured'}


def scores(gold, predictions):
    counts = {'tp': 0, 'fp': 0, 'fn': 0, 'tn': 0, 'unknown': 0, 'gold_unknown': 0,
              'assessed_on_resolved_gold': 0, 'resolved_gold': 0}
    for key, truth in gold.items():
        prediction = predictions.get(key, 'unknown')
        if prediction not in STATES:
            raise ValueError('invalid predicted state')
        counts['unknown'] += prediction == 'unknown'
        if truth == 'unknown':
            counts['gold_unknown'] += 1
            continue
        counts['resolved_gold'] += 1
        counts['assessed_on_resolved_gold'] += prediction != 'unknown'
        counts['tp'] += truth == 'positive' and prediction == 'positive'
        counts['fp'] += truth == 'negative' and prediction == 'positive'
        counts['fn'] += truth == 'positive' and prediction != 'positive'  # Abstentions lower recall.
        counts['tn'] += truth == 'negative' and prediction == 'negative'
    ratio = lambda n, d: n / d if d else None
    return dict(counts, precision=ratio(counts['tp'], counts['tp'] + counts['fp']),
                recall=ratio(counts['tp'], counts['tp'] + counts['fn']),
                false_positive_rate=ratio(counts['fp'], sum(v == 'negative' for v in gold.values())),
                abstention_rate=ratio(counts['unknown'], len(gold)),
                coverage=ratio(counts['assessed_on_resolved_gold'], counts['resolved_gold']))


def review_map(document, expected, packet_hash, rubric_hash):
    if (document.get('packet_sha256') != packet_hash or not document.get('reviewer_id')
            or document.get('independent_of_development') is not True
            or document.get('rubric_version') != 'semantic-endpoint-review-v1'
            or document.get('rubric_sha256') != rubric_hash):
        raise ValueError('missing independent reviewer provenance or packet/rubric mismatch')
    labels = {}
    for row in document['annotations']:
        key = (row['attempt_id'], row['endpoint'])
        if key in labels or key not in expected or row['state'] not in STATES:
            raise ValueError('duplicate, unknown case/endpoint, or invalid annotation')
        if not row.get('rationale') or not isinstance(row.get('evidence'), list):
            raise ValueError('annotation must preserve rationale and evidence references')
        labels[key] = row['state']
    return labels


def run(packet_path, prediction_path, reviews, adjudication, output):
    packet = json.loads(packet_path.read_text())
    packet_hash = hashlib.sha256(packet_path.read_bytes()).hexdigest()
    rubric_hash = hashlib.sha256(packet['rubric_text'].encode()).hexdigest()
    if packet['rubric_sha256'] != rubric_hash:
        raise ValueError('review packet rubric hash mismatch')
    expected = {(r['attempt_id'], ep) for r in packet['cases'] for ep in ENDPOINTS}
    if len(reviews) != 2:
        raise ValueError('exactly two independent original reviews required')
    docs = [json.loads(p.read_text()) for p in reviews]
    if docs[0]['reviewer_id'] == docs[1]['reviewer_id']:
        raise ValueError('reviewers must be distinct')
    maps = [review_map(d, expected, packet_hash, rubric_hash) for d in docs]
    if any(set(m) != expected for m in maps):
        raise ValueError('each original review must label every endpoint of every case')
    disagreements = {k for k in expected if maps[0][k] != maps[1][k]}
    resolved = {}
    if adjudication:
        doc = json.loads(adjudication.read_text())
        if doc['reviewer_id'] in {d['reviewer_id'] for d in docs}:
            raise ValueError('adjudicator must be a third reviewer')
        resolved = review_map(doc, expected, packet_hash, rubric_hash)
        if set(resolved) != disagreements:
            raise ValueError('adjudication must cover exactly the original disagreements')
    gold = {k: maps[0][k] for k in expected - disagreements}
    gold.update(resolved)
    predictions = {}
    for line in prediction_path.read_text().splitlines():
        row = json.loads(line)
        for ep, decision in row['measurement']['endpoints'].items():
            key = (row['attempt_id'], ep)
            if key in predictions or key not in expected:
                raise ValueError('duplicate or unexpected prediction')
            predictions[key] = decision['state']
    if set(predictions) != expected:
        raise ValueError('prediction population differs from review packet')
    output.mkdir(parents=True, exist_ok=False)
    inputs = [packet_path, prediction_path, *reviews] + ([adjudication] if adjudication else [])
    for index, path in enumerate(inputs):
        shutil.copyfile(path, output / f'input_{index}_{path.name}')
    report = {'review_provenance': 'reviewer-declared independence; original files preserved',
        'held_out_validation': not packet.get('development_selection', True),
        'development_selection': packet.get('development_selection', True),
        'extraction_accuracy': 'unmeasured: endpoint labels do not validate extraction',
        'inputs': [{'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in inputs],
        'disagreements': [{'attempt_id': k[0], 'endpoint': k[1],
                           'reviewer_1': maps[0][k], 'reviewer_2': maps[1][k],
                           'adjudication': resolved.get(k)} for k in sorted(disagreements)],
        'unadjudicated': len(disagreements - resolved.keys()),
        'metrics': {ep: scores({k: v for k, v in gold.items() if k[1] == ep}, predictions) for ep in ENDPOINTS}}
    atomic_json(output / 'review_scores.json', report)
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--packet', type=Path, required=True)
    p.add_argument('--predictions', type=Path)
    p.add_argument('--review', type=Path, action='append')
    p.add_argument('--template-reviewer')
    p.add_argument('--adjudication', type=Path)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if a.template_reviewer:
        result = prepare_template(a.packet, a.template_reviewer, a.output)
    else:
        if not a.predictions or not a.review:
            p.error('scoring requires --predictions and two --review files')
        result = run(a.packet, a.predictions, a.review, a.adjudication, a.output)
    print(json.dumps(result, indent=2))
