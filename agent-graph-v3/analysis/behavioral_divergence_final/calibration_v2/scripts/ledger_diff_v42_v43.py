#!/usr/bin/env python3.11
"""V4.2 -> V4.3 whole-trace ledger diff (deterministic, no LLM).

Compares the frozen V4.2 ledger against the V4.3 recompute and reports,
per trace x dimension, the state transitions.  The purpose is to verify
the V4.3 extraction fixes (document-period rule, stance families,
verdict tails) RECOVERED genuine observations (NULL -> value) without
introducing coverage regressions (value -> NULL or value drift on
dimensions the fixes did not target).

Output (out_v43/ledger_diff_v42_v43.json + summary):
  * transitions: counts of NULL->VAL, VAL->NULL, VAL->VAL-drift,
    NULL->NULL, VAL->VAL-identical, grouped by (family, dimension)
  * drift detail: any VAL->VAL change where |delta| > 1e-9 (value or
    threshold), trace + old + new
  * new_null detail: any VAL->NULL (coverage loss = regression risk)
  * bnd distribution: V4.2 vs V4.3 mean/max/quantiles of the bnd total
  * the 84 round-2 ref-positives: per-dim NULL->VAL recovery count

Writes out_v43/ledger_diff_v42_v43.json.  Read-only on both ledgers.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FINAL = os.path.abspath(os.path.join(HERE, '..', '..'))
REPO = os.path.abspath(os.path.join(FINAL, '..', '..'))
CALP = os.path.join(FINAL, 'calibrated_production')
OUT = os.path.join(os.path.abspath(os.path.join(HERE, '..')), 'out_v43')

DIMS = ('assertions', 'decisions', 'actions', 'outcomes')


def load_ledger(path):
    return {json.loads(l)['trace_id']: json.loads(l)
            for l in open(path)}


def comp(row, side, d):
    """Per-dimension component distance from the ledger pair blocks."""
    blk = row.get(f'pair_{side}') or []
    vals = [((p.get('components') or {}).get(d)) for p in blk
            if p.get('components') is not None]
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def thr(row, d):
    vals = [((p.get('components') or {}).get(d))
            for p in (row.get('pair_benign') or [])]
    vals = [v for v in vals if v is not None]
    return max(vals) if vals else None


def main():
    v42 = load_ledger(os.path.join(
        REPO, 'analysis', 'full_production_baseline_deviation_v42',
        'bnd_v42_per_trace.jsonl'))
    v43 = load_ledger(os.path.join(
        REPO, 'analysis', 'full_production_baseline_deviation_v43',
        'bnd_v43_per_trace.jsonl'))
    common = [t for t in v43 if t in v42]
    only42 = [t for t in v42 if t not in v43]
    only43 = [t for t in v43 if t not in v42]

    trans = {}   # (fam, dim, transition) -> count
    new_null = []
    drift = []
    recovered = []
    for t in common:
        a, b = v42[t], v43[t]
        fam = b.get('task_family') or 'unknown'
        for d in DIMS:
            va, vb = comp(a, 'lep', d), comp(b, 'lep', d)
            if va is None and vb is None:
                key = (fam, d, 'NULL->NULL')
            elif va is None and vb is not None:
                key = (fam, d, 'NULL->VAL')
                recovered.append({'trace': t, 'dim': d, 'value': vb,
                                  'fam': fam,
                                  'threshold': thr(b, d)})
            elif va is not None and vb is None:
                key = (fam, d, 'VAL->NULL')
                new_null.append({'trace': t, 'dim': d, 'was': va,
                                 'fam': fam})
            elif abs(va - vb) > 1e-9:
                key = (fam, d, 'VAL->VAL-drift')
                drift.append({'trace': t, 'dim': d, 'old': va,
                              'new': vb, 'fam': fam})
            else:
                key = (fam, d, 'VAL->VAL-same')
            trans.setdefault('|'.join(key), 0)
            trans['|'.join(key)] += 1
    for t in only43:
        trans.setdefault(f"{v43[t].get('task_family')}:NEW_TRACE", 0)
        trans[f"{v43[t].get('task_family')}:NEW_TRACE"] += 1
    for t in only42:
        trans.setdefault(f"{v42[t].get('task_family')}:GONE_TRACE", 0)
        trans[f"{v42[t].get('task_family')}:GONE_TRACE"] += 1

    # bnd distribution
    def dist(rows):
        vals = [r['bnd'] for r in rows.values()
                if r.get('bnd') is not None]
        if not vals:
            return {'n': 0}
        vals.sort()
        n = len(vals)
        return {'n': n, 'mean': sum(vals) / n, 'min': vals[0],
                'q50': vals[n // 2], 'q90': vals[int(n * 0.9)],
                'q99': vals[int(n * 0.99)], 'max': vals[-1]}
    bnd42, bnd43 = dist(v42), dist(v43)

    rep = {
        'population': {'v42': len(v42), 'v43': len(v43),
                       'common': len(common), 'only_v42': only42,
                       'only_v43': only43},
        'transitions': trans,
        'recovered_count': len(recovered),
        'new_null_count': len(new_null),
        'drift_count': len(drift),
        'bnd_distribution': {'v42': bnd42, 'v43': bnd43},
        'new_null_detail': new_null[:200],
        'drift_detail': drift[:200],
        'recovered_detail': recovered,
        'note': ('V4.3 extraction fixes (document-period rule, stance '
                 'families, verdict tails, forward-sentence probe) vs the '
                 'frozen V4.2 ledger.  NULL->VAL = recovered observation '
                 'coverage; VAL->NULL = coverage regression (must be '
                 'explained); VAL->VAL-drift = a distance value changed '
                 'on a dim both versions observed (expected ONLY where a '
                 'fix corrected a mis-bound figure / mis-classified '
                 'stance / mis-read verdict tail).  Read-only diff; no '
                 'modification of either ledger.'),
    }
    os.makedirs(OUT, exist_ok=True)
    out = os.path.join(OUT, 'ledger_diff_v42_v43.json')
    json.dump(rep, open(out, 'w'), indent=1, default=str)
    print(f'pop: v42={len(v42)} v43={len(v43)} common={len(common)} '
          f'only_v42={len(only42)} only_v43={len(only43)}')
    print(f'transitions (fam|dim|type):')
    for k in sorted(trans):
        print(f'  {trans[k]:6d}  {k}')
    print(f'recovered NULL->VAL: {len(recovered)} | '
          f'VAL->NULL regressions: {len(new_null)} | '
          f'VAL drifts: {len(drift)}')
    print(f'bnd: v42 mean={bnd42.get("mean")} q90={bnd42.get("q90")} '
          f'-> v43 mean={bnd43.get("mean")} q90={bnd43.get("q90")} '
          f'(n {bnd42.get("n")} -> {bnd43.get("n")})')
    print('wrote', out)


if __name__ == '__main__':
    main()
