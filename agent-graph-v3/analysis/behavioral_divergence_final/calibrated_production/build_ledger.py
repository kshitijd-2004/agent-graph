#!/usr/bin/env python3.11
"""
build_ledger.py — the ONE writer for calibrated_production.  Builds the
per-execution production ledger (both operating rules) + caches the heavy
extraction so the metric/report scripts are fast and re-derivable.

Writes ONLY into calibrated_production/out/:
  out/ledger_v6_calibrated.jsonl   — per-execution ledger (original + calibrated)
  out/cache_v6_cells.json          — v6 whole-trace cells (T_lep/T_p2/T_p3) + rule thr
  out/cache_ckpt_null.json         — checkpoint-level p3 null pools + checkpoint theta
  out/metrics_core.json            — the re-derived M1/M2/M3/M4 headline numbers

Determinism: re-execs under PYTHONHASHSEED=0 when unpinned (the v6 TV kernel
is float-order sensitive; FROZEN_PROTOCOL §6).  Run:
  PYTHONHASHSEED=0 python3.11 build_ledger.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import cp_lib as L                     # noqa: E402

OUT = os.path.join(_HERE, 'out')
os.makedirs(OUT, exist_ok=True)


def _w(p: str, obj: Any) -> str:
    with open(os.path.join(OUT, p), 'w') as fh:
        json.dump(obj, fh, indent=1)
    return os.path.join(OUT, p)


def _w_jsonl(p: str, rows: List[dict]) -> str:
    """One JSON object per line (the ledger is read back line-by-line)."""
    path = os.path.join(OUT, p)
    with open(path, 'w') as fh:
        for r in rows:
            fh.write(json.dumps(r, default=str) + '\n')
    return path


def _cache_fingerprint(specs, idx, ds_rows, v6) -> str:
    """Deterministic key: the inputs that feed the two EXPENSIVE phases
    (v6 extraction -> cells, checkpoint nulls -> _ckpt).  If unchanged, the
    cached cells (with _ckpt) are reused and both ~18-min phases are skipped;
    the cheap phases (theta tables, scoring, ledger assembly, re-derivation)
    always re-run.  The cache is a RE-DERIVABLE artifact, not a source of
    truth: deleting out/cache_cells_full.json forces a full rebuild."""
    import hashlib

    def sha(p):
        try:
            h = hashlib.sha256()
            with open(p, 'rb') as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b''):
                    h.update(chunk)
            return h.hexdigest()
        except Exception:
            return 'MISSING'

    base = os.path.dirname(L.REPO)
    src_v6 = os.path.join(L.common.V6_DIR)
    parts = [
        # input ledgers / index / specs (the extraction inputs)
        sha(L.V6_LEDGER), sha(L.DOWNSTREAM), sha(L.TRACE_INDEX),
        sha(L.FROZEN),
        # the v6 kernel source (distances + pipeline + features)
        sha(os.path.join(src_v6, 'distances.py')),
        sha(os.path.join(src_v6, 'run_pipeline.py')),
        sha(os.path.join(src_v6, 'features_v6.py')),
        sha(os.path.join(src_v6, 'union.py')),
        sha(os.path.join(src_v6, 'graders.py')),
        # the frozen checkpoint machinery
        sha(L.FROZEN_RUN + '/benchmark/baseline_deviation_v41.py'),
        # the checkpoint-null recompute source
        sha(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'cp_lib.py')),
    ]
    spec_blob = hashlib.sha256(json.dumps(
        {k: v.get('sha256') for k, v in specs.items()},
        sort_keys=True).encode()).hexdigest()
    rule = json.dumps(L.RULE, sort_keys=True)
    return hashlib.sha256(
        (':'.join(parts) + '|' + spec_blob + '|' + rule).encode()).hexdigest()


def main() -> None:
    t0 = time.time()
    specs = L.load_specs()
    idx = L.load_trace_index()
    ds_rows = L._load_jsonl(L.DOWNSTREAM)      # 16773 frame rows
    ds = L.load_downstream()
    fr = L.load_frozen()
    v6 = L.load_v6()
    v6_by = {r['trace_id']: r for r in v6}

    print(f'[ledger] frame rows {len(ds_rows)}; v6 ledger {len(v6)}; '
          f'frozen {len(fr)}', flush=True)

    CACHE = os.path.join(OUT, 'cache_cells_full.json')
    fp = _cache_fingerprint(specs, idx, ds_rows, v6)

    cells = None
    ck_theta = None
    if os.path.exists(CACHE):
        try:
            _c = json.load(open(CACHE))
            if _c.get('fingerprint') == fp:
                cells = _c['cells']
                print(f'[ledger] loading cached cells ({len(cells)}); '
                      f'fingerprint match — skipping the two extraction '
                      f'phases', flush=True)
        except Exception:
            cells = None

    if cells is None:
        # ── v6 whole-trace cells (the calibrated M1 kernel) ─────────────
        dist = L.v6_dist_factory()
        cells = L.build_v6_cells(ds_rows, specs, idx, dist)
        print(f'[ledger] v6 cells {len(cells)} in {time.time() - t0:.0f}s',
              flush=True)
        thr = L.build_theta_table(cells, null_field='T_p3', q=0.98,
                                  pool='family', floor=0.0)
        st_l, st_b, per_trace = L.score_calibrated(cells, thr, floor=0.0)
        print(f'[ledger] M1-cal LEP {st_l["u"]}/{st_l["n"]} '
              f'(zero-only {st_l["zero_only"]}); B2B {st_b["u"]}/{st_b["n"]}',
              flush=True)

        # ── checkpoint-level p3 null (the calibrated M2 gate) ───────────
        # Load traces FRESH per cell (no raw-trace cache) so memory stays
        # bounded: a raw trace is ~235KB and there are ~25k distinct;
        # caching them all is ~6GB and OOM-kills.  Each cell's checkpoint
        # null only needs that cell's 4 traces, processed sequentially.
        M = L.load_frozen_mod()
        _t1 = time.time()

        def ftr_fresh(tid):
            p = idx.get(tid)
            return json.load(open(p)) if p and os.path.exists(p) else None

        for ci, c in enumerate(cells):
            r = ds.get(c['trace_id'], {})
            fid = c['trace_id'].split('.')[0]
            spec = specs.get(fid, {})
            refs = r.get('reference_set') or []
            leptr = ftr_fresh(c['trace_id'])
            refs_tr = [ftr_fresh(t) for t in refs]
            if leptr is None or any(x is None for x in refs_tr) \
                    or len(refs) < 3:
                c['_ckpt'] = ([], [], [])
                continue
            c['_ckpt'] = L.checkpoint_null(M, leptr, refs_tr, spec)
            del leptr, refs_tr   # bound memory to one cell
            if (ci + 1) % 2000 == 0:
                print(f'[ledger]   checkpoint nulls {ci + 1}/{len(cells)} '
                      f'({time.time() - _t1:.0f}s)', flush=True)
        print(f'[ledger] checkpoint nulls attached in '
              f'{time.time() - _t1:.0f}s', flush=True)
        # write the cache (cells carry T_* + _ckpt; the cheap phases below
        # recompute theta/scoring/ledger from it on every run)
        _w('cache_cells_full.json', {'fingerprint': fp, 'cells': cells})
        print('[ledger] wrote cache_cells_full.json', flush=True)
    else:
        # recompute the cheap per-cell theta table + scoring (NOT cached:
        # they are pure functions of cells, re-derived every run)
        thr = L.build_theta_table(cells, null_field='T_p3', q=0.98,
                                  pool='family', floor=0.0)
        st_l, st_b, per_trace = L.score_calibrated(cells, thr, floor=0.0)
        print(f'[ledger] M1-cal LEP {st_l["u"]}/{st_l["n"]} '
              f'(zero-only {st_l["zero_only"]}); B2B {st_b["u"]}/{st_b["n"]}',
              flush=True)

    ck_theta = L.build_checkpoint_theta(cells, L.load_frozen_mod(), specs,
                                        idx, q=0.98, pool='family',
                                        floor=0.0)

    # ── build the per-execution ledger (original + calibrated) ──────────────
    ledger: List[Dict[str, Any]] = []
    for r in ds_rows:
        tid = r['trace_id']
        f = fr.get(tid, {})
        st = f.get('stage') or {}
        v6r = v6_by.get(tid, {})
        pd6 = v6r.get('per_dimension') or {}
        u6 = v6r.get('union') or {}
        pt = per_trace.get(tid, {})
        dims_cal = pt.get('dims', {})
        ledger.append({
            # identity
            'trace_id': tid,
            'fixture_id': tid.split('.')[0],
            'task_family': r.get('task_family'),
            'lep_code': r.get('lep_code'),
            'topology': r.get('topology'),
            'propagation_mode': r.get('propagation_mode'),
            'reference_set': r.get('reference_set') or [],
            # delivery / evaluable
            'delivery_verified': r.get('delivery_verified'),
            'downstream_evaluable': r.get('evaluable'),
            'origin_roles': r.get('origin_roles'),
            # ── M1 ORIGINAL (frozen per-cell max theta, v6 ledger) ─────────
            'm1_union_orig': bool(u6.get('divergent')),
            'm1_dims_orig': {d: bool((pd6.get(d) or {}).get('divergent'))
                             for d in L.DIMS},
            'm1_dim_eval_orig': {d: bool((pd6.get(d) or {})
                                         .get('evaluable')) for d in L.DIMS},
            'm1_triggered_orig': list(u6.get('triggered_dimensions') or []),
            # zero-threshold-ONLY positive: the row is union-POSITIVE and
            # EVERY triggered dim rested on a zero θ (negative rows have
            # no triggered dims and are never counted here).
            'm1_zero_only_orig': (
                bool(u6.get('divergent')) and
                len(u6.get('zero_threshold_triggered') or []) ==
                len(u6.get('triggered_dimensions') or [])),
            # ── M1 CALIBRATED (p3_q98_f0_family) ───────────────────────────
            # m1_cal_state: 'pos' / 'neg' (determined) / 'unavail' (every
            # calibrated dim indicator undetermined — no evaluable T_d, or
            # evaluable T_d but no pooled θ).  'unavail' is NEVER a negative.
            'm1_union_cal': bool(pt.get('m1_cal')),
            'm1_cal_state': ('pos' if pt.get('m1_cal') else
                             ('neg' if (pt.get('n_eval') or 0) > 0
                              else 'unavail')),
            'm1_dims_cal': dims_cal,
            'm1_dim_eval_cal': {d: bool(pt.get('dist', {}).get(d) is not None
                                        and pt.get('thr', {}).get(d) is not None)
                                for d in L.DIMS},
            'm1_triggered_cal': list(pt.get('triggered') or []),
            'm1_zero_only_cal': bool(pt.get('zero_only')),
            'm1_dist_cal': pt.get('dist'),
            'm1_thr_cal': pt.get('thr'),
            'm1_cal_reason': pt.get('reason'),
            # ── M2 (downstream deviation; original frozen gate) ────────────
            'm2_cross_agent': bool(r.get('m2_cross_agent_positive')),
            'm2_scope': r.get('m2_scope'),
            'm2_nonzero_threshold': bool(r.get('m2_nonzero_threshold')),
            'm2_legacy_positive': bool(r.get('m2_legacy_positive')),
            # checkpoint distances (frozen, composite deviation score)
            'ckpt_d_peak': st.get('d_peak'),
            'ckpt_d_final': st.get('d_final_output'),
            'ckpt_stage_threshold_frozen': st.get('stage_threshold'),
            'ckpt_final_threshold_frozen': st.get('final_threshold'),
            'ckpt_persistent_final_frozen': st.get('persistent_final_deviation'),
            # checkpoint-calibrated threshold + gate
            'ckpt_theta_cal': ck_theta.get(tid),
            # relevant checkpoint identifiers (the frozen stage block's
            # cross-agent structure: which checkpoint keys carry the M2 gate)
            'm2_ckpt_keys': [c.get('key') for c in (st.get('checkpoints') or [])
                             if not c.get('is_final') and c.get('available')
                             and not c.get('is_origin')
                             and c.get('after_boundary')]
            + [c.get('key') for c in (st.get('checkpoints') or [])
               if c.get('is_final') and c.get('available')],
            'm2_cross_agent_cal': None,   # filled below
            'm2_cal_reason': None,
            # calibration rule identifier + provenance (both operating rules)
            'calibration_rule': 'p3_q98_f0_family',
            'calibration_provenance': (
                'original: frozen per-cell max theta (v6 ledger, byte-sealed; '
                'out/production_summary_v6.json). calibrated: dev-selected '
                'G5 rule p3_q98_f0_family (p3 exchangeable null, q=0.98, '
                'family pool, floor 0.0, exact leave-one-cell-out multiset '
                'quantile; selected on DEV out/calib_experiment_dev_v6.json, '
                'confirmed on heldout out/calib_experiment_heldout_v6.json '
                'exactly once; re-run here on the full P1 frame).'),
            # ── M3 (reach evidence classification; the frozen downstream
            #    frame fields, injection-boundary-gated + role-gated) ──────
            'm3_reach_class': r.get('m3_reach_class'),
            'm3_detail': r.get('m3_detail', ''),
            # ── M4 / M3-matched degradation (v6 five-way class) ────────────
            'm4_class': v6r.get('m4_class'),
            'm4_matched': bool(v6r.get('m4_matched')),
            'm4_detail': v6r.get('m4_detail'),
            'lep_grade': v6r.get('lep_grade'),
            'benign_grades': v6r.get('benign_grades'),
            'm4_historical_frozen': r.get('m4_task_failure'),
        })

    # fill the checkpoint-calibrated M2 gate (inter OR final), reusing the
    # frozen stage block's own peak/final availability + role gating.
    # Each sub-gate returns one of:
    #   False  — structurally inapplicable (no cross-agent structure: e.g.
    #            all intermediate checkpoints are origin roles, or the final
    #            role is an origin role) -> the cross-agent metric does not
    #            apply, counted negative exactly as the frozen m2_cross_agent
    #            flag treats it;
    #   None   — applicable (a cross-agent checkpoint exists) but no pooled
    #            benchmark threshold could be formed -> UNDETERMINED, never a
    #            forced negative;
    #   bool   — evaluated against the calibrated checkpoint threshold.
    # The row-level gate = OR over sub-gates; a None sub-gate makes the row
    # undetermined (a cross-agent deviation cannot be ruled out).
    for row in ledger:
        tid = row['trace_id']
        f = fr.get(tid, {})
        st = f.get('stage') or {}
        th = ck_theta.get(tid) or {}
        cps = st.get('checkpoints') or []
        origin_roles = set(row.get('origin_roles') or [])

        # intermediate cross-agent peak (mirror cross_agent_deviation's "best")
        best = None
        for c in cps:
            if c.get('is_final') or not c.get('available') or c.get('is_origin'):
                continue
            if (c.get('role') or '') in origin_roles:
                continue
            if not c.get('after_boundary'):
                continue
            mr = c.get('mean_ref_distance')
            if mr is None:
                continue
            if best is None or mr > best:
                best = mr
        dfin = st.get('d_final_output')
        final_role = next((c.get('role') for c in cps if c.get('is_final')),
                          None)
        final_applicable = (dfin is not None and final_role is not None
                            and final_role not in origin_roles)

        def _gate(value, thr, applicable):
            if not applicable:
                return False
            if thr is None:
                return None          # undetermined (never a forced negative)
            return value > thr

        inter_state = _gate(best, th.get('inter'), best is not None)
        final_state = _gate(dfin, th.get('final'), final_applicable)
        states = [inter_state, final_state]
        if True in states:
            row['m2_cross_agent_cal'] = True
            row['m2_cal_reason'] = ('inter' if inter_state is True
                                     else 'final')
        elif None in states:
            row['m2_cross_agent_cal'] = None
            row['m2_cal_reason'] = 'undetermined_no_ckpt_threshold'
        else:
            row['m2_cross_agent_cal'] = False
            row['m2_cal_reason'] = 'negative'

    _w_jsonl('ledger_v6_calibrated.jsonl', ledger)
    print(f'[ledger] wrote {len(ledger)} ledger rows', flush=True)

    # ── checkpoint-scope B2B FPR (the checkpoint null validated SEPARATELY,
    #    as requirement 4 demands: the checkpoint-level benign false-positive
    #    rate is its OWN null, not the whole-trace B2B FPR) ─────────────────
    # For each cell, the checkpoint p3 probe = the 3 benign↔benign
    # checkpoint-pair distances (inter-scope) OR the C(3,2)=3 final-pair
    # distances.  Score the probe statistic against the SAME cell-excluded
    # checkpoint θ; a probe "fires" if its checkpoint-scope mean exceeds
    # the θ.  Denominator = total evaluable checkpoint probes; numerator =
    # probes firing.  This is the checkpoint-level empirical FPR.
    ck_b2b_fire = 0
    ck_b2b_total = 0
    for c in cells:
        _bi, _bf, _u = c.get('_ckpt', ([], [], None))
        th = ck_theta.get(c['trace_id'], {})
        ti, tf = th.get('inter'), th.get('final')
        # intermediate-scope probes: each of the 3 benigns, its mean over
        # the OTHER 2 benigns at the same intermediate checkpoint keys
        # (the p2 checkpoint analog; the 3-way p3 4-execution null is not
        # constructible at checkpoint scope — see MEASUREMENT_METHODS §5.2)
        for v in _bi:
            ck_b2b_total += 1
            if ti is not None and v > ti:
                ck_b2b_fire += 1
        for v in _bf:
            ck_b2b_total += 1
            if tf is not None and v > tf:
                ck_b2b_fire += 1
    ck_fpr = (ck_b2b_fire / ck_b2b_total) if ck_b2b_total else None

    # cache the heavy cell structures (drop the giant T dicts' provenance)
    cache = {
        'n_cells': len(cells),
        'rule': L.RULE,
        'm1_cal_lep': [st_l['u'], st_l['n'], st_l['zero_only']],
        'm1_cal_b2b': [st_b['u'], st_b['n']],
        'm1_cal_lep_dims': st_l['dims'],
        'm1_cal_b2b_dims': st_b['dims'],
        'm1_cal_b2b_note': ('the B2B denominator 50,319 = 3 p3 probes x '
                             '16,773 cells; these are CONSTRUCTED probe '
                             'observations (repeated reference '
                             'appearances), NOT independent benign '
                             'executions'),
        'ckpt_b2b_fire': ck_b2b_fire,
        'ckpt_b2b_total': ck_b2b_total,
        'ckpt_b2b_fpr': ck_fpr,
        'ckpt_b2b_note': ('checkpoint-scope B2B FPR: each cell contributes '
                          'its |inters|-many intermediate-scope × 3 '
                          'benign↔benign checkpoint-pair distances AND '
                          '3 final-scope distances (|inters|+1 × 3 '
                          'pool values per cell) to the probe pool, '
                          'scored against the cell-excluded checkpoint θ '
                          '(same fixed policy — p3-family, q=0.98, floor '
                          '0.0, exact LOO — applied to the checkpoint-'
                          'pooled lists).  This is the SEPARATE '
                          'checkpoint-level benign FPR required by '
                          'requirement 4; it is NOT the whole-trace '
                          '50,319-probe B2B FPR (different reference-'
                          'cardinality object: 3-benign-pair checkpoint '
                          'pool vs 4-execution p3 null).'),
    }
    _w('cache_v6_cells.json', cache)

    # ── re-derive the headline metric core from the LEDGER (not the cache) ──
    core = rederive(ledger, fr, ds, st_l, st_b)
    _w('metrics_core.json', core)
    print(f'[ledger] wrote metrics_core.json in {time.time() - t0:.0f}s',
          flush=True)
    print(json.dumps({
        'm1_orig': core['m1']['union_orig'],
        'm1_cal': core['m1']['union_cal'],
        'm1_cal_b2b_fpr': [st_b['u'], st_b['n']],
        'ckpt_b2b_fpr': [ck_b2b_fire, ck_b2b_total,
                         (round(ck_fpr, 4) if ck_fpr is not None
                          else None)],
        'm2_orig': core['m2']['cross_agent'],
        'm2_cal_true': core['m2']['cross_agent_cal_true'],
        'm2_cal_undetermined': core['m2']['cross_agent_cal_undetermined'],
        'm3_supported': core['m3']['supported'],
        'm3_matched': core['m3']['matched'],
    }, indent=1))


def rederive(ledger: List[Dict[str, Any]], fr, ds, st_l, st_b
             ) -> Dict[str, Any]:
    """Re-derive every headline count from the final ledger (completeness
    criterion: independently re-derive all headline counts from the ledger).
    Also emits the M1 flip states, trigger profiles and overlaps, which the
    reports read straight from this module (not from the tables JSON), so the
    headline is a single source of truth."""
    n = len(ledger)

    def cnt(pred):
        return sum(1 for r in ledger if pred(r))

    m1 = {
        'union_orig': cnt(lambda r: r['m1_union_orig']),
        'union_cal': cnt(lambda r: r['m1_union_cal']),
        'union_cal_state': {
            'pos': cnt(lambda r: r['m1_cal_state'] == 'pos'),
            'neg': cnt(lambda r: r['m1_cal_state'] == 'neg'),
            'unavail': cnt(lambda r: r['m1_cal_state'] == 'unavail')},
        'union_orig_zero_only': cnt(lambda r: r['m1_zero_only_orig']),
        'union_cal_zero_only': cnt(lambda r: r['m1_zero_only_cal']),
        'dims_orig': {d: cnt(lambda r, _d=d: r['m1_dims_orig'][_d])
                      for d in L.DIMS},
        'dims_cal': {d: cnt(lambda r, _d=d: r['m1_dims_cal'][_d])
                     for d in L.DIMS},
        'dim_eval_orig': {d: cnt(lambda r, _d=d: r['m1_dim_eval_orig'][_d])
                          for d in L.DIMS},
        'dim_eval_cal': {d: cnt(lambda r, _d=d: r['m1_dim_eval_cal'][_d])
                         for d in L.DIMS},
        'dim_unavail_orig': {d: n - cnt(lambda r, _d=d:
                                        r['m1_dim_eval_orig'][_d])
                             for d in L.DIMS},
        'dim_unavail_cal': {d: n - cnt(lambda r, _d=d:
                                       r['m1_dim_eval_cal'][_d])
                            for d in L.DIMS},
        'b2b_fpr_num': st_b['u'], 'b2b_fpr_den': st_b['n'],
        'b2b_dims': st_b['dims'],
        'b2b_union_note': ('denominator is CONSTRUCTED p3 probe '
                           'observations (3 per cell), NOT independent '
                           'benign executions'),
        # flip reconciliation original -> calibrated (state-precise)
        'orig_pos_cal_pos': cnt(lambda r: r['m1_union_orig'] and
                                r['m1_union_cal']),
        'orig_pos_cal_neg': cnt(lambda r: r['m1_union_orig'] and
                                 r['m1_cal_state'] == 'neg'),
        'orig_pos_cal_unavail': cnt(lambda r: r['m1_union_orig'] and
                                     r['m1_cal_state'] == 'unavail'),
        'orig_neg_cal_pos': cnt(lambda r: (not r['m1_union_orig']) and
                                 r['m1_union_cal']),
        'orig_neg_cal_neg': cnt(lambda r: (not r['m1_union_orig']) and
                                 r['m1_cal_state'] == 'neg'),
        'orig_neg_cal_unavail': cnt(lambda r: (not r['m1_union_orig']) and
                                     r['m1_cal_state'] == 'unavail'),
    }

    # M1 trigger profiles + pairwise overlaps (calibrated), for the report
    trig: Dict[str, int] = {}
    overlap_pairs: Dict[str, int] = {}
    for r in ledger:
        tc = [d for d in L.DIMS if r['m1_dims_cal'][d]]
        if tc:
            trig[','.join(tc)] = trig.get(','.join(tc), 0) + 1
        for i, a in enumerate(L.DIMS):
            for b in L.DIMS[i + 1:]:
                if a in tc and b in tc:
                    overlap_pairs[f'{a}+{b}'] = \
                        overlap_pairs.get(f'{a}+{b}', 0) + 1
    m1['trigger_profiles_cal'] = trig
    m1['pairwise_overlap_cal'] = overlap_pairs

    m2 = {
        'cross_agent': cnt(lambda r: r['m2_cross_agent']),
        'cross_agent_nonzero': cnt(lambda r: r['m2_cross_agent'] and
                                   r['m2_nonzero_threshold']),
        'cross_agent_cal_true': cnt(lambda r: r['m2_cross_agent_cal']
                                    is True),
        'cross_agent_cal_false': cnt(lambda r: r['m2_cross_agent_cal']
                                     is False),
        'cross_agent_cal_undetermined': cnt(
            lambda r: r['m2_cross_agent_cal'] is None),
        'cal_reasons': {},
        # the separately-named diagnostic intersection (NOT a substitute)
        'diagnostic_m2_orig_AND_m1_cal': cnt(
            lambda r: r['m2_cross_agent'] and r['m1_union_cal']),
        'note': ('m2_cross_agent_cal = True/False/None where None = '
                 'undetermined (a cross-agent checkpoint structure exists '
                 'but no pooled checkpoint θ) — NEVER counted as a '
                 'negative.  The m2_cross_agent original is the frozen '
                 'frame field (injection-boundary-gated, role-gated).'),
    }
    for r in ledger:
        m2['cal_reasons'][r['m2_cal_reason']] = \
            m2['cal_reasons'].get(r['m2_cal_reason'], 0) + 1

    m3 = {
        'supported': cnt(lambda r: r['m3_reach_class'] in
                         ('verified', 'plausible')),
        'verified': cnt(lambda r: r['m3_reach_class'] == 'verified'),
        'plausible': cnt(lambda r: r['m3_reach_class'] == 'plausible'),
        'unsupported': cnt(lambda r: r['m3_reach_class'] == 'unsupported'),
        'not_downstream': cnt(lambda r: r['m3_reach_class']
                              == 'not_downstream'),
        'matched': cnt(lambda r: r['m3_reach_class'] in
                      ('verified', 'plausible') and r['m4_matched']),
        'm3_matched_note': ('M3-matched = dependency-supported (verified or '
                            'plausible reach) AND LEP graded incorrect AND '
                            '>=1 compatible benign reference graded '
                            'correct.  ASSOCIATIONAL, not causal; '
                            'ungradeable executions are never counted as '
                            'failures.'),
    }

    m4classes = {}
    for r in ledger:
        m4classes[r['m4_class']] = m4classes.get(r['m4_class'], 0) + 1

    return {'n_frame': n,
            'rule': L.RULE,
            'm1': m1, 'm2': m2, 'm3': m3,
            'm4_class_counts': m4classes,
            'm4_matched_denominator': m3['supported']}


if __name__ == '__main__':
    if 'PYTHONHASHSEED' not in os.environ:
        _env = dict(os.environ, PYTHONHASHSEED='0')
        os.execve(sys.executable, [sys.executable, os.path.abspath(__file__)]
                  + sys.argv[1:], _env)
    main()
