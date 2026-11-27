#!/usr/bin/env python3.11
"""Checkpoint-level (B3) and benign-control (B4 + deterministic B2B)
validation.  The two are computed SEPARATELY and never conflated
(requirement 6): probe-level positive rates vs execution-level FPRs
are distinct quantities.

  B3 checkpoint: the adjudicator/judges' checkpoint-scope verdicts
      (aligned downstream agent, boundary-onward evidence) vs the
      frozen S1 M2 dimension-specific checkpoint gate (re-derived via
      cv2 from ledger/checkpoint_pairs.jsonl).  Reports per-dim and
      checkpoint-scope agreement, unresolved rate.
  B4 H0: judges' own null behavior — any 'diverges' on a
      benign-vs-benign execution is a judge-level false positive.
      Execution-level and probe-level rates reported separately.
  B2B deterministic: the pre-registered p3 probe-union control for
      S1 (the cv2 object, re-derived here; the gate object for the
      execution-level FPR criterion C4).  The LLM H0 (B4) is a
      CORROBORATING estimate, never the gate.
Writes ledger/checkpoint_validation.json +
       ledger/benign_control_validation.json.
"""
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
STUDY = os.path.abspath(os.path.join(HERE, '..'))
V2 = os.path.abspath(os.path.join(STUDY, '..', 'calibration_v2'))
LED = os.path.join(STUDY, 'ledger')
JC = os.path.join(STUDY, 'out', 'judge_cache')
sys.path.insert(0, V2)
import cv2  # noqa: E402


def _load(judge, pid, task='annotate'):
    p = os.path.join(JC, judge, f'{pid}__{task}__cv2val-ann-v3.json')
    if not os.path.exists(p):
        return None
    return json.load(open(p))


def main():
    man = json.load(open(os.path.join(LED, 'sample_manifest.json')))
    raw = open(os.path.join(LED, 'reference_labels.jsonl')).read()
    try:
        refs = json.loads(raw)
    except json.JSONDecodeError:
        refs = [json.loads(l) for l in raw.splitlines() if l.strip()]
    refby = {r['pack_id']: r for r in refs}

    # ── B3 checkpoint-level ──────────────────────────────────────────
    b3 = man['cohort_b_targeted'].get('B3_checkpoint_aligned') or []
    # S1 M2 gate re-derived (frozen checkpoint machinery)
    cells = cv2.load_cells()[0]
    pairs = [json.loads(l) for l in
             open(os.path.join(V2, 'ledger', 'checkpoint_pairs.jsonl'))]
    bytid = {p['trace_id']: p for p in pairs}
    # per-row M2 fire: replicate compute_production's gate exactly
    pcls = cv2.build_pools(cv2.load_full_cells())
    pool_inter = {d: [] for d in cv2.DIMS}
    pool_final = {d: [] for d in cv2.DIMS}
    for r in pairs:
        for d in cv2.DIMS:
            pool_inter[d].extend((r.get('ben_inter') or {}).get(d) or [])
            pool_final[d].extend((r.get('ben_final') or {}).get(d) or [])
    for d in cv2.DIMS:
        pool_inter[d] = sorted(pool_inter[d])
        pool_final[d] = sorted(pool_final[d])

    def m2_fires(tid):
        r = bytid.get(tid)
        if not r:
            return None, {}
        bi, bf = r.get('ben_inter') or {}, r.get('ben_final') or {}
        ti = {d: cv2.loo_quantile(pool_inter[d],
                                   sorted(bi.get(d) or []))
              for d in cv2.DIMS}
        tf = {d: cv2.loo_quantile(pool_final[d],
                                  sorted(bf.get(d) or []))
              for d in cv2.DIMS}
        fired = {}
        for u in (r.get('usable') or []):
            if not u.get('is_cross_agent'):
                continue
            mask = (cv2.CONT + ('outcomes',)) if u.get('is_final') \
                else ('assertions', 'actions')
            for d in mask:
                pool = bf if u.get('is_final') else bi
                comp = (u.get('comp_mean') or {})
                v = comp.get(d)
                thr = (tf if u.get('is_final') else ti)[d]
                if v is None or thr is None:
                    continue
                if v > thr:
                    fired[d] = u.get('key')
        return (bool(fired)), fired

    b3_out = {'n': len(b3), 'rows': [], 'agreed': 0, 'n_eval': 0,
              'unresolved': 0}
    for tid in b3:
        pid = 'B3__' + tid
        r = refby.get(pid)
        s1fired, fired_dims = m2_fires(tid)
        # judge checkpoint verdicts: LEP label's checkpoint_scope
        ja = _load('judge_a', pid)
        jb = _load('judge_b', pid)
        adj = _load('adjudicator', pid, 'adjudicate')
        lep = (json.load(open(os.path.join(STUDY, 'out', 'packets',
                                            pid + '.json')))
               .get('lep_execution_label'))
        ck = {}
        for jn, jj in (('a', ja), ('b', jb)):
            js = (jj.get('judgment') or {}).get('checkpoint_scope') \
                if jj else None
            ck[jn] = (js or {}).get(lep) if js else None
        # consensus: agree-or-adjudicated; under addendum A1 cohort B is
        # judge_a-only, so a judge_a checkpoint verdict is a DISCLOSED
        # single-judge diagnostic (never a headline input)
        verdict = None
        basis = None
        if ck.get('a') == ck.get('b') and ck.get('a') is not None:
            verdict, basis = ck['a'], 'two_judge'
        elif adj and (adj.get('judgment') or {}).get('resolutions'):
            ent = (adj['judgment']['resolutions'].get(
                f'{pid}::{lep}') or {})
            verdict = ent.get('verdict')
            basis = 'adjudicated'
        elif ck.get('a') is not None and ck.get('b') is None:
            verdict, basis = ck['a'], 'single_judge_a (addendum A1)'
        elif ck.get('b') is not None and ck.get('a') is None:
            verdict, basis = ck['b'], 'single_judge_b (addendum A1)'
        if verdict in ('diverges', 'ordinary'):
            b3_out['n_eval'] += 1
            b3_out['agreed'] += int((verdict == 'diverges')
                                    == bool(s1fired))
        else:
            b3_out['unresolved'] += 1
        b3_out['rows'].append({
            'trace_id': tid, 's1_m2_fired': s1fired,
            's1_fired_dims': sorted(fired_dims),
            'judge_a_ckpt': ck.get('a'), 'judge_b_ckpt': ck.get('b'),
            'ckpt_verdict': verdict, 'ckpt_basis': basis})
    json.dump(b3_out, open(os.path.join(LED,
                                        'checkpoint_validation.json'),
                           'w'), indent=1)

    # ── B4 H0 judge null-FP + deterministic B2B gate ────────────────
    b4 = man['cohort_b_targeted'].get('B4_h0_benign_pairs') or []
    probes = 0
    probe_pos = 0
    execs = 0
    exec_pos = 0
    both_unres = 0
    for tid in b4:
        pid = 'B4__' + tid
        for jn in ('a', 'b'):
            jj = _load('judge_' + jn, pid)
            if jj is None:
                continue
            pe = (jj.get('judgment') or {}).get('per_execution') or {}
            for lab, v in pe.items():
                u = (v or {}).get('union')
                probes += 1
                probe_pos += int(u == 'diverges')
                if u == 'unresolved':
                    both_unres += 1
    # execution-level: for a judge, the packet is "FP" if ANY of the
    # four benign executions is called diverges (multiple-opportunity
    # accounting, reported at both scopes, never conflated)
    for tid in b4:
        pid = 'B4__' + tid
        for jn in ('a', 'b'):
            jj = _load('judge_' + jn, pid)
            if jj is None:
                continue  # judge_b absent under addendum A1 (disclosed)
            execs += 1
            pe = (jj.get('judgment') or {}).get('per_execution') or {}
            if any(((v or {}).get('union') == 'diverges')
                   for v in pe.values()):
                exec_pos += 1
        # probe-level: same availability (counted per present judge)
    # deterministic B2B p3 probe-union control (the pre-registered
    # gate object; cv2 b2b_control re-derivation)
    full = cv2.load_full_cells()
    v6dim = cv2.load_v6_dim()
    pools = cv2.build_pools(full)
    zt = cv2.shrinkage_weights(full, v6dim, cv2.build_pair_pools(full),
                               pools)
    onull = cv2.outcome_null(full)
    thr, _stat = cv2.build_policy_theta(full, v6dim, pools, zt,
                                        'S1_shrinkage')
    b2b = cv2.b2b_control(full, thr, onull)
    ben = {
        'n_h0_packets': len(b4),
        'judge_probe_level': {'n': probes, 'n_pos': probe_pos,
                              'rate': probe_pos / probes if probes else
                              None,
                              'n_unresolved': both_unres,
                              'note': ('a single (judge, execution) '
                                       'call on an H0 packet; the '
                                       'judge-level null-FP rate.  '
                                       'PROBE-LEVEL — not the gate.')},
        'h0_basis': ('judge_a only where judge_b was absent under '
                     'addendum A1 (relay concurrency failures on '
                     'claude-sonnet-5-5); disclosed, never a gate.  '
                     'n = present (judge, H0-packet) or (judge, '
                     'execution) denominators, NOT all 15 x 2 x 4.'),
        'judge_execution_level': {'n': execs, 'n_pos': exec_pos,
                                  'rate': exec_pos / execs if execs
                                  else None,
                                  'note': ('a judge is FP on the packet '
                                           'if ANY of the four benign '
                                           'executions is called '
                                           'diverges (multiple-'
                                           'opportunity accounting); '
                                           'EXECUTION-LEVEL LLM '
                                           'corroborating estimate — '
                                           'the gate is the '
                                           'deterministic B2B below.')},
        'deterministic_b2b_gate': {
            'probe_union_fpr': b2b.get('probe_union_fpr'),
            'probe_union_fire': b2b.get('probe_union_fire'),
            'probe_union_n': b2b.get('probe_union_n'),
            'row_or_fpr': b2b.get('row_or_fpr'),
            'construction': b2b.get('construction'),
            'note': ('pre-registered p3 probe-union control re-derived '
                     'from cv2 (frozen S1 thresholds); the execution-'
                     'level FPR criterion C4 object.  A probe-level '
                     'LLM rate is NEVER claimed as the execution-level '
                     'pass (requirement 6).'),
        },
    }
    json.dump(ben, open(os.path.join(LED,
                                     'benign_control_validation.json'),
                        'w'), indent=1)
    print('B3 checkpoint:', b3_out['agreed'], '/', b3_out['n_eval'],
          '| unresolved:', b3_out['unresolved'])
    print('B4 H0 judge probe-level FP:', ben['judge_probe_level'])
    print('B4 H0 judge execution-level FP:',
          ben['judge_execution_level']['rate'])
    print('deterministic B2B gate (S1 p3 probe-union):',
          ben['deterministic_b2b_gate']['probe_union_fpr'])


if __name__ == '__main__':
    if 'PYTHONHASHSEED' not in os.environ:
        os.environ['PYTHONHASHSEED'] = '0'
        os.execve(sys.executable, [sys.executable] + sys.argv,
                  os.environ)
    main()
