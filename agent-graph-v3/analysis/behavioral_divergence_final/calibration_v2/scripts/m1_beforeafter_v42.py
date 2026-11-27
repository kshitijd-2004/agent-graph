#!/usr/bin/env python3.11
"""Apples-to-apples M1 before/after on the round-2 development surface:

  BEFORE = recentered-extremal gate (V4.1 construct) over the FROZEN V4.1
           ledger (analysis/consolidated_20261121_bnd_v41/.../bnd_v41_per_trace.jsonl)
  AFTER  = the same recentered gate (OP0) and the V4.2 dim-typed gate (OP1)
           over the V4.2 ledger.
All three are scored by the SAME scorer against the SAME 221-row round-2
reference surface (AI-adjudicated; treated as development evidence).
Also per-case before/after on the 44 diagnosed error cases.
Writes out_v42/m1_beforeafter_v42.json.  No LLM calls.
"""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import m1_gate_v42 as G

FROZEN_LEDGER = os.path.join(G.REPO, 'analysis',
    'consolidated_20261121_bnd_v41', 'bnd_v41_run', 'bnd_v41_per_trace.jsonl')

def main():
    led_v41 = {}
    for l in open(FROZEN_LEDGER):
        r = json.loads(l); led_v41[r['trace_id']] = r
    led_v42 = G.load_v42_ledger()
    refs = G.load_round2_refs()   # pilot-excluded, label=None where excluded
    cells, _ = __import__('cv2').load_cells()
    P1 = [c['trace_id'] for c in cells]
    seen = set(); P1star = []
    for t in P1:
        if t not in seen:
            seen.add(t); P1star.append(t)
    p_hat = G.family_disagree_mass(led_v42, P1star)
    ctx = {'p_hat': p_hat, 'alpha': G.ALPHA}
    out = {}
    out['BEFORE_v41_recentered'] = G.confusion('OP0', led_v41, refs, P1star, ctx)
    out['AFTER_v42_OP0'] = G.confusion('OP0', led_v42, refs, P1star, ctx)
    out['AFTER_v42_OP1'] = G.confusion('OP1', led_v42, refs, P1star, ctx)
    # per-case before/after on the 44 diagnosed cases
    r2 = json.load(open(os.path.join(G.STUDY, 'recovery', 'ledger', 'r2_results.json')))
    v41m = r2['mechanistic']['V41_union']
    cases = [r['trace_id'] for r in v41m['FNs']] + [r['trace_id'] for r in v41m['FPs']]
    def fires_union(led, t, op):
        row = led.get(t)
        if not row or not row.get('comparable'): return None
        f = G.union_op(row, op, ctx)
        return any(v is True for v in f.values())
    pc = []
    for t in cases:
        b, a0, a1 = (fires_union(led_v41, t, 'OP0'),
                     fires_union(led_v42, t, 'OP0'),
                     fires_union(led_v42, t, 'OP1'))
        pc.append({'trace_id': t,
                   'v41_fired': b, 'v42_op0_fired': a0, 'v42_op1_fired': a1})
    out['per_case'] = pc
    os.makedirs(G.OUT, exist_ok=True)
    json.dump(out, open(os.path.join(G.OUT, 'm1_beforeafter_v42.json'), 'w'), indent=1)
    for k in ('BEFORE_v41_recentered', 'AFTER_v42_OP0', 'AFTER_v42_OP1'):
        cc = out[k]['confusion']
        print(f"{k:24s} P={cc['P']:.3f} R={cc['R']:.3f} F1={cc['F1']:.3f} "
              f"(TP {cc['TP']} FP {cc['FP']} FN {cc['FN']})")
    # transitions
    rec_fn = sum(1 for c in pc if c['trace_id'] in [r['trace_id'] for r in v41m['FNs']]
                 and not c['v41_fired'] and c['v42_op0_fired'])
    new_fn = sum(1 for c in pc if c['trace_id'] in [r['trace_id'] for r in v41m['FNs']]
                 and not c['v42_op0_fired'])
    fp_still = sum(1 for c in pc if c['trace_id'] in [r['trace_id'] for r in v41m['FPs']]
                   and c['v42_op0_fired'])
    fp_fixed = sum(1 for c in pc if c['trace_id'] in [r['trace_id'] for r in v41m['FPs']]
                    and not c['v42_op0_fired'])
    print(f"44-case OP0: FNs recovered {rec_fn}/35, FPs resolved {fp_fixed}/9, "
          f"FPs still firing {fp_still}/9")
if __name__ == '__main__':
    main()
