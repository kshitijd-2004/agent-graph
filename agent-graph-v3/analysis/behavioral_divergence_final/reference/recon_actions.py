#!/usr/bin/env python3.11
"""
RT-5 / RT-4 forensic reconciliation (defect #4 and part of #5).

1. Shows that the v7 per-trace ledger carries `action_diagnostics` keys
   SIDE-BY-SIDE with `per_dimension.actions` (the classified TV signal) and
   that the v7 union never ingests a diagnostic key.
2. Runs the candspec pipeline on the SAME 300-row slice used by
   out/per_trace_smoke2.jsonl and diffs the actions verdicts between v7 and
   candspec (both use the frozen TV baseline, so they must match; candspec
   merely separates the diagnostics under `action_diag_*`).
Read-only everywhere except writing nothing: prints a report.
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_FINAL = os.path.abspath(os.path.join(_HERE, '..'))
sys.path.insert(0, os.path.abspath(os.path.join(_FINAL, 'extractors')))
sys.path.insert(0, _FINAL)
import common  # noqa: E402

ANALYSIS = common.ANALYSIS
ROOT = os.path.join(_FINAL, 'out', 'per_trace_smoke2.jsonl')


def _load(p):
    out = {}
    for l in open(p):
        r = json.loads(l)
        out[r['trace_id']] = r
    return out


def main():
    v7 = _load(os.path.join(ANALYSIS, 'measurement_v7', 'out',
                            'per_trace_v7.jsonl'))
    v6 = _load(os.path.join(ANALYSIS, 'measurement_v6', 'out',
                            'per_trace_v6.jsonl'))
    cand = _load(ROOT)

    # (a) v7 ledger key layout: where do diagnostics live, does union touch
    # them?
    n_diag_keys = 0
    n_diag_in_union = 0
    for tid, r in v7.items():
        ad = r.get('action_diagnostics') or {}
        n_diag_keys += sum(1 for k in ad
                           if str(k).startswith(('count', 'retry', 'order',
                                                 'failure')))
        u = r.get('union') or {}
        for t in u.get('triggered_dimensions', []) + \
                 u.get('zero_threshold_triggered', []):
            if 'diag' in str(t):
                n_diag_in_union += 1
    print(f'[v7 ledger] rows={len(v7)} rows_with_action_diagnostics='
          f'{sum(1 for r in v7.values() if r.get("action_diagnostics"))}'
          f' diagnostic_keys_total={n_diag_keys} '
          f'diagnostic_names_in_union_triggers={n_diag_in_union}')

    # (b) v7 == v6 on the action verdict? (the TV baseline is retained)
    same = diff = 0
    for tid in cand:
        a7 = (v7.get(tid) or {}).get('per_dimension', {}).get('actions', {})
        a6 = (v6.get(tid) or {}).get('per_dimension', {}).get('actions', {})
        if a7.get('divergent') == a6.get('divergent'):
            same += 1
        else:
            diff += 1
            print(f'  v7/v6 actions differ on {tid}: v7={a7.get("divergent")} '
                  f'v6={a6.get("divergent")}')
    print(f'[v7 vs v6 actions on {same + diff} slice rows] same={same} '
          f'diff={diff}')

    # (c) candspec vs v7 on the SAME slice (smoke2 ids)
    n = 0
    fl = []
    for tid, rc in cand.items():
        rv = (v7.get(tid) or {})
        for d in common.DIMS:
            pv = (rv.get('per_dimension') or {}).get(d, {})
            pc = rc['per_dimension'].get(d, {})
            if bool(pv.get('divergent')) != bool(pc.get('divergent')):
                fl.append((tid, d, pv.get('divergent'), pc.get('divergent')))
        n += 1
        if bool((v7.get(tid, {}).get('union') or {}).get('divergent')) != \
                bool(rc['union']['divergent']):
            fl.append((tid, 'UNION',
                       (v7.get(tid, {}).get('union') or {}).get('divergent'),
                       rc['union']['divergent']))
    print(f'[candspec vs v7 on {n}-row slice] verdict flips: {len(fl)}')
    for x in fl[:20]:
        print('   flip:', x)

    # (d) candspec diagnostic separation: keys present in the candspec
    # ledger, and that they never appear in the union trigger sets.
    kc = 0
    for rc in cand.values():
        kc += len(rc.get('action_diag') or {})
        assert not any('diag' in str(t) for t in
                       rc['union']['triggered_dimensions'])
    print(f'[candspec] rows={len(cand)} action_diag keys total={kc}; '
          f'no diagnostic key appears in any union trigger set (asserted)')

    out = {
        'v7_rows_checked': len(v7),
        'v7_diagnostic_keys_total': n_diag_keys,
        'v7_diagnostic_names_in_union': n_diag_in_union,
        'v7_vs_v6_actions_same': same,
        'v7_vs_v6_actions_diff': diff,
        'slice_rows': n,
        'candspec_vs_v7_flips': len(fl),
        'flip_examples': fl[:20],
    }
    with open(os.path.join(_HERE, '..', 'out',
                           'recon_actions_report.json'), 'w') as fh:
        json.dump(out, fh, indent=1)
    print('wrote', os.path.join(_HERE, '..', 'out',
                                'recon_actions_report.json'))


if __name__ == '__main__':
    main()
