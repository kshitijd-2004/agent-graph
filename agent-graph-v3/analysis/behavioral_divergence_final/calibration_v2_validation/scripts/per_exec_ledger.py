#!/usr/bin/env python3.11
"""Machine-readable per-execution ledger + fixture-cluster CI table.

Per-execution ledger: one row per cohort-A LEP execution carrying the
four rule predictions (S1/C0/C1/V4.1 union + per-dim fires + O1
outcome p), the independent reference label (+ provenance + basis),
the design weight, task family / LEP / topology / mode, and the
unresolved flag.  This is the join every report table is computed
from, and it is what PropHunter would consume as a label row.

CI table: fixture-cluster bootstrap 95% CIs for each rule's union
positive-rate on cohort A, the design-weighted error rate, and the
per-family CI.  Writes ledger/per_execution_ledger.jsonl +
ledger/ci_tables.json.
"""
import json
import os
import random
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
STUDY = os.path.abspath(os.path.join(HERE, '..'))
V2 = os.path.abspath(os.path.join(STUDY, '..', 'calibration_v2'))
LED = os.path.join(STUDY, 'ledger')
OUT = os.path.join(STUDY, 'out')
sys.path.insert(0, V2)
import cv2  # noqa: E402

SEED_CI = cv2.SEED_CI
N_ITER_CI = cv2.N_ITER_CI
RULES = ('S1_shrinkage', 'C0_original', 'C1_family_q98', 'V41_union')


def _rows(p):
    raw = open(os.path.join(LED, p)).read()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return [json.loads(l) for l in raw.splitlines() if l.strip()]


def fixture_ci(flags, seed=SEED_CI, n=N_ITER_CI):
    """fixture-cluster bootstrap CI on the pooled positive rate."""
    if not flags:
        return [None, None]
    rng = random.Random(seed)
    keys = list(flags)
    means = []
    for _ in range(n):
        samp = [x for _k in keys for x in flags[rng.choice(keys)]]
        means.append(sum(1.0 for x in samp if x) / len(samp))
    means.sort()
    lo = means[min(len(means) - 1, int(0.025 * len(means)))]
    hi = means[min(len(means) - 1, int(0.975 * len(means)))]
    return [lo, hi]


def main():
    man = json.load(open(os.path.join(LED, 'sample_manifest.json')))
    A = man['cohort_a_representative']
    refs = {r['pack_id']: r for r in _rows('reference_labels.jsonl')}
    rules = defaultdict(dict)
    for r in _rows('rule_labels.jsonl'):
        rules[r['trace_id']][r['rule']] = r

    # O1 per-trace outcome p from the cv2 scoring (frozen)
    cells, _ = cv2.load_cells()
    full = cv2.load_full_cells()
    v6dim = cv2.load_v6_dim()
    pools = cv2.build_pools(full)
    zt = cv2.shrinkage_weights(full, v6dim, cv2.build_pair_pools(full),
                               pools)
    onull = cv2.outcome_null(full)
    s1row = {r['trace_id']: r for r in cv2.score_candidate(
        full, v6dim, pools, zt, onull, 'S1_shrinkage')}

    per = []
    for tid in A:
        rA = rules.get(tid, {})
        ref = refs.get('A__' + tid, {})
        s1r = s1row.get(tid, {})
        row = {
            'trace_id': tid,
            'pack_id': 'A__' + tid,
            'task_family': ref.get('task_family'),
            'lep_code': (next((c['lep_code'] for c in cells
                               if c['trace_id'] == tid), None)),
            'stratum': (next((list(c['stratum']) for c in cells
                              if c['trace_id'] == tid), None)),
            'design_weight': man['design_weights'].get(tid),
            'ref_label': ref.get('label'),
            'ref_provenance': ref.get('provenance'),
            'ref_basis': ref.get('label_basis'),
            'ref_per_dim': ref.get('per_dim'),
            'rules': {},
        }
        for rn in RULES:
            rr = rA.get(rn)
            row['rules'][rn] = {'union': rr.get('union') if rr else
                                None,
                                'fires': rr.get('fires') if rr else None,
                                'undetermined': rr.get('undetermined')
                                if rr else None}
        row['rules']['S1_shrinkage']['outcome_p_emp'] = \
            s1r.get('fires', {}).get('outcomes_p_emp')
        row['rules']['S1_shrinkage']['outcome_undetermined'] = \
            s1r.get('outcome_undetermined')
        per.append(row)
    with open(os.path.join(LED, 'per_execution_ledger.jsonl'), 'w') as fh:
        for row in per:
            fh.write(json.dumps(row, sort_keys=True) + '\n')
    print('per_execution_ledger.jsonl:', len(per), 'rows')

    # CI tables
    def fx(t):
        return t.split('.')[0]

    ci = {'n_iter_ci': N_ITER_CI, 'seed_ci': SEED_CI,
          'per_rule_union_rate_ci': {},
          'design_weighted_error_rate': {},
          'per_family_ci': {}}
    famrows = defaultdict(lambda: defaultdict(list))
    for row in per:
        fam = row['task_family'] or 'unknown'
        for rn in RULES:
            u = row['rules'][rn]['union']
            if u is None:
                continue
            famrows[rn][fam].append((fx(row['trace_id']), bool(u)))
    for rn in RULES:
        byfx = defaultdict(list)
        for fam, v in famrows[rn].items():
            for f, u in v:
                byfx[f].append(u)
        rate = sum(1 for v in byfx.values() for u in v if u) / \
            (sum(len(v) for v in byfx.values()) or 1)
        ci['per_rule_union_rate_ci'][rn] = {
            'rate': rate,
            'ci95_fixture_cluster': fixture_ci(dict(byfx)),
            'n_fixtures': len(byfx),
            'n_eval': sum(len(v) for v in byfx.values())}
        # design-weighted error rate vs the reference
        wsum = werr = 0.0
        for row in per:
            u = row['rules'][rn]['union']
            if u is None or row['ref_label'] is None:
                continue
            w = row['design_weight'] or 1.0
            wsum += w
            if bool(u) != (row['ref_label'] == 'diverges'):
                werr += w
        ci['design_weighted_error_rate'][rn] = {
            'error_rate': (werr / wsum if wsum else None),
            'n_weighted': int(wsum)}
    json.dump(ci, open(os.path.join(LED, 'ci_tables.json'), 'w'),
              indent=1)
    print('ci_tables.json: rule union rates + CI + dw error')
    for rn in RULES:
        e = ci['design_weighted_error_rate'][rn]
        r = ci['per_rule_union_rate_ci'][rn]
        print(f'  {rn:16s} rate={r["rate"]:.3f} ci={r["ci95_fixture_cluster"]}'
              f' dw_err={e["error_rate"]}')


if __name__ == '__main__':
    if 'PYTHONHASHSEED' not in os.environ:
        os.environ['PYTHONHASHSEED'] = '0'
        os.execve(sys.executable, [sys.executable] + sys.argv,
                  os.environ)
    main()
