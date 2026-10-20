"""Attempt and plan denominators, independent of propagation outcomes."""
from collections import Counter, defaultdict
import math

REASONS = ('infrastructure_failure', 'injection_target_unreachable', 'ineffective_mutation',
           'partial_injection', 'other_admission_failure', 'unverifiable_legacy')


def cell_key(meta):
    codes = meta.get('lep_codes') or ([meta['lep_code']] if meta.get('lep_code') else [])
    return '|'.join((meta.get('fixture_id', ''), meta.get('topology', ''),
                     '+'.join(sorted(codes)), meta.get('propagation_mode', 'single_origin')))


def is_perturbed(meta):
    return meta.get('condition') not in ('benign', 'counterfactual') and bool(
        meta.get('lep_codes') or meta.get('lep_code') or
        meta.get('condition') in ('single_lep', 'convergence', 'containment'))


def ratio(n, d):
    return n / d if d else None


def recorded_attempt(record):
    """Runtime ledger summary only; analysis must validate the trace itself."""
    valid = record.get('dataset_eligible') is True
    reason = 'admitted' if valid else record.get('admission_reason') or (
        'infrastructure_failure' if record.get('termination_reason', '').startswith('backend_')
        else 'other_admission_failure')
    return {**record, 'status': 'admitted' if valid else (
        'unverifiable' if reason == 'unverifiable_legacy' else 'rejected'),
        'reason': reason, 'analysis_status': 'not_analyzed', 'propagation_occurred': None}


def counts(rows, slots=None):
    reasons = Counter(r['reason'] for r in rows)
    admitted = [r for r in rows if r['status'] == 'admitted']
    measurable = [r for r in admitted if r.get('propagation_occurred') is not None]
    propagated = sum(r['propagation_occurred'] for r in measurable)
    fulfilled = {r['scenario_id'] for r in admitted}
    planned_ids = {s['scenario_id'] for s in slots} if slots is not None else None
    quota = (sum(s['valid_trace_quota'] for s in slots)
             if slots and all('valid_trace_quota' in s for s in slots) else None)
    low = high = None
    if measurable:
        n, p, z = len(measurable), propagated / len(measurable), 1.96
        denominator = 1 + z*z/n
        center = (p + z*z/(2*n)) / denominator
        width = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / denominator
        low, high = max(0, center-width), min(1, center+width)
    return {
        'planned_perturbed_slots': len(slots) if slots is not None else None,
        'started_perturbed_attempts': len(rows),
        'valid_admitted_perturbed_traces': len(admitted),
        'rejected_attempts': sum(r['status'] == 'rejected' for r in rows),
        'unverifiable_traces': sum(r['status'] == 'unverifiable' for r in rows),
        'rejected_by_reason': dict(reasons - Counter({'admitted': reasons['admitted']})),
        'infrastructure_failures': reasons['infrastructure_failure'],
        'injection_target_unreachable_attempts': reasons['injection_target_unreachable'],
        'ineffective_mutation_attempts': reasons['ineffective_mutation'],
        'partial_injections': reasons['partial_injection'],
        'other_admission_failures': reasons['other_admission_failure'],
        'analysis_ready_traces': sum(r.get('analysis_status') in ('measured', 'analysis_failed') for r in admitted),
        'analysis_failures': sum(r.get('analysis_status') == 'analysis_failed' for r in admitted),
        'unknown_outcomes': len(admitted) - len(measurable),
        'measurable_propagation_outcomes': len(measurable),
        'valid_traces_with_downstream_propagation': propagated,
        'valid_traces_without_downstream_propagation': len(measurable) - propagated,
        'injection_yield': ratio(len(admitted), len(rows)),
        'propagation_probability': ratio(propagated, len(measurable)),
        'propagation_ci_low': low,
        'propagation_ci_high': high,
        'analysis_coverage': ratio(len(measurable), len(admitted)),
        'fulfilled_slots': len(fulfilled & planned_ids) if planned_ids is not None else None,
        'slot_fulfillment': ratio(len(fulfilled & planned_ids), len(slots)) if slots is not None else None,
        'valid_trace_quota': quota,
        'quota_met': (all(sum(r['scenario_id'] == s['scenario_id'] for r in admitted)
                          >= s['valid_trace_quota'] for s in slots) if quota is not None else None),
    }


def summarize_attempts(rows, plan=None):
    protocols = {r.get('memory_protocol') or 'write_interception_v1' for r in rows}
    protocols.update(s.get('memory_protocol') or 'write_interception_v1' for s in plan or [])
    if len(protocols) > 1:
        raise ValueError('Separate protocol accounting required; pooling is forbidden')
    rows = [r for r in rows if is_perturbed(r)]
    slots = [s for s in plan if is_perturbed(s)] if plan is not None else None
    grouped, planned = defaultdict(list), defaultdict(list)
    for row in rows:
        grouped[cell_key(row)].append(row)
    for slot in slots or []:
        planned[cell_key(slot)].append(slot)
    cells = {}
    for key in sorted(set(grouped) | set(planned)):
        items, entries = grouped.get(key, []), planned.get(key, [])
        meta = (entries or items)[0]
        cells[key] = dict(fixture_id=meta.get('fixture_id'), task_family=meta.get('task_family'),
            topology=meta.get('topology'), lep_code=key.split('|')[2],
            propagation_mode=meta.get('propagation_mode'),
            **counts(items, entries if slots is not None else None))
    overall = counts(rows, slots)
    planned_cells = [cells[k] for k in planned]
    quota_cells = [c for c in planned_cells if c['quota_met'] is not None]
    overall.update(plan_available=plan is not None,
        planned_cells=len(planned) if plan is not None else None,
        observed_cells=len(grouped),
        any_valid_cell_coverage=ratio(sum(c['valid_admitted_perturbed_traces'] > 0 for c in planned_cells), len(planned_cells)),
        quota_coverage=ratio(sum(c['quota_met'] for c in quota_cells), len(quota_cells)),
        zero_valid_cells=[k for k, c in cells.items() if not c['valid_admitted_perturbed_traces']])
    return {'overall': overall, 'by_cell': cells}
