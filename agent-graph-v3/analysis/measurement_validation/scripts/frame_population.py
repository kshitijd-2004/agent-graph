#!/usr/bin/env python3.11
"""Build the measurement-validation population frame (one row per measurement unit).

READ-ONLY.  Joins the four frozen/corrected artifacts that describe the SAME
16,773 comparable measurement rows, and attaches everything the sampler and
the evaluator need:

  * analysis/consolidated_20261121_bnd_v41/bnd_v41_run/bnd_v41_per_trace.jsonl
        the frozen V4.1 ledger (component/benign distances, thresholds,
        reference sets, frozen extraction states)
  * analysis/consolidated_20261121_bnd_v41/downstream_results/per_trace.jsonl
        the M1-M4 downstream attribution for those same rows
  * analysis/measurement_v6/out/per_trace_v6.jsonl
        the corrected v6 per-dimension divergence and union
  * the trace corpus itself (for extraction-state detail re-derived by
        running BOTH extractors, so the frame never trusts a stored summary
        field it has not reproduced)

JOIN KEY AND ORDERING — established by inspection, then asserted here.
The consolidated ledger's comparable rows and the downstream file agree
**position-wise** on all 16,773 rows (verified: 16,773/16,773).  The v6
pipeline iterates the downstream file, so its rows line up position-wise too.
The join is therefore positional, but every row must AGREE on trace_id or the
frame refuses to be built: a positional join that silently misaligns would
corrupt every downstream statistic.

WHY A POSITIONAL JOIN IS USED RATHER THAN trace_id
SIX trace_ids occur twice in the frozen ledger with two distinct attempt_ids
(same cell, different attempt).  trace_id is therefore NOT a unique key in
this corpus.  The frame's unit identity is ``unit_id = trace_id + '#' +
attempt_id`` and the positional join is what makes the attempt pairing
unambiguous.

Writes only ``.cache/frame_units.jsonl``.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import common as C  # noqa: E402
from extractors import (extractor_versions, load_specs, run_both,
                        state_summary)  # noqa: E402

FRAME_PATH = os.path.join(C.CACHE_DIR, 'frame_units.jsonl')
INDEX_PATH = os.path.join(C.CACHE_DIR, 'trace_index.jsonl')


# ── frozen per-dimension M1 decomposition ────────────────────────────────────
def frozen_dimension_flags(row: dict) -> dict:
    """Reproduce ``whole_trace_positive`` per dimension, exactly.

    Frozen rule (analysis/downstream_propagation_corrected/analyze_downstream.py
    ::whole_trace_positive): for each dimension d,

        theta_d = max over benign-to-benign pairs of D_d      (None if no pair)
        positive_d = (D_d(lep) is not None and D_d(benign) is not None
                      and theta_d is not None
                      and D_d(lep) - D_d(benign) > theta_d)

    ``D_d(benign)`` is the stored ``component_benign[d]`` (the mean over the
    reference pool), NOT recomputed here, so this is the frozen number.
    """
    cl = row.get('component_lep') or {}
    cb = row.get('component_benign') or {}
    pb = row.get('pair_benign') or []
    out = {}
    for d in C.DESIGN['dimensions']:
        vals = [p['components'][d] for p in pb
                if (p.get('components') or {}).get(d) is not None]
        thr = max(vals) if vals else None
        dl, db = cl.get(d), cb.get(d)
        pos = (dl is not None and db is not None and thr is not None
               and (dl - db) > thr)
        out[d] = {
            'positive': bool(pos),
            'evaluable': dl is not None and db is not None and thr is not None,
            'd_lep': dl,
            'd_benign': db,
            'theta': thr,
            'delta': (dl - db) if (dl is not None and db is not None) else None,
            'zero_threshold': (thr == 0.0),
            'd_lep_missing': dl is None,
            'd_benign_missing': db is None,
            'theta_missing': thr is None,
        }
    return out


_POLAR = {('SUPPORTED', 'CONTRADICTED'), ('CONTRADICTED', 'SUPPORTED')}


def deliverable_states(fam: str, fz: dict, v6: dict) -> dict:
    """Per-family FINAL-DELIVERABLE observation states, both extractors.

    The extractors expose the final deliverable under different fields per
    family (established by inspecting live output, not assumed):

      financial_analysis  frozen ``final_answer``        v6 ``answer``
      research_synthesis  frozen ``claim_verdicts``      v6 ``amap`` (*_verdict)
      code_review         frozen ``final_stances``       v6 ``amap``

    Reading the wrong field would make an entire family look 'unavailable'
    (this was observed: the frozen ``final_answer`` is absent for
    research/code, so a family-blind reader reports 90 % unavailable in those
    families).  The family dispatch below is therefore load-bearing.
    """
    if fam == 'financial_analysis':
        fs = [(None, (fz.get('final_answer') or {}).get('state'))]
        vs = [('final_answer', (v6.get('answer') or {}).get('state'))]
    elif fam == 'research_synthesis':
        fs = list((fz.get('claim_verdicts') or {}).items())
        vs = (list((v6.get('amap') or {}).items())
              or list((v6.get('dmap') or {}).items()))
    else:
        fs = list((fz.get('final_stances') or {}).items())
        vs = list((v6.get('amap') or {}).items())

    def cnt(pairs):
        c = {}
        for _k, v in pairs:
            s = (v or {}).get('state') if isinstance(v, dict) else v
            s = s or 'missing'
            c[s] = c.get(s, 0) + 1
        return c

    fc, vc = cnt(fs), cnt(vs)

    def dominant(c):
        if not c:
            return None
        # 'unavailable'/'missing' dominate numerically for many traces; the
        # state that matters for the deliverable is the strongest one present
        for s in ('observed', 'ambiguous', 'absent', 'unavailable', 'missing'):
            if c.get(s):
                return s
        return sorted(c)[0]

    return {'frozen': {'states': fc, 'dominant': dominant(fc), 'n': len(fs)},
            'v6': {'states': vc, 'dominant': dominant(vc), 'n': len(vs)}}


def derived_features(states: dict, st: dict, fam: str) -> dict:
    """Small, frame-level predicates the targeted strata select on.

    Computed from the two extractors' OUTPUT VALUES, not from a stored
    summary, so a targeted stratum cannot be populated by a field that does
    not correspond to what the extractor actually produced.
    """
    fz = (st or {}).get('frozen') or {}
    v6 = (st or {}).get('v6') or {}

    def vals(mp):
        return {k: (v or {}).get('value') for k, v in (mp or {}).items()
                if (v or {}).get('state') == 'observed'}

    fa, va = vals(fz.get('amap')), vals(v6.get('amap'))
    fd, vd = vals(fz.get('dmap')), vals(v6.get('dmap'))

    # frozen-vs-v6 VALUE disagreement on a key both call observed
    shared_a = set(fa) & set(va)
    val_diff_a = sum(1 for k in shared_a if fa[k] != va[k])
    shared_d = set(fd) & set(vd)
    val_diff_d = sum(1 for k in shared_d if fd[k] != vd[k])

    # polarity inversion: a claim verdict that flipped between the two
    # extractors in a way that reverses the sign of the conclusion
    polar = 0
    for k in shared_a:
        a, b = str(fa[k]).upper().strip(), str(va[k]).upper().strip()
        if (a, b) in _POLAR or (b, a) in _POLAR:
            polar += 1

    # state-only disagreement (observed on one side, not on the other)
    sa = {k: (v or {}).get('state') for k, v in (fz.get('amap') or {}).items()}
    sb = {k: (v or {}).get('state') for k, v in (v6.get('amap') or {}).items()}
    state_diff_a = sum(1 for k in set(sa) | set(sb)
                       if sa.get(k) != sb.get(k))

    dl = deliverable_states(fam, fz, v6)

    def dup_max(counters):
        return max((counters or {}).values()) if counters else 0

    return {
        'value_diff_assertions': val_diff_a,
        'value_diff_decisions': val_diff_d,
        'polarity_inversions': polar,
        'state_diff_assertions': state_diff_a,
        'action_dup_max': max(dup_max(fz.get('action_counters')),
                              dup_max(v6.get('action_counters'))),
        'deliverable': dl,
        'frozen_deliverable_state': dl['frozen']['dominant'],
        'v6_deliverable_state': dl['v6']['dominant'],
        'frozen_deliverable_observed_frac': (
            (dl['frozen']['states'].get('observed', 0) / dl['frozen']['n'])
            if dl['frozen']['n'] else None),
        'frozen_n_action_keys': len(fz.get('action_counters') or {}),
    }


def triplet(row: dict, downstream: dict, v6: dict) -> dict:
    return {
        'trace_id': row['trace_id'],
        'attempt_id': row.get('attempt_id'),
        'task_family': row.get('task_family'),
        'fixture_id': row['trace_id'].split('.')[0],
        'topology': row.get('topology'),
        'propagation_mode': row.get('propagation_mode'),
        'lep_code': (row.get('lep_code') or '').replace('LEP_', ''),
        'lep_code_full': row.get('lep_code'),
        'reference_set': list(downstream.get('reference_set') or []),
        'reference_pool_size': row.get('reference_pool_size'),
        # frozen M1-M4
        'm1_frozen': bool(downstream.get('m1_whole_trace_positive')),
        'm1_frozen_dd0': bool(downstream.get('m1_dd0_positive')),
        'm2_frozen': bool(downstream.get('m2_cross_agent_positive')),
        'm2_nonzero_threshold': bool(downstream.get('m2_nonzero_threshold')),
        'm3_reach_class': downstream.get('m3_reach_class'),
        'm4_frozen_task_failure': bool(downstream.get('m4_task_failure')),
        'task_grade': downstream.get('task_grade'),
        'delivery_verified': downstream.get('delivery_verified'),
        'frozen_evaluable': downstream.get('evaluable'),
        'origin_roles': list(downstream.get('origin_roles') or []),
        # v6 union
        'm1_v6': bool(v6.get('m1_union')),
        'v6_union': v6.get('union'),
        'v6_per_dimension': v6.get('per_dimension'),
        'm4_class_v6': v6.get('m4_class'),
        'm4_matched_v6': bool(v6.get('m4_matched')),
        'lep_grade_v6': v6.get('lep_grade'),
        'benign_grades_v6': list(v6.get('benign_grades') or []),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args()
    if os.path.isfile(FRAME_PATH) and not a.force:
        n = sum(1 for _ in open(FRAME_PATH))
        print(f'frame present: {FRAME_PATH} ({n} rows); --force to rebuild')
        return

    C.ensure_dir(C.CACHE_DIR)
    led = C.read_jsonl(C.CONS_LEDGER)
    dl = C.read_jsonl(C.CONS_DOWNSTREAM)
    v6 = C.read_jsonl(C.V6_PER_TRACE)
    comp = [r for r in led if r.get('comparable')]
    print(f'ledger {len(led)} rows, comparable {len(comp)}')
    print(f'downstream {len(dl)} rows, v6 {len(v6)} rows')
    if not (len(comp) == len(dl) == len(v6)):
        raise SystemExit(
            f'row counts disagree: comparable={len(comp)} downstream={len(dl)} '
            f'v6={len(v6)} — refusing to build a positional join')
    bad = [i for i, (r, d, w) in enumerate(zip(comp, dl, v6))
           if not (r['trace_id'] == d['trace_id'] == w['trace_id'])]
    if bad:
        raise SystemExit(
            f'positional join fails on {len(bad)} rows (first index {bad[0]}): '
            'trace_ids do not line up, so a positional join would misalign')
    print('positional join verified on all rows (trace_id agreement 100%)')

    idx = {r['trace_id']: r for r in C.read_jsonl(INDEX_PATH)}
    specs = load_specs()
    vers = extractor_versions()
    print('extractor versions:', json.dumps(vers, indent=1))

    rows_out = []
    t0 = time.time()
    fstate = collections.Counter()
    for i, (r, d, w) in enumerate(zip(comp, dl, v6)):
        tid = r['trace_id']
        meta = idx.get(tid) or {}
        path = meta.get('path')
        rec = triplet(r, d, w)
        rec['unit_id'] = f"{tid}#{(r.get('attempt_id') or '')[:8]}"
        rec['stratum_family'] = rec['task_family']
        rec['frozen_per_dimension'] = frozen_dimension_flags(r)
        rec['frozen_has_dup_trace_id'] = False

        # extraction states, re-derived by running BOTH extractors
        spec = specs.get(rec['fixture_id'])
        if path and spec is not None:
            try:
                tr = C.read_json(path)
                st = run_both(tr, spec, rec['task_family'])
                rec['states'] = state_summary(st)
                rec['derived'] = derived_features(rec['states'], st,
                                                  rec['task_family'])
            except Exception as exc:                            # noqa: BLE001
                rec['states'] = {'error': f'{type(exc).__name__}: {exc}'}
                rec['derived'] = {}
                fstate['extract_error'] += 1
        else:
            rec['states'] = {'error': 'trace_not_found_or_spec_missing'}
            rec['derived'] = {}
            fstate['missing_input'] += 1
        fstate['n'] += 1
        rows_out.append(rec)
        if (i + 1) % 2500 == 0:
            print(f'  framed {i + 1}/{len(comp)} ({time.time() - t0:.0f}s)',
                  file=sys.stderr)

    # mark the six ambiguous trace_ids so the sampler and the evaluator can
    # treat them as a known defect rather than discovering it late
    dup = collections.Counter(r['trace_id'] for r in rows_out)
    dup_ids = {k for k, v in dup.items() if v > 1}
    for r in rows_out:
        if r['trace_id'] in dup_ids:
            r['frozen_has_dup_trace_id'] = True
    print(f'ambiguous trace_ids in the frame: {len(dup_ids)} ('
          f'{sum(1 for r in rows_out if r["frozen_has_dup_trace_id"])} rows)')

    C.write_jsonl(FRAME_PATH, rows_out)
    C.write_json(os.path.join(C.CACHE_DIR, 'frame_meta.json'), {
        'n_units': len(rows_out),
        'n_unique_trace_ids': len({r['trace_id'] for r in rows_out}),
        'n_ambiguous_trace_ids': len(dup_ids),
        'ambiguous_trace_ids': sorted(dup_ids),
        'extractor_versions': vers,
        'frozen_module_sha256': C.sha256_file(C.FROZEN_MODULE),
        'frozen_module_note': C.FROZEN_MODULE_NOTE,
        'state_counts': dict(fstate),
        'built_seconds': round(time.time() - t0, 1),
    })
    print(f'wrote {len(rows_out)} frame units -> {FRAME_PATH}')


if __name__ == '__main__':
    main()
