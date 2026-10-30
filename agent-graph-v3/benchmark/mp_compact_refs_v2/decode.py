"""Total row diagnostics; invalid references are never repaired."""
from collections import Counter
from copy import deepcopy
import jsonschema
from benchmark.mp_adjudicator_compact.engine import A_SCHEMA
from benchmark.mp_adjudicator_compact.codec import block


def decode(packet, codec, response):
    rows = response.get('events', []) if isinstance(response, dict) else []
    if not isinstance(rows, list):
        rows = []
    counts = Counter(r.get('event_id') for r in rows if isinstance(r, dict) and isinstance(r.get('event_id'), str))
    diagnostics, accepted = [], {}
    validator = jsonschema.Draft202012Validator(A_SCHEMA['properties']['events']['items'])
    for index, row in enumerate(rows):
        errors, mapped = [], []
        def error(code, **details):
            errors.append(dict(code=code, **details))
        for err in validator.iter_errors(row):
            error('invalid_row_contract', path=list(err.path), message=err.message)
        eid = row.get('event_id') if isinstance(row, dict) else None
        known = isinstance(eid, str) and eid in packet['cards']
        if not known:
            error('unknown_event', event_id=eid)
        if isinstance(eid, str) and counts[eid] > 1:
            error('duplicate_event', event_id=eid, occurrences=counts[eid])
        refs = row.get('evidence', []) if isinstance(row, dict) else []
        if not isinstance(refs, list):
            refs = []
        for position, ref in enumerate(refs):
            details = dict(reference=ref, evidence_index=position)
            if not isinstance(ref, str):
                error('invalid_reference_type', **details)
                continue
            if ref in codec['view']['texts'] or ref.startswith('t'):
                error('wrong_namespace', namespace='shared_text', **details)
                continue
            if not ref.startswith('b'):
                error('wrong_namespace', namespace='not_occurrence', **details)
                continue
            if ref not in codec['lookup']:
                error('unknown_occurrence', **details)
                continue
            try:
                bid = block(codec, packet, ref)
                actual = packet['blocks'][bid]['event_id']
                if not known:
                    error('invalid_evidence_relationship', reason='unknown_row_event', actual_event=actual, **details)
                elif actual != eid:
                    error('cross_event_evidence', actual_event=actual, **details)
                mapped.append(bid)
            except (ValueError, KeyError) as exc:
                error('invalid_evidence_relationship', reason=str(exc), **details)
        if isinstance(row, dict) and row.get('review') == 'reviewed':
            if not refs:
                error('invalid_evidence_relationship', reason='reviewed_without_evidence')
            if not isinstance(row.get('summary'), str) or not row['summary'].strip():
                error('invalid_evidence_relationship', reason='empty_review_summary')
        diagnostics.append(dict(row_index=index, event_id=eid, errors=errors,
                                status='quarantined' if errors else 'accepted', original_row=deepcopy(row)))
        if not errors:
            accepted[eid] = {k: deepcopy(v) for k, v in row.items() if k != 'event_id'}
            accepted[eid]['evidence'] = mapped
    unresolved = {'review':'unresolved','kind':'other','summary':'No valid event review supplied.','evidence':[]}
    events = {eid: deepcopy(accepted.get(eid, unresolved)) for eid in packet['cards']}
    top_errors = [dict(path=list(e.path), message=e.message) for e in jsonschema.Draft202012Validator(A_SCHEMA).iter_errors(response)]
    return dict(original_response=deepcopy(response), rows=diagnostics, structural_errors=top_errors,
                error_counts=dict(Counter(e['code'] for r in diagnostics for e in r['errors'])),
                accepted_event_ids=list(accepted), quarantined_rows=[r['row_index'] for r in diagnostics if r['errors']],
                decoding_status='partial_or_invalid' if top_errors or any(r['errors'] for r in diagnostics) else 'complete',
                decoded={'events':events})
