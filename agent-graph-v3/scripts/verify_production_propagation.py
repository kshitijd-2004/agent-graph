"""Optional regression comparison; never required by production analysis.

Compare freshly ingested output with the historical 9,880 measured traces,
HC baseline-relative v1, and IPI evidence v2. No detector reruns or model calls.
"""
from __future__ import annotations
import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path

from benchmark.production_propagation import sha, write_json, DETECTORS


def rows(path):
    with Path(path).open() as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def normalize_legacy_reason_order(row):
    """Legacy set iteration makes reason order process-dependent.

    Sort only this diagnostic list, retaining duplicates and all actual text.
    Event order, classifications, metrics, and every other field stay exact.
    This comparison normalization never changes analyzer or detector outputs.
    """
    row = deepcopy(row)
    for detail in row.get('anomaly_details', {}).values():
        if 'reasons' in detail:
            detail['reasons'] = sorted(detail['reasons'])
    return row


def verify(integrated, baseline, hc_root, ipi_root, output):
    integrated = Path(integrated)
    new = {r['attempt_id']: r for r in rows(integrated / 'propagation_metrics.jsonl')}
    old = {r['attempt_id']: r for r in rows(baseline)}
    if len(new) != 9880 or set(new) != set(old):
        raise AssertionError(f'Measured population differs: new={len(new)}, old={len(old)}, '
                             f'missing={sorted(set(old)-set(new))[:20]}, added={sorted(set(new)-set(old))[:20]}')
    hc_frozen = {r['attempt_id']: r for r in rows(Path(hc_root) / 'all_2208/event_evidence.jsonl')}
    ipi_frozen = {r['attempt_id']: r for r in rows(Path(ipi_root) / 'event_evidence.jsonl')
                  if r['attempt_id'] in old and old[r['attempt_id']]['lep_code'] == 'LEP_INDIRECT_PROMPT_INJECTION'}
    ipi_comparison = {r['attempt_id']: r for r in rows(Path(ipi_root) / 'trace_comparison.jsonl')}
    if len(hc_frozen) != 2208 or len(ipi_frozen) != 1839:
        raise AssertionError('Unexpected frozen candidate populations')
    mismatches, matches, reason_order_only = [], Counter(), []
    for r in rows(integrated / 'legacy_metrics.jsonl'):
        if r != old[r['attempt_id']]:
            if normalize_legacy_reason_order(r) == normalize_legacy_reason_order(old[r['attempt_id']]):
                reason_order_only.append(r['attempt_id'])
                matches['legacy_complete_metric_rows_modulo_reason_order'] += 1
            else:
                mismatches.append({'attempt_id': r['attempt_id'], 'type': 'legacy_metric_row',
                                   'fields': [k for k in r.keys() | old[r['attempt_id']].keys() if r.get(k) != old[r['attempt_id']].get(k)]})
        else:
            matches['legacy_complete_metric_rows'] += 1
            matches['legacy_complete_metric_rows_modulo_reason_order'] += 1
    seen = set()
    for r in rows(integrated / 'detector_evidence.jsonl'):
        aid = r['attempt_id']; seen.add(aid)
        if aid not in hc_frozen and aid not in ipi_frozen:
            continue
        expected = hc_frozen.get(aid, ipi_frozen.get(aid))
        actual = r['result']
        # Compare every native detector field, not only aggregate counts.
        # Additional frozen wrapper fields (source path, cohort flag) are not
        # detector outputs and are compared separately where relevant.
        differences = [k for k, v in actual.items() if expected.get(k) != v]
        if differences:
            mismatches.append({'attempt_id': aid, 'type': 'native_detector_report', 'fields': differences})
        else:
            matches['hc_full_reports' if aid in hc_frozen else 'ipi_full_reports'] += 1
        frozen_paths = hc_frozen[aid]['clean_paths'] if aid in hc_frozen else ipi_comparison[aid]['clean_paths']
        if [c['path'] for c in r['clean_references']] != frozen_paths:
            mismatches.append({'attempt_id': aid, 'type': 'clean_reference_pairing'})
        else:
            matches['three_run_pairings'] += 1
        expected_status = 'negative' if expected['status'] == 'no_detected_effect' else expected['status']
        metric = new[aid]
        final = expected.get('final_answer_impact', expected.get('final_impact'))
        if (metric['classification'] != expected_status or metric['detector_status'] != expected['status']
                or metric['anomalous_event_ids'] != expected['anomalous_event_ids']
                or metric['recovery_events'] != expected['recovery_events']
                or metric['final_answer_impact'] != final):
            mismatches.append({'attempt_id': aid, 'type': 'unified_classification_or_endpoint'})
        else:
            matches['unified_candidate_endpoints'] += 1
    if seen != set(new):
        mismatches.append({'type': 'evidence_population'})
    # Native implementations must still match the code used by frozen runs.
    manifest = json.loads((integrated / 'run_manifest.json').read_text())
    hc_manifest = json.loads((Path(hc_root) / 'manifest.json').read_text())
    preserved = {}
    for path in ('benchmark/hc_baseline_relative.py', 'benchmark/evidence_propagation.py',
                 'benchmark/behavioral_anomaly.py', 'generation/event_graph_builder.py'):
        expected = hc_manifest['input_and_code_sha256'][path]
        preserved[path] = sha(path) == manifest['code_sha256'][path] == expected
        if not preserved[path]:
            mismatches.append({'type': 'changed_detector_or_graph_code', 'path': path})
    summary = {lep: dict(Counter(r['classification'] for r in new.values() if r['lep_code'] == lep)) for lep in DETECTORS}
    verification = {'passed': not mismatches, 'measured_population_exact_match': True,
                    'measured': len(new), 'matches': dict(matches), 'classifications': summary,
                    'preserved_detector_and_graph_code': preserved, 'mismatches': mismatches,
                    'legacy_reason_order_only': {'count': len(reason_order_only), 'attempt_ids': reason_order_only,
                        'interpretation': 'Identical diagnostic reason multisets; legacy set iteration order varies between Python processes. Native outputs are not rewritten.'},
                    'integrated_output': str(integrated.resolve()),
                    'verifier_sha256': sha(__file__),
                    'frozen_sources': {str(p): sha(p) for p in (Path(baseline),
                        Path(hc_root) / 'all_2208/event_evidence.jsonl', Path(ipi_root) / 'event_evidence.jsonl')}}
    write_json(output, verification)
    print(json.dumps({k: v for k, v in verification.items() if k not in ('legacy_reason_order_only', 'frozen_sources')}
                     | {'legacy_reason_order_only_count': len(reason_order_only)}, indent=2))
    if mismatches:
        raise AssertionError(f'{len(mismatches)} frozen reproduction mismatches; see {output}')
    return verification


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--integrated', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, default=Path('analysis/prelim_oct25_combined/results/propagation_metrics.jsonl'))
    parser.add_argument('--hc-root', type=Path, default=Path('analysis/prelim_oct25_hc_baseline_relative_v1'))
    parser.add_argument('--ipi-root', type=Path, default=Path('analysis/prelim_oct25_hc_ipi_v2/results'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    verify(args.integrated, args.baseline, args.hc_root, args.ipi_root, args.output)
