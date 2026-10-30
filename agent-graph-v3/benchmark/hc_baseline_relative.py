"""HC-only baseline-relative candidate detector; no causal/correctness verdict.

Three original controls, semantic assertion agreement, recorded receiver delivery,
DAG reachability, and exclusion of pre-existing/independent deviations are required.
V1/V2 and all other perturbation detectors are deliberately left unchanged.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, replace
import json
import math
import re
from decimal import Decimal

from benchmark import evidence_propagation as v2
from schemas.trace import TraceEventType as ET

VERSION = 'hc-baseline-relative-v1.0'
EXTRACTION_VERSION = 'hc-assertions-v1.0'
HC = 'LEP_HANDOFF_CORRUPTION'
GATES = ('verified_intervention', 'receiver_delivery', 'downstream_active',
         'stable_three_run_comparison', 'substantive_task_deviation',
         'no_preexisting_or_independent_deviation', 'candidate')


def extract_assertions(text, spec):
    """V2 grammar with local precision guards; never alter shared extraction."""
    result = v2.extract_assertions(text, spec)
    for key, a in list(result.items()):
        if key.startswith('claim_'):
            # Also recognize this explicit NEI paraphrase in pre-intervention
            # checks; missing it can falsely make an old decision look new.
            number = key.split('_')[1]
            pattern = (r'\bClaim\s*' + number + r'\s+has\s+'
                       r'(?:not enough info(?:rmation)?|insufficient (?:evidence|information))\b')
            hits = [m.group() for line in v2.eligible_lines(text)
                    for m in re.finditer(pattern, line, re.I)]
            if hits:
                if a.status == 'asserted' and a.value != 'NOT ENOUGH INFORMATION':
                    result[key] = replace(a, status='ambiguous', value=None)
                elif a.status != 'ambiguous':
                    result[key] = v2.Assertion(key, 'NOT ENOUGH INFORMATION', 'asserted', hits[0])
        elif key == 'repair_target' and a.status == 'asserted':
            # A plural phrase such as "identified methods" is not a symbol.
            if (a.value.lower() in {'identified', 'affected', 'relevant', 'appropriate', 'existing',
                                    'corresponding', 'following', 'original', 'other', 'same', 'new', 'current'}
                    or not re.search(r'\b' + re.escape(a.value) + r'(?:\(\))?\s+(?:function|method)\b', a.excerpt)):
                result[key] = replace(a, status='unknown', value=None)
        elif key == 'bug_status' and a.value == 'REPAIRED':
            # "The analysis has been updated" is not a claim of code repair.
            if not re.search(r'\b(?:I have (?:fixed|updated)|implemented the fix|'
                             r'(?:code|function|method|implementation|patch) (?:has been|was) (?:fixed|updated)|'
                             r'issue (?:has been|is) resolved by)\b', a.excerpt, re.I):
                result[key] = replace(a, status='unknown', value=None)
        elif key not in ('answer', 'answer_calculation') and a.status == 'asserted' and type(a.value) in (int, float):
            # E.g. "percentage ... total units is 0.27%" must not assert
            # total_units=0.27. Units are not guessed from the answer key.
            if 'percent' not in spec.get('required_facts', {}).get(key, {}).get('unit', '').lower():
                percentages = re.findall(r'(' + v2.NUM + r')\s*%', a.excerpt)
                if any(float(x.replace(',', '')) == a.value for x in percentages):
                    result[key] = replace(a, status='unknown', value=None)
    return result


def _witness(trace, event, assertion):
    return {'trace_id': trace.trace_id, 'repetition_index': trace.metadata.get('repetition_index'),
            'event_id': event.event_id, 'event_index': event.event_index,
            'slot': v2.event_slot(event), 'assertion': asdict(assertion),
            'emitted_text': v2.emitted_text(event)}


def build_reference(clean_traces, spec):
    """Retain every witness, including conflicts; exactly three distinct runs."""
    if len(clean_traces) != 3 or len({t.metadata.get('repetition_index') for t in clean_traces}) != 3:
        raise ValueError('Exactly three distinct original benign repetitions required')
    if any(t.metadata.get('repetition_index') is None for t in clean_traces):
        raise ValueError('Missing benign repetition identity')
    for field in ('fixture_id', 'task_family', 'topology', 'execution_variant'):
        if len({t.metadata.get(field) for t in clean_traces}) > 1:
            raise ValueError('Non-corresponding benign references: ' + field)
    runs = []
    for trace in clean_traces:
        exact, role = defaultdict(lambda: defaultdict(list)), defaultdict(lambda: defaultdict(list))
        for e in trace.events:
            if e.event_type not in v2.legacy.ACTIVE_EVENT_TYPES:
                continue
            for k, a in extract_assertions(v2.emitted_text(e), spec).items():
                w = _witness(trace, e, a)
                exact[v2.event_slot(e)][k].append(w)
                if a.status != 'absent':
                    role[(e.agent_role, v2.event_slot(e)[-1])][k].append(w)
        runs.append((exact, role))
    profiles = []
    for mode in (0, 1):
        profile = {}
        slots = set().union(*(set(r[mode]) for r in runs))
        for slot in slots:
            keys = set().union(*(set(r[mode].get(slot, {})) for r in runs))
            stable = {}
            for key in keys:
                groups = [r[mode].get(slot, {}).get(key, []) for r in runs]
                witnesses = [w for group in groups for w in group]
                if not all(groups) or any(w['assertion']['status'] != 'asserted' for w in witnesses):
                    continue
                value = witnesses[0]['assertion']['value']
                # Exact semantic consensus, as in V2. Tolerance only determines
                # whether a HC numeric change is substantive, not unanimity.
                if all(w['assertion']['value'] == value for w in witnesses):
                    stable[key] = {'value': value, 'witnesses_by_run': groups,
                                   'basis': 'exact_slot' if mode == 0 else 'role_phase_assertion_consensus'}
            profile[slot] = stable
        profiles.append(profile)
    return {'exact': profiles[0], 'role': profiles[1],
            'metadata': {k: clean_traces[0].metadata.get(k) for k in
                         ('fixture_id', 'task_family', 'topology', 'execution_variant')}}


def expectations(event, actual, reference):
    expected = dict(reference['exact'].get(v2.event_slot(event), {}))
    fallback = reference['role'].get((event.agent_role, v2.event_slot(event)[-1]), {})
    for k, a in actual.items():
        if k not in expected and a.status == 'asserted' and k in fallback:
            expected[k] = fallback[k]
    return expected


def relevant_key(key, spec):
    if key == 'answer_calculation':
        return False  # expression syntax/equivalent arithmetic is not an effect
    if spec.get('task_family') == 'code_review':
        return key in ('bug_status', 'repair_target') and bool(spec.get('required_issues'))
    return key in spec.get('required_facts', {})


def substantive(actual, expected, key, spec):
    if actual.status != 'asserted':
        return None, 'unresolved_assertion_not_proven_omission'
    if v2.same(actual.value, expected, key, spec):
        return False, 'same_behavior_or_numeric_tolerance'
    if type(actual.value) in (int, float) and type(expected) in (int, float) and actual.value and expected and key != 'answer':
        ratio = abs(actual.value / expected)
        if any(math.isclose(ratio, scale, rel_tol=1e-9) for scale in (1e-9, 1e-6, .001, 1000, 1e6, 1e9)):
            return None, 'unit_scale_comparability_unresolved'
    return True, 'asserted_task_behavior_changed'


def percentage_rounding_overlap(actual, expectation):
    """Use the recorded decimal precision, not a fitted numerical threshold."""
    def interval(a):
        tokens = re.findall(r'(' + v2.NUM + r')\s*%', a['excerpt'])
        matches = [Decimal(t.replace(',', '')) for t in tokens if float(t.replace(',', '')) == a['value']]
        if not matches:
            return None
        # Use the most precise explicit rendering of the asserted percentage.
        number = min(matches, key=lambda n: n.as_tuple().exponent)
        half = Decimal(10) ** number.as_tuple().exponent / 2
        return number - half, number + half
    observed = interval(asdict(actual))
    if observed is None:
        return False
    for group in expectation['witnesses_by_run']:
        for witness in group:
            benign = interval(witness['assertion'])
            if benign is None or max(observed[0], benign[0]) >= min(observed[1], benign[1]):
                return False
    return True


def repair_scope_unresolved(actual, expectation, text):
    """Nested function naming or the same recorded edit is not a new repair."""
    expected = expectation['value']
    if re.search(r'\b' + re.escape(expected) + r'\b', actual.excerpt):
        return True
    def assignments(body):
        return {re.sub(r'\s+', '', x) for x in re.findall(r'`([^`\n]+)`', body)
                if re.fullmatch(r'\s*[\w.]+\s*=\s*[^=]+', x)}
    common = assignments(text)
    for group in expectation['witnesses_by_run']:
        for witness in group:
            common &= assignments(witness['emitted_text'])
    # A shared before/after assignment pair makes the repair action overlap,
    # even if one report names a public method and another its implementation.
    return len(common) >= 2


def analyze_hc(trace, clean_traces, spec, *, reference=None, graph=None):
    codes = trace.metadata.get('lep_codes', [HC])
    if codes != [HC]:
        raise ValueError('This detector accepts HC only')
    reference = reference if reference is not None else build_reference(clean_traces, spec)
    for field, value in reference['metadata'].items():
        if value != trace.metadata.get(field):
            raise ValueError('HC and benign references differ: ' + field)
    edges, ancestors, repairs = v2.reconstruct_graph(trace, graph)
    byindex = {e.event_index: e for e in trace.events}
    byid = {e.event_id: e for e in trace.events}
    assertions = {i: extract_assertions(v2.emitted_text(e), spec) for i, e in byindex.items()}
    origins = [e for e in trace.events if e.event_labels.is_injection_origin]
    boundaries = []
    for o in origins:
        pair = v2.mutation_pair(o, trace)
        b = {'origin_id': o.event_id, 'origin_index': o.event_index,
             'sender_role': o.agent_role, 'status': 'unverified', 'deliveries': []}
        if o.event_type == ET.AGENT_HANDOFF and pair and pair[0] != pair[1]:
            before, after = pair
            b.update(status='verified_change', before=before, after=after)
            for d in trace.events:
                if (d.event_type != ET.TOPOLOGY_TRANSITION or o.event_id not in d.depends_on
                        or o.event_index not in ancestors[d.event_index] or d.agent_role == o.agent_role):
                    continue
                summary = v2.emitted_text(d)
                # Empty omissions need an exact recorded empty receiving payload.
                if not ((after and after in summary) or (not after and not summary)):
                    continue
                exposures = [e.event_id for e in trace.events if e.event_type == ET.REASONING
                             and e.agent_role == d.agent_role and d.event_index in ancestors[e.event_index]
                             and (not after or after in (e.input_text or '') or json.dumps(after)[1:-1] in (e.input_text or ''))]
                if exposures:
                    b['deliveries'].append({'event_id': d.event_id, 'receiver_role': d.agent_role,
                                            'exposure_ids': exposures, 'received_summary': summary})
        boundaries.append(b)
    rows, positives = [], []
    for e in sorted(trace.events, key=lambda e: e.event_index):
        if e.event_type not in v2.legacy.ACTIVE_EVENT_TYPES or e.event_labels.is_injection_origin:
            continue
        upstream = [b for b in boundaries if b['origin_index'] in ancestors[e.event_index]]
        if not upstream:
            continue
        actual = assertions[e.event_index]
        expected = expectations(e, actual, reference)
        row = {'event_id': e.event_id, 'event_index': e.event_index, 'agent_role': e.agent_role,
               'event_type': e.event_type.value, 'status': 'negative', 'reasons': [], 'evidence': [],
               'assertions': {k: asdict(a) for k, a in actual.items()},
               'clean_expectations': {k: ex['value'] for k, ex in expected.items()},
               'gates': {gate: False for gate in GATES}}
        delivered = []
        for b in upstream:
            if b['status'] != 'verified_change':
                continue
            row['gates']['verified_intervention'] = True
            for d in b['deliveries']:
                exposures = [x for x in d['exposure_ids'] if byid[x].event_index in ancestors[e.event_index] | {e.event_index}]
                if byid[d['event_id']].event_index in ancestors[e.event_index] and exposures:
                    delivered.append((b, d, exposures))
        row['gates']['receiver_delivery'] = bool(delivered)
        row['gates']['downstream_active'] = bool(delivered)
        if not delivered:
            row['status'] = 'unknown'
            row['reasons'].append('no_verified_receiver_path')
        if not expected and (e.event_type == ET.FINAL_RESPONSE or any(a.status != 'absent' for a in actual.values())):
            row['status'] = 'unknown'
            row['reasons'].append('no_stable_comparable_behavior')
        if any(relevant_key(k, spec) and a.status != 'absent' and k not in expected for k, a in actual.items()):
            row['status'] = 'unknown'
            row['reasons'].append('observed_task_assertion_without_stable_comparison')
        for key, ex in expected.items():
            if not relevant_key(key, spec):
                continue
            a = actual[key]
            row['gates']['stable_three_run_comparison'] |= bool(delivered)
            changed, reason = substantive(a, ex['value'], key, spec)
            if changed is True and key == 'answer' and percentage_rounding_overlap(a, ex):
                changed, reason = False, 'equivalent_percentage_rounding'
            if changed is True and key == 'repair_target' and repair_scope_unresolved(a, ex, v2.emitted_text(e)):
                changed, reason = None, 'repair_scope_comparability_unresolved'
            if changed is False:
                continue
            row['status'] = 'unknown'
            row['reasons'].append(key + ':' + reason)
            if changed is None:
                continue
            row['gates']['substantive_task_deviation'] |= bool(delivered)
            for b, d, exposures in delivered:
                oindex = b['origin_index']
                # Scan all pre-intervention authored task behavior, including
                # parallel branches. Also exclude an independently changed
                # decision in this event's ancestry after the intervention.
                prior = [i for i, pe in byindex.items() if pe.event_type in v2.legacy.ACTIVE_EVENT_TYPES
                         and not pe.event_labels.is_injection_origin
                         and (i < oindex or (i in ancestors[e.event_index] and i != oindex and oindex not in ancestors[i]))]
                conflicts = []
                for i in prior:
                    pa = assertions[i].get(key)
                    if pa and (pa.status == 'ambiguous' or (pa.status == 'asserted' and not v2.same(pa.value, ex['value'], key, spec))):
                        conflicts.append({'event_id': byindex[i].event_id, 'assertion': asdict(pa)})
                original = extract_assertions(b['before'], spec).get(key)
                if original and (original.status == 'ambiguous' or (original.status == 'asserted' and not v2.same(original.value, ex['value'], key, spec))):
                    conflicts.append({'event_id': b['origin_id'], 'surface': 'before_mutation', 'assertion': asdict(original)})
                if conflicts:
                    row['reasons'].append(key + ':preexisting_or_independent_deviation')
                    row.setdefault('excluded_evidence', []).append({'origin_id': b['origin_id'], 'key': key, 'conflicts': conflicts})
                    continue
                row['gates']['no_preexisting_or_independent_deviation'] = True
                # Explicitly route the proof through delivery and receiver use.
                exposure = min(exposures, key=lambda x: byid[x].event_index)
                points = [oindex, byid[d['event_id']].event_index, byid[exposure].event_index, e.event_index]
                path = [points[0]]
                for start, end in zip(points, points[1:]):
                    segment = v2.evidence_path(edges, start, end)
                    if not segment:
                        raise AssertionError('Delivery-path invariant violated')
                    path.extend(segment[1:])
                evidence = {'rule': 'stable_benign_relative_behavior', 'key': key,
                            'task_relevance': spec.get('required_facts', {}).get(key, {}).get('label') or
                                              spec.get('required_facts', {}).get(key, {}).get('claim') or
                                              {'required_issues': spec.get('required_issues', [])},
                            'stable_benign_expectation': ex, 'observed_hc_deviation': asdict(a),
                            'observed_emitted_text': v2.emitted_text(e),
                            'intervention_boundary': b, 'delivery_event_id': d['event_id'],
                            'receiver_exposure_event_id': exposure,
                            'path': [byindex[i].event_id for i in path],
                            'path_edges': [{'from': byindex[x].event_id, 'to': byindex[y].event_id,
                                            'basis': 'verified_artifact' if [x, y] in [r['edge'] for r in repairs] else 'recorded_graph'}
                                           for x, y in zip(path, path[1:])],
                            'preintervention_and_independent_events_checked': [byindex[i].event_id for i in prior],
                            'correctness': 'not_assessed', 'causal_attribution': 'not_established'}
                row['evidence'].append(evidence)
                positives.append((e, key, ex['value'], b['origin_id']))
        if row['evidence']:
            row['status'] = 'positive'
            row['gates']['candidate'] = True
        row['reasons'] = sorted(set(row['reasons']))
        rows.append(row)
    recoveries = []
    for seed, key, expected, origin_id in positives:
        for e in trace.events:
            if e.event_type not in v2.legacy.ACTIVE_EVENT_TYPES or seed.event_index not in ancestors[e.event_index]:
                continue
            a = assertions[e.event_index].get(key)
            ex = expectations(e, assertions[e.event_index], reference).get(key)
            if a and a.status == 'asserted' and ex and v2.same(ex['value'], expected, key, spec) and v2.same(a.value, expected, key, spec):
                recoveries.append({'seed_event_id': seed.event_id, 'event_id': e.event_id,
                                   'origin_id': origin_id, 'key': key, 'value': a.value,
                                   'interpretation': 'baseline_assertion_restored; not global recovery'})
    final = [r for r in rows if r['event_type'] == ET.FINAL_RESPONSE.value]
    detected = [r for r in rows if r['status'] == 'positive']
    status = 'positive' if detected else 'unknown' if not rows or any(r['status'] == 'unknown' for r in rows) else 'negative'
    return {'detector_version': VERSION, 'extraction_version': EXTRACTION_VERSION,
            'status': status, 'propagation_candidate': bool(detected),
            'negative_definition': 'no_detected_effect_in_supported_behavior; not a causal negative',
            'events': rows, 'boundaries': boundaries, 'graph_repairs': repairs,
            'anomalous_event_ids': [r['event_id'] for r in detected], 'recovery_events': recoveries,
            'final_answer_impact': 'detected' if any(r['status'] == 'positive' for r in final) else
                                   'unknown' if not final or any(r['status'] == 'unknown' for r in final) else 'not_detected',
            'correctness': 'not_assessed', 'causal_attribution': 'not_established'}
