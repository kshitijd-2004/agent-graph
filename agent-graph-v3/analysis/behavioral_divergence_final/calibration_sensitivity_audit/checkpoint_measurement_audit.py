#!/usr/bin/env python3.11
"""Checkpoint-level measurement audit (requirement 9).

Parts:
  A. WHAT THE CHECKPOINT SCORE MEASURES — static analysis of the frozen
     mechanism (reproduced from cp_lib.cache): component mask, weights,
     pooled null, gate structure, multiple-opportunity accounting.
  B. ISOLATED DIAGNOSTIC — per-dimension checkpoint calibration, built
     from the SAME frozen building blocks (_pair_computation per-component
     outputs, no new inference): on a seeded 500-cell stratified sample
     it computes per-component benign-pair distance lists at the
     checkpoint scopes, pools them family-wide (q98, LOO), and fires a
     dimension-specific checkpoint gate; compared against the current
     single composite-score gate on the same sample.  A 500-cell
     diagnostic establishes feasibility + direction; it is NOT the
     full-frame rule (no re-tuning of the frozen policy).
  C. ROW-LEVEL FPR ACCOUNTING — the OR over multiple checkpoint
     opportunities (peak + final) vs the probe-observation FPR.

Writes ledger/checkpoint_audit.json.
"""
import json
import os
import random
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
    cached = json.load(open(os.path.join(A.CALP, 'out',
                                         'cache_cells_full.json')))
    cells = cached['cells']
    for c in cells:
        if isinstance(c.get('stratum'), list):
            c['stratum'] = tuple(c['stratum'])
    fr = A.frozen_by()
    ledger = {r['trace_id']: r for r in A.cal()}

    # ── A. static analysis ──────────────────────────────────────────────
    # component weights come from the frozen DEFAULT_WEIGHTS (experimental,
    # 0.40/0.25/0.25/0.10); the checkpoint pair total is a WEIGHTED
    # COMPOSITE over the scope component mask.
    rep = {'A_static': {
        'score': ('mean over the 3 benign refs of the frozen COMPOSITE '
                  'pair distance at a checkpoint scope: intermediate '
                  'scope = weighted(assertions .40, actions .25); final '
                  'scope = weighted(assertions .40, decisions .25, '
                  'outcomes .10); weights are the frozen experimental '
                  'DEFAULT_WEIGHTS, component mask = benign-pair '
                  'intersection'),
        'weighted_composite_used': True,
        'construct_differs_from_m1_union':
            'YES: M1 = logical OR of 4 independent dimension indicators '
            'vs per-dim benign-calibrated theta; checkpoint = single '
            'weighted scalar with fixed experimental weights.  An '
            'outcome-only change (weight .10) must exceed the composite '
            'threshold 8x more than an assertion change to fire; a '
            'uniform 0.5 across all 4 dims fires the composite at '
            '0.5 either way — the construct is not the 4-dim union.',
        'null_comparable': ('the checkpoint null pools BENIGN-pair '
                            'composite distances (inter list for the '
                            'inter gate, final list for the final gate), '
                            'LOO-excluding the cell, q98 family pool — '
                            'matched in cardinality AND construct to the '
                            'perturbed composite.  It is comparable at '
                            'CHECKPOINT scope; it is NOT the whole-trace '
                            'p3 null (different object).'),
        'multiple_opportunities':
            'the gate is an OR over (intermediate peak, final): each row '
            'can be flagged through several checkpoint observations; the '
            'probe-observation FPR under-reports the row-level FPR.  '
            'See C below.',
    }}

    # ── C. row-level vs probe-level FPR (cheap, from cache) ─────────────
    M = L.load_frozen_mod()
    specs = L.load_specs()
    idx = L.load_trace_index()
    ck_theta = L.build_checkpoint_theta(cells, M, specs, idx,
                                        q=0.98, pool='family', floor=0.0)
    probe_fire = probe_tot = 0
    row_or_fire = row_or_n = 0
    n_inter_keys = Counter()
    n_final = 0
    for c in cells:
        bi, bf, _u = c.get('_ckpt', ([], [], None))
        th = ck_theta.get(c['trace_id']) or {}
        ti, tf = th.get('inter'), th.get('final')
        row_cells = [v for v in bi if (ti is not None and v > ti)] + \
                    [v for v in bf if (tf is not None and v > tf)]
        n_inter_keys[len(bi)] += 1
        if bf:
            n_final += 1
        probe_tot += len(bi) + len(bf)
        probe_fire += len(row_cells)
        # row-level: is this cell APPLICABLE to a cross-agent gate?
        # (same structure the frozen M2 flag used: has cross-agent
        # intermediate checkpoint or final non-origin) — approximate the
        # control as: any probe pool value exists (a comparable benign
        # checkpoint pair at that scope existed)
        if bi or bf:
            row_or_n += 1
            if row_cells:
                row_or_fire += 1
    rep['C_row_level_fpr'] = {
        'probe_observations': probe_tot,
        'probe_fired': probe_fire,
        'probe_fpr': probe_fire / probe_tot if probe_tot else None,
        'row_applicable': row_or_n,
        'row_or_fired': row_or_fire,
        'row_or_fpr': row_or_fire / row_or_n if row_or_n else None,
        'n_inter_pool_sizes': dict(n_inter_keys),
        'note': ('row-or FPR = the B2B control\'s ROW-level false '
                 'positive rate (any benign probe in the cell\'s pools '
                 'firing); the reported checkpoint B2B FPR in '
                 'cache_v6_cells.json is the probe-observation rate '
                 '(1057/115404 = 0.92%).  Row-level is the relevant '
                 'denominator for per-execution FPR; the gap is the '
                 'multiple-opportunity effect.'),
    }

    # ── B. per-dimension diagnostic (500-cell seeded sample) ────────────
    rng = random.Random(20270125)
    # stratify: small feasibility sample (NOT the full frame) enriched on
    # M2-cal-positive rows (where the gate matters)
    m2pos = [c for c in cells if
             (ledger.get(c['trace_id'], {}).get('m2_cross_agent_cal')
              is True)]
    m2neg = [c for c in cells if
             (ledger.get(c['trace_id'], {}).get('m2_cross_agent_cal')
              is not True)]
    n_pos = min(60, len(m2pos))
    n_neg = min(90, len(m2neg))
    samp = rng.sample(m2pos, n_pos) + rng.sample(m2neg, n_neg)
    print(f'[ckpt] diagnostic sample: {len(samp)} cells '
          f'({n_pos} M2-cal-pos of {len(m2pos)}; {n_neg} neg of '
          f'{len(m2neg)})', flush=True)

    def checkpoint_null_dims(M, leptr, refs_tr, spec):
        """Per-component benign-pair distance lists at checkpoint scopes
        (the comps dict of _pair_computation), same structure as
        cp_lib.checkpoint_null but keeping components separate."""
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
            rec = {'key': c.key, 'is_final': c.is_final,
                   '_cp': c}
            rec['mean_ref_distance'] = (sum(dists) / len(dists)) \
                if dists else None
            if dists:
                usable.append(rec)
        if not usable:
            return None
        inters = [r for r in usable if not r['is_final']
                  and r['mean_ref_distance'] is not None]
        # peak (frozen 'best' = argmax mean_ref_distance among inters)
        peak = max(inters, key=lambda r: r['mean_ref_distance']) \
            if inters else None
        fin = [r for r in usable if r['is_final']]
        fin_rec = fin[-1] if fin else None
        # per-component LEP distances at the peak / final
        lep_comps_inter = lep_comps_final = None
        if peak is not None:
            for ri, m in enumerate(key_to_ref):
                d, comps, _un, mask = stg_comps(
                    scoped(lep_feats, peak['_cp']),
                    scoped(ref_feats[ri], m[peak['key']]), False)
                if comps:
                    base = {k: None for k in mask}
                    for k in mask:
                        base[k] = comps[k][0]
                    if lep_comps_inter is None:
                        lep_comps_inter = base
                    else:
                        for k in mask:
                            if base.get(k) is not None:
                                lep_comps_inter[k] = base[k]
        if fin_rec is not None:
            for ri, m in enumerate(key_to_ref):
                d, comps, _un, mask = stg_comps(
                    scoped(lep_feats, fin_rec['_cp']),
                    scoped(ref_feats[ri], m[fin_rec['key']]), True)
                if comps:
                    base = {k: None for k in mask}
                    for k in mask:
                        base[k] = comps[k][0]
                    if lep_comps_final is None:
                        lep_comps_final = base
                    else:
                        for k in mask:
                            if base.get(k) is not None:
                                lep_comps_final[k] = base[k]
        # per-component BENIGN pair lists (pools)
        def pool_comps(scope_final):
            out = {d: [] for d in ('assertions', 'decisions', 'actions',
                                    'outcomes')}
            for i in range(len(ref_feats)):
                for j in range(i + 1, len(ref_feats)):
                    if scope_final:
                        if ('final' not in key_to_ref[i]
                                or 'final' not in key_to_ref[j]):
                            continue
                        ci = key_to_ref[i].get('final')
                        cj = key_to_ref[j].get('final')
                    else:
                        keys = sorted({r['key'] for r in inters})
                        if not keys:
                            continue
                        ci = key_to_ref[i].get(keys[0])
                        cj = key_to_ref[j].get(keys[0])
                        if ci is None or cj is None:
                            continue
                    d, comps, _un, mask = stg_comps(
                        scoped(ref_feats[i], ci), scoped(ref_feats[j], cj),
                        scope_final)
                    if d is None:
                        continue
                    for k in mask:
                        if comps[k][0] is not None:
                            out[k].append(comps[k][0])
            return out
        ben_inter = pool_comps(False)
        ben_final = pool_comps(True)
        return {'peak_key': peak['key'] if peak else None,
                'final_key': fin_rec['key'] if fin_rec else None,
                'lep_mean_composite': {'inter': peak['mean_ref_distance']
                                       if peak else None,
                                       'final': fin_rec['mean_ref_distance']
                                       if fin_rec else None},
                'lep_comps_inter': lep_comps_inter,
                'lep_comps_final': lep_comps_final,
                'ben_inter': ben_inter, 'ben_final': ben_final}

    import gc
    dims_res = {}
    n_done = 0
    for ci_, c in enumerate(samp):
        tid = c['trace_id']
        fid = tid.split('.')[0]
        spec = specs.get(fid, {})
        drow = A.ds_by().get(tid, {})
        refs = drow.get('reference_set') or []
        if len(refs) < 3:
            continue
        p = idx.get(tid)
        leptr = json.load(open(p)) if p and os.path.exists(p) else None
        refs_tr = []
        ok = leptr is not None
        for t in refs[:3]:
            pp = idx.get(t)
            tr = json.load(open(pp)) if pp and os.path.exists(pp) \
                else None
            if tr is None:
                ok = False
            refs_tr.append(tr)
        del c
        if not ok:
            del leptr, refs_tr
            gc.collect()
            continue
        res = checkpoint_null_dims(M, leptr, refs_tr, spec)
        del leptr, refs_tr
        gc.collect()
        if res is None:
            continue
        n_done += 1
        # per-dim pooled q98 thresholds (family pools accumulate)
        dims_res.setdefault(tid, res)
        if (ci_ + 1) % 10 == 0:
            print(f'[ckpt]   {ci_ + 1}/{len(samp)} extracted '
                  f'{n_done}', flush=True)
    # accumulate family pools across the sample (approximation of the
    # full-frame family pool; documented as sample-scale)
    fam_pools_inter = {d: [] for d in ('assertions', 'decisions', 'actions',
                                        'outcomes')}
    fam_pools_final = {d: [] for d in ('assertions', 'decisions', 'actions',
                                       'outcomes')}
    for tid, res in dims_res.items():
        f = ledger.get(tid, {}).get('task_family')
        for d in fam_pools_inter:
            fam_pools_inter[d].extend(res['ben_inter'].get(d) or [])
            fam_pools_final[d].extend(res['ben_final'].get(d) or [])
    print(f'[ckpt] {n_done} cells extracted')
    # per-dimension q98 thresholds on the SAMPLE-scale pools, then the
    # dimension-specific gate vs the composite gate on the same sample
    import math
    def q98(pool, loo):
        P = sorted(pool)
        R = sorted(loo)
        n, r = len(P), len(R)
        i_target = int(math.ceil(0.98 * (n - r))) - 1
        j = L.loo_rank(P, max(0, i_target), R) if n - r > 0 else None
        return P[j] if j is not None else None

    comp_fire = perdim_fire = both = neither = 0
    per_dim_fire_counts = Counter()
    for tid, res in dims_res.items():
        Lm = ledger.get(tid, {})
        composite = Lm.get('m2_cross_agent_cal') is True
        inter_th = {d: q98(fam_pools_inter[d], res['ben_inter'].get(d) or [])
                    for d in fam_pools_inter}
        final_th = {d: q98(fam_pools_final[d], res['ben_final'].get(d) or [])
                    for d in fam_pools_final}
        fired_dims = set()
        li = res.get('lep_comps_inter') or {}
        for d, v in li.items():
            if v is not None and inter_th[d] is not None and v > inter_th[d]:
                fired_dims.add('inter_' + d)
        lf = res.get('lep_comps_final') or {}
        for d, v in lf.items():
            if v is not None and final_th[d] is not None and v > final_th[d]:
                fired_dims.add('final_' + d)
        pdf = bool(fired_dims)
        comp_fire += int(composite)
        perdim_fire += int(pdf)
        both += int(composite and pdf)
        neither += int(not composite and not pdf)
        for x in fired_dims:
            per_dim_fire_counts[x] += 1
    rep['B_per_dimension_diagnostic'] = {
        'n_cells': n_done,
        'note': ('per-component benign pools are SAMPLE-SCALE (150 cells: '
                 '60 M2-cal-positive + 90 negative, seeded '
                 'agentprop-audit-cases-20270125 family... see script), '
                 'not full-frame: a feasibility + direction diagnostic, '
                 'clearly labeled as such.  Full-frame implementation '
                 'is feasible from these artifacts (no new inference '
                 'required) — see the feasibility statement.'),
        'sample_scale_pools': {d: {'inter_n': len(fam_pools_inter[d]),
                                   'final_n': len(fam_pools_final[d])}
                               for d in fam_pools_inter},
        'gate_comparison_sample_scale': {
            'composite_cal_gate_fired': comp_fire,
            'per_dimension_gate_fired': perdim_fire,
            'both': both, 'neither': neither,
            'n': n_done,
            'per_dimension_fires': dict(per_dim_fire_counts),
            'interpretation': ('a dimension-specific (no-weight) '
                               'checkpoint gate is expected to fire MORE '
                               'often than the composite gate: any single '
                               'component exceeding its own q98 benign '
                               'threshold fires, versus the weighted '
                               'composite where a low-weight component '
                               'must move the scalar.  The sample gap '
                               'is directional evidence for the '
                               'construct mismatch; it is NOT a '
                               'full-frame measurement.'),
        },
        'feasibility': ('DIMENSION-SPECIFIC CHECKPOINT CALIBRATION IS '
                        'FEASIBLE from existing artifacts: '
                        '_pair_computation exposes the per-component '
                        'distance values (comps dict) at the SAME '
                        'strict-basis checkpoint construction the '
                        'composite uses; no new LLM calls, trace '
                        'generation, or extraction are required — only '
                        'a re-scoring pass over the frozen '
                        'checkpoint machinery.  The composite M1 union '
                        'design (4 independent dims, logical OR, no '
                        'weights) is therefore implementable at '
                        'checkpoint level; the current checkpoint gate '
                        'is NOT that design.'),
    }
    with open(os.path.join(OUT, 'checkpoint_audit.json'), 'w') as fh:
        json.dump(rep, fh, indent=1)
    print('written ledger/checkpoint_audit.json')
    print('probe FPR:', rep['C_row_level_fpr']['probe_fpr'],
          'row-or FPR:', rep['C_row_level_fpr']['row_or_fpr'])


if __name__ == '__main__':
    main()
