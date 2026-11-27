#!/usr/bin/env python3.11
"""ANNOTATION_COST_REPORT generator (requirement 5 + addendum A1).

Reads out/call_ledger*.jsonl (the provenance-tracked per-call
ledger) and reports: call counts by phase/judge/model, input/output
token totals, estimated cost at the authorized models' published
list rates (flagged as estimates where the relay meters
differently), the pilot's mechanics findings, the parallel-run
failure analysis, and the addendum-A1 reallocation.  No LLM calls.
Writes ledger/annotation_cost.json + the markdown source is this
script's stdout (the report file is written separately).
"""
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
STUDY = os.path.abspath(os.path.join(HERE, '..'))
OUT = os.path.join(STUDY, 'out')
LED = os.path.join(STUDY, 'ledger')

# published list rates (USD / 1M tokens; estimates — the relay's own
# metering may differ; flagged in the report)
RATES = {'claude-opus-5-5': {'in': 5.0, 'out': 25.0},
         'claude-sonnet-5-5': {'in': 3.0, 'out': 15.0},
         'claude-opus-4-8': {'in': 15.0, 'out': 75.0}}


def main():
    recs = []
    for f in sorted(os.listdir(OUT)):
        if f == 'call_ledger.jsonl' or (f.startswith('call_ledger.')
                                         and f.endswith('.jsonl')):
            for l in open(os.path.join(OUT, f)):
                if l.strip():
                    recs.append(json.loads(l))
    by = {}
    for r in recs:
        m = r.get('model') or 'unknown'
        u = r.get('usage') or {}
        ent = by.setdefault(m, {'n': 0, 'in': 0, 'out': 0,
                                'cache_read': 0, 'ok': 0,
                                'retries': 0, 'failed_empty': 0})
        ent['n'] += 1
        ent['in'] += u.get('input_tokens') or 0
        ent['out'] += u.get('output_tokens') or 0
        ent['cache_read'] += u.get('cache_read_input_tokens') or 0
        ent['ok'] += int(bool(r.get('ok')))
        ent['retries'] += (r.get('attempts') or 1) - 1
        # a live annotation call that returned no usable text
        if r.get('task', '').startswith('annotate') and not \
                (r.get('usage') or {}).get('input_tokens'):
            ent['failed_empty'] += 1
    cost = 0.0
    for m, ent in by.items():
        rt = RATES.get(m, {'in': 5.0, 'out': 25.0})
        ent['cost_usd_estimate'] = round(
            ent['in'] / 1e6 * rt['in'] + ent['out'] / 1e6 * rt['out'], 3)
        cost += ent['cost_usd_estimate']
    rep = {
        'total_live_calls': len(recs),
        'by_model': by,
        'total_cost_usd_estimate': round(cost, 3),
        'rate_note': ('published list rates (USD/M tokens): opus-5-5 '
                      '5/25, sonnet-5-5 3/15, opus-4-8 15/75. '
                      'ESTIMATES — the relay meters at its own rates; '
                      'token counts are the relay-reported usage '
                      'fields.'),
        'pilot_mechanics': {
            'v1': 'executions block missing from the prompt -> '
                  'all-unresolved (caught pre-confirmatory)',
            'v2': '4096-token truncation on long packets -> 3 '
                  'invalid judge_a responses (caught pre-confirmatory)',
            'v3': '8192 max_tokens + retry loop; 20/20 valid pilot'},
        'parallel_run_failure_analysis': {
            'detail': ('the 4-way parallel confirmatory run hit '
                      'relay concurrency failures on '
                      'claude-sonnet-5-5: 80 cached empty responses '
                      '(input_tokens=0). Sequential re-runs recover '
                      'them at the pilot failure rate (~10%).'),
            'affected_model': 'claude-sonnet-5-5',
            'addendum': 'A1 (prereg §9): remaining 134 calls '
                        'reallocated by priority, results-blind; '
                        'cohort B degraded to judge_a-only where '
                        'unreached (disclosed; B is diagnostic-only, '
                        'C4 gate is deterministic).'},
    }
    json.dump(rep, open(os.path.join(LED, 'annotation_cost.json'),
                        'w'), indent=1)
    print('total live calls:', len(recs))
    for m, e in sorted(by.items()):
        print(f'  {m}: n={e["n"]} in={e["in"]:,} out={e["out"]:,} '
              f'failed_empty={e["failed_empty"]} '
              f'est_cost=${e["cost_usd_estimate"]}')
    print('est total cost: $%.2f' % cost)
    print('headroom under 600:', 600 - len(recs))
    print('wrote ledger/annotation_cost.json')


if __name__ == '__main__':
    main()
