#!/usr/bin/env python3.11
"""RETROSPECTIVE reference evaluation across operating points.

STANDING RULE (explicit): the heldout partition was ALREADY consumed
by the original G4/G5 evaluation.  Everything computed here on that
surface is RETROSPECTIVE DIAGNOSTIC, NOT fresh confirmatory
validation.  It answers 'where would each operating point sit on the
observable-divergence reference', which is needed for the
sensitivity-specificity tradeoff (req 7) — but it cannot be used to
select a new operating point (req 7: 'Do not ... select an operating
point based on the resulting propagation rate').

Method: the heldout pair reference (independent adjudicated
observable-divergence labels, 145 evaluable of 216) is joined to
operating-point predictions.  Full-frame operating points are not
computable on the heldout surface (pools would use the full family,
mixing partitions — forbidden).  Instead, PARTITION-LOCAL pools
(heldout-only, the same policy the G5 repair used at evaluation:
'partition-local pool — no cross-partition data at evaluation time')
are used, so the evaluation is apples-to-apples with the reported
G4 surface.  For the 3 family/quantile cells that lack heldout
coverage, the original rule (per-cell max) is the only partition-
local option and is reported as such.

The original rule and the p3_q98_f0_family rule ARE the two
pre-registered rules; the other points are retrospective extras.
"""
import json
import math
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audit_lib as A
sys.path.insert(0, os.path.join(A.CALP))
import cp_lib as L

OUT = os.path.join(A.AUDIT, 'ledger')


def main():
    os.makedirs(OUT, exist_ok=True)
    # heldout partition
    FINAL = A.FINAL
    part = json.load(open(os.path.join(FINAL, 'out',
                                       'partitions_final.json')))
    held = {k for k, v in part['fixtures'].items()
            if (v.get('partition') if isinstance(v, dict) else v)
            == 'heldout'}
    # heldout cells: read the cal experiment's own construction inputs —
    # the cleanest path is to re-run calib_experiment over heldout cells,
    # but that is ~heavy.  Instead: the PARTITION-LOCAL theta tables are
    # already stored for the SELECTED rule in the G5 repair artifacts;
    # for the sweep points we recompute on heldout cells only.
    # heldout frame rows:
    ds = A.ds_by()
    held_rows = [r for r in ds.values()
                 if r['trace_id'].split('.')[0] in held
                 and len(r.get('reference_set') or []) >= 3]
    print('heldout frame rows:', len(held_rows), flush=True)
    specs = A.load_specs()
    idx = A.load_trace_index()

    def _mean(xs):
        vals = [x for x in xs if x is not None]
        return sum(vals) / len(vals) if vals else None

    # extract v6 features for heldout cells
    import features_v6 as F6
    dist = L.v6_dist_factory()
    feat_cache = {}

    # fixture -> family (from the trace index, which carries task_family)
    idx_full = A.load_trace_index_full()
    _FAM_FIX = {}
    for _tid, _info in idx_full.items():
        if _tid.split('.')[0] not in _FAM_FIX:
            _FAM_FIX[_tid.split('.')[0]] = _info.get('task_family')

    def _fam_of(tid):
        # family is a fixture property; benign refs share it with the LEP.
        drow = ds.get(tid)
        if drow:
            return drow['task_family']
        return _FAM_FIX[tid.split('.')[0]]

    def feats(tid):
        if tid not in feat_cache:
            p = idx.get(tid)
            tr = json.load(open(p)) if p and os.path.exists(p) else None
            feat_cache[tid] = (F6.extract_features_v6(
                tr, specs.get(tid.split('.')[0], {}), _fam_of(tid))
                if tr is not None else None)
        return feat_cache[tid]

    cells = []
    for r in held_rows:
        tid = r['trace_id']
        spec = specs.get(tid.split('.')[0], {})
        fam = r['task_family']
        refs = r.get('reference_set') or []
        frefs = [feats(t) for t in refs]
        flep = feats(tid)
        if flep is None or any(f is None for f in frefs) \
                or len(frefs) < 3:
            continue
        lep_d = [dist(flep, frefs[k], spec, fam) for k in range(3)]
        pairs = {}
        for i in range(3):
            for j in range(i + 1, 3):
                pairs[(i, j)] = dist(frefs[i], frefs[j], spec, fam)
        T_lep = {d: _mean([x.get(d) for x in lep_d]) for d in A.DIMS}
        T_p2, T_p3 = [], []
        for bi in range(3):
            o2 = [pairs[(min(bi, k), max(bi, k))]
                  for k in range(3) if k != bi]
            T_p2.append({d: _mean([o.get(d) for o in o2])
                         for d in A.DIMS})
            T_p3.append({d: _mean([o.get(d) for o in ([lep_d[bi]] + o2)])
                         for d in A.DIMS})
        cells.append({'trace_id': tid, 'fam': fam, 'family': fam,
                     'lep_code': r.get('lep_code'),
                     'stratum': (fam, r.get('topology'),
                                 r.get('propagation_mode')),
                     'T_lep': T_lep, 'T_p2': T_p2, 'T_p3': T_p3,
                     'idx': len(cells)})
    print('heldout cells:', len(cells), flush=True)

    rep = {'note': ('RETROSPECTIVE diagnostic on the consumed heldout '
                    'partition: partition-local (heldout-only) pools, '
                    'NO cross-partition data.  This CANNOT select a '
                    'new operating point (standing rule); it only '
                    'locates points on the observable-divergence '
                    'reference.'),
           'n_heldout_cells': len(cells)}
    thr = {}
    for null in ('p2', 'p3'):
        for q in (0.90, 0.95, 0.98, 0.99):
            for pool in ('family', 'stratum'):
                key = f'{null}_q{int(q*100)}_{pool}'
                thr[key] = L.build_theta_table(cells, null_field='T_' + null,
                                               q=q, pool=pool, floor=0.0)
                print('built', key, flush=True)
    # join to reference labels
    refs = {json.loads(l)['lep_trace_id']: json.loads(l)
            for l in open(os.path.join(FINAL, 'out',
                                       'reference_heldout_pairs.jsonl'))}
    cal = A.cal_by()
    out = {}
    for key, t in thr.items():
        tp = fp = tn = fn = excl = 0
        for c in cells:
            rp = refs.get(c['trace_id'])
            if rp is None or rp.get('union_ref') is None:
                continue
            u = bool(rp['union_ref'])
            trig = L.fire(c['T_lep'], t[c['idx']])
            p = bool(trig)
            if p and u:
                tp += 1
            elif p:
                fp += 1
            elif u:
                fn += 1
            else:
                tn += 1
        out[key] = {'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn,
                    'n_eval': tp + fp + fn + tn,
                    'precision': (tp / (tp + fp) if (tp + fp) else None),
                    'recall': (tp / (tp + fn) if (tp + fn) else None)}
    # the original per-cell max rule (partition-independent)
    tp = fp = fn = tn = 0
    for c in cells:
        rp = refs.get(c['trace_id'])
        if rp is None or rp.get('union_ref') is None:
            continue
        u = bool(rp['union_ref'])
        lf = cal.get(c['trace_id'], {})
        p = bool(lf.get('m1_union_orig'))
        if p and u:
            tp += 1
        elif p:
            fp += 1
        elif u:
            fn += 1
        else:
            tn += 1
    out['original_frozen_theta_rule'] = {
        'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn,
        'n_eval': tp + fp + fn + tn,
        'precision': (tp / (tp + fp) if (tp + fp) else None),
        'recall': (tp / (tp + fn) if (tp + fn) else None)}
    rep['operating_points'] = out
    with open(os.path.join(OUT, 'operating_point_reference.json'),
              'w') as fh:
        json.dump(rep, fh, indent=1)
    for k, v in out.items():
        print(k, v)


if __name__ == '__main__':
    main()
