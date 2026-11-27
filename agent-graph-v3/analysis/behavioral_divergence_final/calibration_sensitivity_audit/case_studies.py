#!/usr/bin/env python3.11
"""Reproducible stratified case-study sampler (audit requirement 4).

Strata:
  removed positives (4,350): (task_family, zero-class) where zero-class
    in {zero_only_union, partly_zero, all_nonzero_theta};
  new positives (256):       (task_family, mechanism) where mechanism in
    {pooling, recentering, evaluable_gain}.

Draw: deterministic seeded rank (SHA-256, seed agentprop-audit-cases-
20270125) over each stratum, up to 3 rows per stratum (9 families x
3 classes x 3 = up to 27 removed; 9 x 3 x 3 = up to 27 new).  No
cherry-picking: the draw is mechanical, the strata are pre-declared
above, and every case packet includes BOTH sides' evidence.

Evidence assembly (agent-authored surface only, per the construct
definitions): final response + last handoff excerpt (blinded the way
the reference packets are), the 3 matched benign final responses,
per-dimension distances/thresholds/flags under BOTH rules, reach +
grade context, and the independent reference label when the row is in
the heldout pair surface.  Writes ledger/case_packets.jsonl.
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audit_lib as A

OUT = os.path.join(A.AUDIT, 'ledger')
SEED = 'agentprop-audit-cases-20270125'
IDX = A.REPO + '/analysis/measurement_validation/.cache/trace_index.jsonl'
REF = os.path.join(A.FINAL, 'out', 'reference_heldout_pairs.jsonl')


def _rank(s):
    return int(hashlib.sha256(f'{SEED}:{s}'.encode()).hexdigest()[:14],
               16)


def _excerpts(trace, n=900):
    fin, hand = None, None
    for e in trace.get('events') or []:
        t = e.get('output_text') or ''
        if e.get('event_type') == 'final_response' and t:
            fin = t[:n]
        elif e.get('event_type') == 'agent_handoff' and t:
            hand = f"[{e.get('agent_role')}] {t[:n]}"
    return {'final_response': fin, 'last_handoff': hand}


def _zero_class(r):
    """Zero-threshold class (matches transition_summary.json's
    zero_classes): zero_only = EVERY triggered dim had theta=0;
    partly_zero = some triggered dims on zero theta + some on
    nonzero; all_nonzero_theta = none on zero."""
    zt = r.get('orig_triggered_on_zero') or []
    trig = r['orig']['triggered']
    if not zt:
        return 'all_nonzero_theta'
    return 'zero_only_union' if len(zt) == len(trig) else 'partly_zero'


def main():
    os.makedirs(OUT, exist_ok=True)
    rows = [json.loads(l) for l in
            open(os.path.join(OUT, 'transition_ledger.jsonl'))]
    ref = {json.loads(l)['lep_trace_id']: json.loads(l)
           for l in open(REF) if l.strip()}
    tidx = {json.loads(l)['trace_id']: json.loads(l)['path']
            for l in open(IDX) if l.strip()}
    # attach mechanism to new positives (from the attribution artifact)
    mech_map = {}
    for r in rows:
        if r['transition'] == 'new_positive':
            cd, od = r['cal']['dims'], r['orig']['dims']
            m = set()
            for d in A.DIMS:
                T, B, th1 = cd[d]['distance'], od[d]['benign_ref'], \
                    od[d]['threshold']
                th2 = cd[d]['threshold']
                if T is None or th2 is None or not T > th2:
                    continue
                if B is None or th1 is None:
                    m.add('evaluable_gain')
                elif T > th1:
                    m.add('recentering')
                else:
                    m.add('pooling')
            mech_map[r['trace_id']] = '+'.join(sorted(m)) or 'undecomposed'

    strata = {}
    for r in rows:
        if r['transition'] == 'removed_positive':
            key = (r['task_family'], _zero_class(r))
        elif r['transition'] == 'new_positive':
            key = (r['task_family'],
                   mech_map.get(r['trace_id'], 'undecomposed'))
        else:
            continue
        strata.setdefault(key, []).append(r)

    picked = []
    for key in sorted(strata, key=lambda k: str(k)):
        def _r(r):
            tag = 'removed' if r['transition'] == 'removed_positive' \
                else 'new'
            return _rank(tag + str(key) + ':' + r['trace_id'])
        mem = sorted(strata[key], key=_r)
        picked += mem[:3]

    # attach evidence + context
    packets = []
    for r in picked:
        tid = r['trace_id']
        dsr = A.ds_by().get(tid, {})
        refs = dsr.get('reference_set') or []
        rec = {
            'trace_id': tid,
            'fixture_id': r['fixture_id'],
            'transition': r['transition'],
            'stratum': [r['task_family'],
                        (_zero_class(r) if r['transition']
                         == 'removed_positive'
                         else mech_map.get(tid, '?'))],
            'lep_code': r['lep_code'],
            'topology': r['topology'],
            'propagation_mode': r['propagation_mode'],
            'orig': {
                'triggered': r['orig']['triggered'],
                'zero_only': r['orig']['zero_only'],
                'zero_triggered': r.get('orig_triggered_on_zero', []),
                'dims': {d: {'distance': v['distance'],
                             'threshold': v['threshold'],
                             'benign_ref': v['benign_ref'],
                             'delta': v['delta'],
                             'fired': v['divergent'],
                             'zero_thr': v['zero_threshold']}
                         for d, v in r['orig']['dims'].items()},
            },
            'cal': {
                'triggered': r['cal']['triggered'],
                'dims': {d: {'distance': v['distance'],
                             'threshold': v['threshold'],
                             'fired': v['divergent']}
                         for d, v in r['cal']['dims'].items()},
            },
            'cal_excess': r.get('cal_dim_excess'),
            'reach': {'m3_reach_class': r['m3_reach_class'],
                      'm3_detail': r['m3_detail'],
                      'm2_cross_agent': r['m2_cross_agent'],
                      'm2_scope': r['m2_scope'],
                      'm4_task_failure': r['m4_task_failure'],
                      'm4_class': r['m4_class'],
                      'm4_matched': r['m4_matched'],
                      'lep_grade': r['lep_grade'],
                      'benign_grades': r['benign_grades']},
            'ckpt': {'d_peak': r['ckpt_d_peak'],
                     'd_final': r['ckpt_d_final'],
                     'stage_thr_frozen': r['ckpt_stage_thr_frozen'],
                     'final_thr_frozen': r['ckpt_final_thr_frozen'],
                     'theta_cal': (A.cal_by().get(tid) or {})
                     .get('ckpt_theta_cal'),
                     'm2_cross_agent_cal': (A.cal_by().get(tid) or {})
                     .get('m2_cross_agent_cal')},
        }
        # independent reference label, if this row is in the heldout
        # pair surface
        rp = ref.get(tid)
        rec['independent_reference'] = ({
            'available': rp is not None,
            'union_ref': rp.get('union_ref') if rp else None,
            'pair_class': rp.get('pair_class') if rp else None,
            'resolved_dims': rp.get('resolved_dims') if rp else None,
            'note': 'heldout reference pair; None = unresolved, excluded'
        } if rp else {'available': False, 'union_ref': None})
        # agent-authored evidence (LEP + its matched benign references)
        letp = tidx.get(tid)
        lt = json.load(open(letp)) if letp and os.path.exists(letp) \
            else None
        rec['evidence'] = {'lep': _excerpts(lt) if lt else
                           {'note': 'trace missing'}}
        bens = []
        for bt in refs:
            bp = tidx.get(bt)
            b = json.load(open(bp)) if bp and os.path.exists(bp) \
                else None
            bens.append({'trace_id': bt,
                         **( _excerpts(b) if b else {'note': 'missing'})})
        rec['evidence']['benign'] = bens
        packets.append(rec)

    with open(os.path.join(OUT, 'case_packets.jsonl'), 'w') as fh:
        for p in packets:
            fh.write(json.dumps(p, default=str) + '\n')
    nrm = sum(1 for p in packets if p['transition'] == 'removed_positive')
    nna = sum(1 for p in packets if p['transition'] == 'new_positive')
    print(f'packets: {len(packets)} ({nrm} removed, {nna} new) -> '
          'ledger/case_packets.jsonl')
    # compact index
    idx_out = []
    for p in packets:
        idx_out.append({'trace_id': p['trace_id'],
                        'transition': p['transition'],
                        'stratum': p['stratum'],
                        'cal_triggered': p['cal']['triggered'],
                        'ref_union': p['independent_reference'].get(
                            'union_ref')})
    print(json.dumps(idx_out, indent=1))


if __name__ == '__main__':
    main()
