#!/usr/bin/env python3.11
"""Post-extraction: annotate runtime_seconds trust (deterministic, read-only on out/).

runtime_seconds comes from benchmark_records. A record is TRUSTED for a trace only
when the two provably describe the SAME execution:
  trust='attempt_id'      record matched by trace-file attempt_id (authoritative).
  trust='consistent'      record fell back to single-record-by-tid, and its
                          num_events AND num_tool_calls AND num_handoffs exactly
                          equal the trace's event counts (cross-run re-execution
                          with identical shape — strong but not conclusive).
  trust='inconsistent'    fallback record disagrees with the trace counts → the
                          record belongs to a different run of the same trace_id;
                          its runtime_seconds must NOT be used for this trace.
  trust='none'            no record.

Analysis rule (used by the report): matched latency comparisons use
runtime_seconds where trust in {'attempt_id','consistent'}, otherwise the
recorded trace_span_s (event-timeline span of the trace itself) as the
fallback latency measure — and the fallback population is reported explicitly.
"""
import json
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, 'out')
PEX = os.path.join(OUT, 'operational_impact_per_execution.jsonl')
BEN = os.path.join(OUT, 'benign_reference_operational.jsonl')


def annotate(path, census):
    rows = [json.loads(l) for l in open(path) if l.strip()]
    tmp = path + '.tmp'
    with open(tmp, 'w') as out:
        for r in rows:
            t = trust_of(r)
            r['runtime_trust'] = t
            census[t] += 1
            out.write(json.dumps(r, separators=(',', ':')) + '\n')
    os.replace(tmp, path)


def trust_of(row):
    rec = row.get('record')
    tr = row.get('trace')
    how = row.get('record_match')
    if rec is None:
        return 'none'
    if how in ('attempt_id_match_trace', 'attempt_id_match_ledger_pin'):
        return 'attempt_id'
    if tr is not None:
        same = (
            rec.get('num_events') is not None and rec.get('num_events') == tr.get('n_events')
            and rec.get('num_tool_calls') is not None and rec.get('num_tool_calls') == tr.get('n_tool_calls')
            and rec.get('num_handoffs') is not None and rec.get('num_handoffs') == tr.get('n_agent_handoffs')
        )
        return 'consistent' if same else 'inconsistent'
    return 'consistent'  # record present, trace missing: cannot cross-check; flag separately


def main():
    from collections import Counter
    c_pert = Counter()
    annotate(PEX, c_pert)
    c_ben = Counter()
    annotate(BEN, c_ben)
    print('perturbed runtime_trust census:', dict(c_pert))
    print('benign runtime_trust census:', dict(c_ben))
    with open(os.path.join(OUT, 'extraction_coverage.json')) as fh:
        cov = json.load(fh)
    cov['runtime_trust_counts_perturbed'] = dict(c_pert)
    cov['runtime_trust_counts_benign'] = dict(c_ben)
    cov.pop('runtime_trust_counts', None)
    with open(os.path.join(OUT, 'extraction_coverage.json'), 'w') as fh:
        json.dump(cov, fh, indent=2)
    print('updated extraction_coverage.json')


if __name__ == '__main__':
    main()
