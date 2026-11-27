#!/usr/bin/env python3.11
"""V4.4 downstream (M2) checkpoint recompute — corrected evidence
localization + recorded key-level evidence (Astra §4).

Port of calibration_v2/scripts/compute_checkpoint_v43.py onto the V4.4
extractor (benchmark/baseline_deviation_v44.py) with these corrections:

  (A) FINAL CHECKPOINT ACTION SCOPE: the final checkpoint's SCORED
      action counter is the FINAL AUTHOR'S OWN calls (the role that
      emitted the final response) — symmetric on both sides: at a
      matched 'final' key the LEP author and every benign author run
      the SAME construction over their own same-named role, so the
      statistic is identical for target and control (a cross-agent
      LEP-only origin trim would break that symmetry, because matched
      benign refs record no origins).  The unscoped whole-trace
      counter is recorded alongside (``actions_unscoped``), NEVER
      scored.  Each usable final entry records
      ``action_scope_roles`` / ``action_excluded_origin_roles`` so a
      firing is attributable to named roles — the 98/41 "final
      actions are the only firing evidence" population is
      re-attributed, and other roles' calls remain their OWN
      intermediate-checkpoint evidence (never double-counted at the
      final key).

  (B) FINAL OUTCOME/DECISION PROVENANCE: final-scope files are the
      cross-scope authorship sets (files written by non-origin roles);
      decisions at the final checkpoint are the final author's own
      re-extraction; the whole-trace feature sets remain on M1, not on
      the downstream checkpoints.

  (C) RECORDED INTEGER EVIDENCE (Astra §1 count-reconstruction repair):
      each usable entry records, per dimension, the per-ref LEP
      distance LIST (``comp_dist``), the recorded INTEGER disagree
      count (``comp_k`` = # per-ref distances > 0) and the sparse
      action object (``comp_sparse``) — the discrete gate calibrates on
      these recorded counts against the LEP-free family pool on its
      actual support.  round(mean * n) reconstruction is NOT used
      anywhere in V4.4.

  (D) FIXTURE TAGGING: each row records its fixture id so the
      production gate can build fixture-disjoint threshold pools
      (sibling LEP rows share a reference set; cell-level LOO leaked).

All writes under calibration_v3/out_v44/.  Deterministic (seeded), no
LLM calls, frozen V4.2/V4.3 artifacts untouched.  Consumes the
downstream reach/delivery/origin fields from the frozen consolidated
file (read-only join, as in V4.3).
"""
import gc
import json
import os
import sys
from collections import defaultdict
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
FINAL = os.path.abspath(os.path.join(HERE, '..'))
REPO = os.path.abspath(os.path.join(FINAL, '..', '..'))
CALP = os.path.join(FINAL, 'calibrated_production')
OUT = os.path.join(HERE, 'out_v44')

sys.path.insert(0, os.path.join(FINAL, 'calibrated_production'))
import cp_lib as L  # noqa: E402  (trace index + downstream join only)
sys.path.insert(0, FINAL)
import common  # noqa: E402
sys.path.insert(0, REPO)
import importlib.util as _ilu

DIMS = common.DIMS


def load_v44_mod():
    if 'v44_mod' in sys.modules:
        return sys.modules['v44_mod']
    spec = _ilu.spec_from_file_location(
        'v44_mod', os.path.join(REPO, 'benchmark',
                                 'baseline_deviation_v44.py'))
    m = _ilu.module_from_spec(spec)
    sys.modules['v44_mod'] = m
    spec.loader.exec_module(m)
    return m


def _comps_of(M, fa, fb, fam, W, schema, is_final):
    """Per-dimension component distances on the V4.4 schema mask; each dim
    independently evaluable; the weighted total is context ONLY."""
    comp_mask = (schema.final_components if is_final
                 else schema.intermediate_components)
    _total, comps, _unav = M._pair_computation(
        fa, fb, fam, W, comp_mask, schema.stage_assertion_keys,
        is_final, is_final, strict_basis=True,
        decision_key_mask=schema.decision_required_keys)
    return comps, comp_mask


def checkpoint_null_v44(M, leptr, refs_tr, spec, origin_roles):
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
        # per-dimension LEP distances at THIS key (per-ref lists, so the
        # recorded integer disagree count is recoverable downstream)
        per_dim_lep: Dict[str, List[float]] = defaultdict(list)
        sparse: Dict[str, Any] = {}
        for ri, m in enumerate(key_to_ref):
            rc = m.get(c.key)
            comps, _mask = _comps_of(
                M, fa, scoped(ref_feats[ri], rc), fam, W, schema,
                c.is_final)
            for k in DIMS:
                v = (comps.get(k) or (None,))[0]
                if v is not None:
                    per_dim_lep[k].append(v)
            if 'actions' in sparse:
                continue
            sp = (comps.get('actions') or (None, {'basis': 'none'}))[1]
            sparse = sp.get('action_sparse') or {}
        comp_mean = {k: (sum(v) / len(v)) for k, v in per_dim_lep.items()}
        comp_n = {k: len(v) for k, v in per_dim_lep.items()}
        comp_dist = {k: v for k, v in per_dim_lep.items()}
        comp_k = {k: sum(1 for x in v if x > 0)
                  for k, v in per_dim_lep.items()}
        if not comp_mean:
            continue  # nothing independently evaluable -> not usable
        # benign-pair pool at THIS key + scope (corresponding refs),
        # fixture-tagged at the row level (D)
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
            # (A)/(B): provenance record for this checkpoint's action
            # evidence (identical construction on the benign side)
            'action_scope_roles': (list(c.action_scope_roles)
                                   if c.is_final else [c.role]),
            'action_excluded_origin_roles': list(
                c.action_excluded_origin_roles),
            'n_ref_pairs': len(key_to_ref),
            'comp_mean': comp_mean,
            'comp_n': comp_n,
            # (C): recorded key-level evidence (no reconstruction later)
            'comp_dist': comp_dist,
            'comp_k': comp_k,
            'action_sparse': sparse,
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
    M = load_v44_mod()
    specs = L.load_specs()
    idx = L.load_trace_index()
    cells = json.load(open(os.path.join(CALP, 'out',
                                        'cache_cells_full.json')))['cells']
    ds_by = {json.loads(l)['trace_id']: json.loads(l)
             for l in open(L.DOWNSTREAM) if l.strip()}

    out_path = os.path.join(OUT, 'checkpoint_pairs_v44.jsonl')
    done: set = set()
    if os.path.exists(out_path):
        for l in open(out_path):
            try:
                done.add(json.loads(l)['trace_id'])
            except Exception:
                continue
        print(f'[ckpt44] resuming: {len(done)} cells already scored',
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
                res = checkpoint_null_v44(M, leptr, refs_tr, spec,
                                          origin_roles)
                row = {'trace_id': tid, 'fam': c['fam'],
                       'fixture': fid,
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
                print(f'[ckpt44] {n_done}/{len(cells)}', flush=True)
    print(f'[ckpt44] done {n_done} cells -> {out_path}', flush=True)


if __name__ == '__main__':
    if 'PYTHONHASHSEED' not in os.environ:
        os.environ['PYTHONHASHSEED'] = '0'
        os.execve(sys.executable, [sys.executable] + sys.argv,
                  os.environ)
    main()
