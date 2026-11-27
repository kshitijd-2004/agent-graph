#!/usr/bin/env python3.11
"""Dimension-specific checkpoint (M2) measurement — frame-wide, no composite.

Re-scores the FROZEN checkpoint machinery per component (the audit's §5
feasibility result: `_pair_computation` exposes the per-component `comps` at
the SAME strict-basis, schema-masked construction the composite uses — no new
inference, no LLM, no trace regeneration).  Preserves every frozen restriction:
role-gating, injection-boundary gating, M2O boundary-leak exclusion, non-origin
requirement, many-to-one origin exclusions, delivery verification, explicit
unavailable/undetermined states.

Outputs the per-component per-scope benign-pair pools + LEP per-component
distances, so the dimension-specific checkpoint null is calibrated at the
correct scope (intermediate mask {assertions,actions}; final mask
{assertions,decisions,outcomes}) — NOT by applying the whole-trace threshold.
Writes ledger/checkpoint_pairs.jsonl (one row per cell) — the LONG pole (~38
min full-frame), so it runs first and in the background.
"""
import gc
import json
import os
import sys
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
V2 = os.path.abspath(os.path.join(HERE, '..'))
FINAL = os.path.abspath(os.path.join(HERE, '..', '..'))
REPO = os.path.abspath(os.path.join(FINAL, '..', '..'))
CALP = os.path.join(FINAL, 'calibrated_production')
OUT = os.path.join(V2, 'ledger')

# frozen checkpoint machinery + loaders (read-only)
sys.path.insert(0, os.path.join(FINAL, 'calibrated_production'))
import cp_lib as L  # noqa: E402
sys.path.insert(0, FINAL)
import common  # noqa: E402

DIMS = common.DIMS


def main():
    os.makedirs(OUT, exist_ok=True)
    M = L.load_frozen_mod()
    specs = L.load_specs()
    idx = L.load_trace_index()
    cells = json.load(open(os.path.join(CALP, 'out',
                                        'cache_cells_full.json')))['cells']
    ds_by = {json.loads(l)['trace_id']: json.loads(l)
             for l in open(L.DOWNSTREAM) if l.strip()}

    # per-component checkpoint null (the audit's checkpoint_null_dims, reused)
    def checkpoint_null_dims(leptr, refs_tr, spec, origin_roles):
        W = M.ComponentWeights()
        lep_cell = M.bd.trace_cell(leptr)
        fam = lep_cell.get('task_family') or ''
        boundary = M.injection_boundary_index(leptr)
        lep_view = M.authored_view(leptr)
        ref_views = [M.authored_view(r) for r in refs_tr]
        lep_cps = M.checkpoints_v4(leptr, lep_view)
        ref_cps_list = [M.checkpoints_v4(r, rv)
                        for r, rv in zip(refs_tr, ref_views)]
        key_to_ref = [{c.key: c for c in lc} for lc in ref_cps_list]
        schema = M.build_comparison_schema(refs_tr, spec, W)
        lep_feats = M.extract_features_v4(leptr, spec, lep_view)
        ref_feats = [M.extract_features_v4(r, spec, rv)
                     for r, rv in zip(refs_tr, ref_views)]

        def scoped(full, cp):
            return M._feat_from_checkpoint_v4(full, cp, fam, spec)

        def stg_comps(fa, fb, is_final):
            comp_mask = (schema.final_components if is_final
                         else schema.intermediate_components)
            total, comps, unav = M._pair_computation(
                fa, fb, fam, W, comp_mask, schema.stage_assertion_keys,
                is_final, is_final, strict_basis=True,
                decision_key_mask=schema.decision_required_keys)
            return total, comps, unav, comp_mask

        usable = []
        for c in lep_cps:
            if c.is_origin and not c.origin_authored_response:
                continue
            ab = boundary is None or (c.event_index is not None
                                      and c.event_index > boundary)
            if not c.is_final and not c.is_origin and not ab:
                continue
            if not all(c.key in m for m in key_to_ref):
                continue
            fa = scoped(lep_feats, c)
            dists = []
            for ri, m in enumerate(key_to_ref):
                d, _comp, _un, _mask = stg_comps(
                    fa, scoped(ref_feats[ri], m[c.key]), c.is_final)
                if d is not None:
                    dists.append(d)
            rec = {'key': c.key, 'is_final': c.is_final, '_cp': c,
                   'role': c.role, 'is_cross_agent': c.role not in origin_roles}
            rec['mean_ref_distance'] = (sum(dists) / len(dists)) \
                if dists else None
            # per-component LEP distances at this checkpoint
            comp_lep = {}
            for ri, m in enumerate(key_to_ref):
                d, comps, _un, mask = stg_comps(
                    fa, scoped(ref_feats[ri], m[c.key]), c.is_final)
                if comps:
                    for k in mask:
                        if comps.get(k, (None,))[0] is not None:
                            comp_lep.setdefault(k, []).append(
                                comps[k][0])
            rec['comp_mean'] = {k: (sum(v) / len(v)) for k, v in
                                comp_lep.items()} if comp_lep else {}
            if dists:
                usable.append(rec)
        if not usable:
            return None
        inters = [r for r in usable if not r['is_final']
                  and r['mean_ref_distance'] is not None]
        # per-component BENIGN-pair pools (matched-cardinality checkpoint null)
        def pool_comps(scope_final):
            out = {d: [] for d in DIMS}
            for i in range(len(ref_feats)):
                for j in range(i + 1, len(ref_feats)):
                    if scope_final:
                        if ('final' not in key_to_ref[i]
                                or 'final' not in key_to_ref[j]):
                            continue
                        ci = key_to_ref[i].get('final')
                        cj = key_to_ref[j].get('final')
                    else:
                        if not inters:
                            continue
                        k0 = sorted({r['key'] for r in inters})[0]
                        ci = key_to_ref[i].get(k0)
                        cj = key_to_ref[j].get(k0)
                        if ci is None or cj is None:
                            continue
                    d, comps, _un, mask = stg_comps(
                        scoped(ref_feats[i], ci),
                        scoped(ref_feats[j], cj), scope_final)
                    if d is None:
                        continue
                    for k in mask:
                        if comps.get(k, (None,))[0] is not None:
                            out[k].append(comps[k][0])
            return out
        ben_inter = pool_comps(False)
        ben_final = pool_comps(True)
        return {'usable': [{k: r[k] for k in ('key', 'is_final', 'role',
                                               'is_cross_agent',
                                               'mean_ref_distance',
                                               'comp_mean')}
                           for r in usable],
                'n_intermediate': len(inters),
                'ben_inter': ben_inter, 'ben_final': ben_final,
                'has_final': any(r['is_final'] for r in usable)}

    out_path = os.path.join(OUT, 'checkpoint_pairs.jsonl')
    # RESUMABLE: skip cells already scored in a prior (possibly killed) run
    done: set = set()
    if os.path.exists(out_path):
        for l in open(out_path):
            try:
                r = json.loads(l)
                done.add(r['trace_id'])
            except Exception:
                continue
        print(f'[ckpt] resuming: {len(done)} cells already scored',
              flush=True)
    end = int(os.environ.get('CKPT_END', '0')) or len(cells)
    n_done = 0
    with open(out_path, 'a', buffering=1) as fh:  # line-buffered, flushable
        for ci, c in enumerate(cells):
            if ci >= end:
                break
            if c['trace_id'] in done:
                continue
            tid = c['trace_id']
            fid = tid.split('.')[0]
            spec = specs.get(fid, {})
            drow = ds_by.get(tid, {})
            refs = (drow.get('reference_set') or [])[:3]
            p = idx.get(tid)
            leptr = json.load(open(p)) if p and os.path.exists(p) else None
            refs_tr = []
            ok = leptr is not None
            if ok and len(refs) >= 3:
                for t in refs:
                    pp = idx.get(t)
                    tr = json.load(open(pp)) if pp and os.path.exists(pp) \
                        else None
                    refs_tr.append(tr)
                    if tr is None:
                        ok = False
            origin_roles = drow.get('origin_roles') or []
            if not ok:
                fh.write(json.dumps({'trace_id': tid, 'error': 'unavailable',
                                     'origin_roles': origin_roles,
                                     'delivery_verified': drow.get(
                                         'delivery_verified')}) + '\n')
            else:
                res = checkpoint_null_dims(leptr, refs_tr, spec, origin_roles)
                row = {'trace_id': tid, 'fam': c['fam'],
                       'lep_code': c['lep_code'], 'stratum': c['stratum'],
                       'reference_set': refs,
                       'origin_roles': origin_roles,
                       'delivery_verified': drow.get('delivery_verified'),
                       'm3_reach_class': drow.get('m3_reach_class'),
                       'm2_cross_agent': drow.get('m2_cross_agent'),
                       'm4_class': drow.get('m4_class')}
                if res is None:
                    row['error'] = 'no_usable_checkpoint'
                else:
                    row.update(res)
                fh.write(json.dumps(row) + '\n')
            del leptr, refs_tr
            gc.collect()
            n_done += 1
            if n_done % 500 == 0:
                print(f'[ckpt] {n_done}/{len(cells)}', flush=True)
    print(f'[ckpt] done {n_done} cells -> {out_path}', flush=True)


if __name__ == '__main__':
    # pin the TV float artifact exactly like cp_lib (os.execve)
    if 'PYTHONHASHSEED' not in os.environ:
        os.environ['PYTHONHASHSEED'] = '0'
        os.execve(sys.executable, [sys.executable] + sys.argv,
                  os.environ)
    main()
