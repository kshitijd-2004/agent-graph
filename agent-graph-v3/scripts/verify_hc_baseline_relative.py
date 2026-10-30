"""Verify saved HC candidate evidence against original recorded trace bytes."""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path

from benchmark import hc_baseline_relative as hc
from benchmark import evidence_propagation as v2
from benchmark.propagation_analysis import PropagationAnalyzer
from scripts.analyze_hc_baseline_relative import digest, read_rows, write_json


def verify(root):
    root = Path(root)
    manifest = json.loads((root / 'manifest.json').read_text())
    for path, expected in manifest['input_and_code_sha256'].items():
        assert digest(path) == expected, path
    for path, expected in json.loads((root / 'output_hashes.json').read_text()).items():
        assert digest(root / path) == expected, path
    initial = {r['attempt_id']: r for r in read_rows(root / 'initial_122/event_evidence.jsonl')}
    full = {r['attempt_id']: r for r in read_rows(root / 'all_2208/event_evidence.jsonl')}
    assert len(initial) == 122 and len(full) == 2208
    assert all(full[aid] == r for aid, r in initial.items())
    positives = read_rows(root / 'all_2208/positive_evidence.jsonl')
    assert {r['attempt_id'] for r in positives} == {aid for aid, r in full.items() if r['status'] == 'positive'}
    counts = Counter(r['status'] for r in full.values())
    assert set(counts) <= {'positive', 'negative', 'unknown'}
    assert counts == json.loads((root / 'all_2208/summary.json').read_text())['classifications']
    trace_cache = {}
    def load(path):
        if path not in trace_cache:
            trace_cache[path] = PropagationAnalyzer._trace_from_dict(json.loads(Path(path).read_text()), Path(path))
        return trace_cache[path]
    effects = witnesses = edges_checked = 0
    keys = Counter()
    for r in positives:
        trace = load(r['path'])
        events = {e.event_id: e for e in trace.events}
        spec = json.loads((Path('workspace_fixtures') / trace.metadata['fixture_id'] / 'manifest.json').read_text())
        edges, ancestors, _ = v2.reconstruct_graph(trace)
        for row in r['events']:
            assert row['status'] == 'positive' and row['evidence']
            observed_event = events[row['event_id']]
            for ev in row['evidence']:
                effects += 1
                keys[ev['key']] += 1
                expected = ev['stable_benign_expectation']['value']
                assert ev['observed_emitted_text'] == v2.emitted_text(observed_event)
                assert ev['observed_hc_deviation'] == hc.asdict(hc.extract_assertions(ev['observed_emitted_text'], spec)[ev['key']])
                assert not v2.same(ev['observed_hc_deviation']['value'], expected, ev['key'], spec)
                groups = ev['stable_benign_expectation']['witnesses_by_run']
                assert len(groups) == 3 and all(groups)
                repetitions, source_paths = set(), set()
                for group in groups:
                    assert len({w['source_path'] for w in group}) == 1
                    for w in group:
                        benign = load(w['source_path'])
                        assert w['source_path'] in r['clean_paths']
                        assert benign.metadata['repetition_index'] == w['repetition_index']
                        repetitions.add(w['repetition_index']); source_paths.add(w['source_path'])
                        event = next(e for e in benign.events if e.event_id == w['event_id'])
                        assert v2.emitted_text(event) == w['emitted_text']
                        actual = hc.extract_assertions(w['emitted_text'], spec)[ev['key']]
                        assert actual.status == 'asserted' and actual.value == expected
                        assert hc.asdict(actual) == w['assertion']
                        witnesses += 1
                assert len(repetitions) == len(source_paths) == 3
                b = ev['intervention_boundary']
                origin = events[b['origin_id']]
                assert origin.event_labels.is_injection_origin
                assert v2.mutation_pair(origin, trace) == (b['before'], b['after'])
                assert b['before'] != b['after']
                path = ev['path']
                assert path[0] == origin.event_id and path[-1] == observed_event.event_id
                assert ev['delivery_event_id'] in path and ev['receiver_exposure_event_id'] in path
                delivery = events[ev['delivery_event_id']]
                exposure = events[ev['receiver_exposure_event_id']]
                assert origin.event_id in delivery.depends_on
                assert delivery.agent_role == exposure.agent_role != origin.agent_role
                assert path.index(delivery.event_id) < path.index(exposure.event_id)
                assert len(ev['path_edges']) == len(path) - 1
                for (a, b), recorded in zip(zip(path, path[1:]), ev['path_edges']):
                    assert (events[a].event_index, events[b].event_index) in edges
                    assert recorded['from'] == a and recorded['to'] == b
                    edges_checked += 1
                assert origin.event_index in ancestors[observed_event.event_index]
    result = {'all_input_and_code_hashes_verified': len(manifest['input_and_code_sha256']),
              'historical_results_and_shared_detectors_unchanged': True,
              'initial_122_equal_to_full_evaluation': True, 'measured': len(full),
              'classifications': dict(counts), 'positive_traces_verified': len(positives),
              'effect_records_verified': effects, 'benign_witness_records_verified': witnesses,
              'graph_edges_verified': edges_checked, 'effect_records_by_key': dict(keys),
              'verifier_sha256': digest(__file__)}
    write_json(root / 'verification.json', result)
    print(json.dumps(result, indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    verify(args.root)
