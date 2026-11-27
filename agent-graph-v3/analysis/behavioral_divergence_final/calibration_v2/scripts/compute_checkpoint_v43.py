#!/usr/bin/env python3.11
"""Dimension-specific checkpoint (M2) re-score on the V4.3 extractor.

V4.2 correctness fixes over the frozen compute_checkpoint.py:

  (A) any-dimension retention: a checkpoint is USABLE iff ANY independently
      evaluable dimension has a distance.  Per-dimension availability is
      recorded (comp_eval); the composite total is reported for context
      ONLY and NEVER gates admission — a dim is kept whenever its own
      component distance is evaluable, regardless of the other dims.
  (B) role-authored evidence scope: assertion/verdict/stance extraction at
      each checkpoint runs over the role's evidence surface (own state_text
      + earlier same-role handoffs + bodies of files the role wrote before
      the checkpoint) via the V4.2 module's _feat_from_checkpoint_v4 —
      intermediate authored reports included, not merely handoff text.
  (C) per-key benign alignment: the per-component benign pool is built per
      checkpoint KEY (handoff:role#it / final), comparing corresponding
      benign roles/iterations/scopes — not the lexicographically-first
      intermediate key across all cells (the frozen pool_comps defect).
  (D) family-specific checkpoint pools: each cell records its OWN
      per-key/per-dim benign-pair observations; downstream
      compute_production_v42 pools them per (task_family, scope, dim) —
      NOT the global cross-family pool (the frozen
      compute_production.py:67 defect).

Per-cell output row extends the frozen schema with:
  * usable[].comp_eval   {dim: bool}  — independently evaluable dims
  * usable[].ben_by_key {dim: [obs]}  — this cell's benign-pair obs at key
  * usable[].key, iteration, n_ref_pairs
All writes under calibration_v2/out_v43/.  Deterministic, no LLM calls,
frozen artifacts untouched.  Writes only (out_v42 is inside FINAL_DIR).
"""
import gc
import json
import os
import sys
from collections import defaultdict
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
V2 = os.path.abspath(os.path.join(HERE, '..'))
FINAL = os.path.abspath(os.path.join(HERE, '..', '..'))
REPO = os.path.abspath(os.path.join(FINAL, '..', '..'))
CALP = os.path.join(FINAL, 'calibrated_production')
OUT = os.path.join(V2, 'out_v43')

sys.path.insert(0, os.path.join(FINAL, 'calibrated_production'))
import cp_lib as L  # noqa: E402
sys.path.insert(0, FINAL)
import common  # noqa: E402
sys.path.insert(0, REPO)
import importlib.util as _ilu

DIMS = common.DIMS


def load_v43_mod():
    if 'v43_mod' in sys.modules:
        return sys.modules['v43_mod']
    spec = _ilu.spec_from_file_location(
        'v43_mod', os.path.join(REPO, 'benchmark',
                                'baseline_deviation_v43.py'))
    m = _ilu.module_from_spec(spec)
    sys.modules['v43_mod'] = m
    spec.loader.exec_module(m)
    return m


def _comps_of(M, fa, fb, fam, W, schema, is_final):
    """V4.2 any-dimension: per-dimension component distances on the FIXED
    schema mask; each dim independently evaluable.  The weighted total is
    returned for context but is NEVER used for admission."""
    comp_mask = (schema.final_components if is_final
                 else schema.intermediate_components)
    _total, comps, _unav = M._pair_computation(
        fa, fb, fam, W, comp_mask, schema.stage_assertion_keys,
        is_final, is_final, strict_basis=True,
        decision_key_mask=schema.decision_required_keys)
    return comps, comp_mask


def checkpoint_null_v42(M, leptr, refs_tr, spec, origin_roles):
    W = M.ComponentWeights()
    fam = M.bd.trace_cell(leptr).get('task_family') or ''
    boundary = M.injection_boundary_index(leptr)
    lep_view = M.authored_view(leptr)
    ref_views = [M.authored_view(r) for r in refs_tr]
    lep_cps = M.checkpoints_v4(leptr, lep_view)
    ref_cps_list = [M.checkpoints_v4(r, rv) for r, rv in
                    zip(refs_tr, ref_views)]
    key_to_ref = [{c.key: c for c in lc} for lc in ref_cps_list]
    schema = M.build_comparison_schema(refs_tr, spec, W)
    lep_feats = M.extract_features_v4(leptr, spec, lep_view)
    ref_feats = [M.extract_features_v4(r, spec, rv)
                 for r, rv in zip(refs_tr, ref_views)]

    def scoped(full, cp):
        return M._feat_from_checkpoint_v4(full, cp, fam, spec)

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
        # (A) per-dimension LEP distances at THIS key
        per_dim_lep: Dict[str, List[float]] = defaultdict(list)
        for ri, m in enumerate(key_to_ref):
            rc = m.get(c.key)
            comps, _mask = _comps_of(
                M, fa, scoped(ref_feats[ri], rc), fam, W, schema,
                c.is_final)
            for k in DIMS:
                v = (comps.get(k) or (None,))[0]
                if v is not None:
                    per_dim_lep[k].append(v)
        comp_mean = {k: (sum(v) / len(v)) for k, v in per_dim_lep.items()}
        comp_n = {k: len(v) for k, v in per_dim_lep.items()}
        if not comp_mean:
            continue  # (A): nothing independently evaluable -> not usable
        # (C) benign-pair pool at THIS key + scope (corresponding refs)
        ben_by_key_dim: Dict[str, List[float]] = defaultdict(list)
        for i in range(len(ref_feats)):
            for j in range(i + 1, len(ref_feats)):
                ci, cj = key_to_ref[i].get(c.key), key_to_ref[j].get(c.key)
                if ci is None or cj is None:
                    continue
                comps, _mask = _comps_of(
                    M, scoped(ref_feats[i], ci), scoped(ref_feats[j], cj),
                    fam, W, schema, c.is_final)
                for k in DIMS:
                    v = (comps.get(k) or (None,))[0]
                    if v is not None:
                        ben_by_key_dim[k].append(v)
        usable.append({
            'key': c.key, 'is_final': c.is_final,
            'role': c.role, 'iteration': c.iteration,
            'is_cross_agent': c.role not in origin_roles,
            'n_ref_pairs': len(key_to_ref),
            'comp_mean': comp_mean,
            'comp_n': comp_n,
            'comp_eval': {k: (k in comp_mean) for k in DIMS},
            'ben_by_key': dict(ben_by_key_dim),
        })
    if not usable:
        return None
    return {'usable': usable,
            'n_intermediate': len([u for u in usable
                                   if not u['is_final']]),
            'has_final': any(u['is_final'] for u in usable)}


def main():
    os.makedirs(OUT, exist_ok=True)
    M = load_v43_mod()
    specs = L.load_specs()
    idx = L.load_trace_index()
    cells = json.load(open(os.path.join(CALP, 'out',
                                        'cache_cells_full.json')))['cells']
    ds_by = {json.loads(l)['trace_id']: json.loads(l)
             for l in open(L.DOWNSTREAM) if l.strip()}

    out_path = os.path.join(OUT, 'checkpoint_pairs_v43.jsonl')
    done: set = set()
    if os.path.exists(out_path):
        for l in open(out_path):
            try:
                done.add(json.loads(l)['trace_id'])
            except Exception:
                continue
        print(f'[ckpt43] resuming: {len(done)} cells already scored',
              flush=True)
    end = int(os.environ.get('CKPT_END', '0')) or len(cells)
    n_done = 0
    with open(out_path, 'a', buffering=1) as fh:
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
            refs_tr, ok = [], leptr is not None
            if ok and len(refs) >= 3:
                for t in refs:
                    pp = idx.get(t)
                    tr = json.load(open(pp)) if pp and os.path.exists(pp) \
                        else None
                    refs_tr.append(tr)
                    if tr is None:
                        ok = False
            origin_roles = set(drow.get('origin_roles') or [])
            if not ok:
                fh.write(json.dumps({'trace_id': tid,
                                     'error': 'unavailable',
                                     'origin_roles': sorted(origin_roles),
                                     'delivery_verified':
                                     drow.get('delivery_verified')}) + '\n')
            else:
                res = checkpoint_null_v42(M, leptr, refs_tr, spec,
                                          origin_roles)
                row = {'trace_id': tid, 'fam': c['fam'],
                       'lep_code': c['lep_code'], 'stratum': c['stratum'],
                       'reference_set': refs,
                       'origin_roles': sorted(origin_roles),
                       'delivery_verified':
                       drow.get('delivery_verified'),
                       'm3_reach_class': drow.get('m3_reach_class'),
                       'm4_class': drow.get('m4_class')}
                if res is None:
                    row['error'] = 'no_usable_checkpoint'
                    row['usable'] = []
                else:
                    row.update(res)
                fh.write(json.dumps(row) + '\n')
            del leptr, refs_tr
            gc.collect()
            n_done += 1
            if n_done % 500 == 0:
                print(f'[ckpt43] {n_done}/{len(cells)}', flush=True)
    print(f'[ckpt43] done {n_done} cells -> {out_path}', flush=True)


if __name__ == '__main__':
    if 'PYTHONHASHSEED' not in os.environ:
        os.environ['PYTHONHASHSEED'] = '0'
        os.execve(sys.executable, [sys.executable] + sys.argv,
                  os.environ)
    main()
