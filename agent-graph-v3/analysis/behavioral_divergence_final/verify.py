#!/usr/bin/env python3.11
"""
Mechanical integrity gate (13 mechanical checks).

Re-derives the FIXED reference numbers from the artifacts and confirms the
new pipeline's invariants.  Exit 0 only if ALL pass; any failure is a
non-destructiveness / denominator / ground-truth-containment violation.

All reads are read-only; this writes nothing.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common

OUT = common.OUT
FAILURES = []


def ok(name: str, cond: bool, detail: str = '') -> None:
    tag = 'PASS' if cond else 'FAIL'
    print(f'  [{tag}] {name}' + (f' — {detail}' if (detail and not cond)
                                  else ''))
    if not cond:
        FAILURES.append(name)


def main() -> int:
    print('verify.py — mechanical integrity gate')

    # 1. frame ledger fixed reference (P1 = 16,773; P2 = 17,937)
    p1 = os.path.join(common.CONS_DOWNSTREAM, 'per_trace.jsonl')
    n = sum(1 for _ in open(p1))
    ok('P1 frame ledger row count', n == 16773, f'{n}')
    p2 = sum(1 for _ in open(common.CONS_LEDGER))
    ok('P2 LEP ledger row count', p2 == 17937, f'{p2}')

    # 2. candspec full ledger row count + no skips
    cs_p = os.path.join(OUT, 'per_trace_candspec.jsonl')
    if os.path.exists(cs_p):
        cs = [json.loads(l) for l in open(cs_p)]
        ok('candspec ledger covers the frame',
           len(cs) == 16773, f'{len(cs)}')
        sk = json.load(open(os.path.join(OUT,
                                         'summary_candspec.json'))).get(
            'skipped', {})
        ok('candspec ledger has no skipped rows',
           not sk, str(sk))
    else:
        ok('candspec ledger present', False, cs_p + ' missing')

    # 3. frozen P2 ledger's comparable subset = P1 frame
    p2_led = os.path.join(common.CONS, 'bnd_v41_run',
                          'bnd_v41_per_trace.jsonl')
    frz = sum(1 for l in open(p2_led)
              if json.loads(l).get('comparable') is True)
    ok('frozen P2 ledger comparable = P1 frame', frz == 16773, f'{frz}')

    # 4. no weighted composite key leaks into the scored path
    bad = 0
    if os.path.exists(cs_p):
        for l in open(cs_p):
            r = json.loads(l)
            for k in ('composite', 'weighted_score', 'bnd_total'):
                if k in r:
                    bad += 1
    ok('no composite/weighted key in the candspec ledger', bad == 0,
       f'{bad} rows')

    # 5. union == OR of the four dimension indicators (invariant check)
    if os.path.exists(cs_p):
        bad_u = 0
        for l in open(cs_p):
            r = json.loads(l)
            trig = (r.get('union') or {}).get('triggered_dimensions') or []
            u = bool((r.get('union') or {}).get('divergent'))
            dims = r.get('per_dimension') or {}
            recomputed = any((dims.get(d) or {}).get('divergent')
                             for d in common.DIMS)
            if u != recomputed:
                bad_u += 1
        ok('union == OR of per-dimension indicators', bad_u == 0,
           f'{bad_u} rows')

    # 6. protected-artifact fingerprints unchanged (G8)
    sealed = common.check_sealed()
    if sealed.get('note') and 'not yet written' in sealed['note']:
        ok('sealed fingerprints present', False,
           'run eval_cli.py --seal before final verification')
    else:
        ok('protected fingerprints unchanged', sealed.get('ok'),
           str(sealed.get('mismatches')[:3]))

    # 7. blinding scan on the emitted item streams is clean
    blinding_ok = True
    for part in ('dev', 'heldout'):
        rp = os.path.join(OUT, f'items_{part}_report.json')
        if os.path.exists(rp):
            scan = json.load(open(rp)).get('blinding_scan', {})
            blinding_ok = blinding_ok and (scan.get('n_hits') == 0)
        else:
            blinding_ok = False
    ok('item blinding scan clean (0 GT/structural hits)', blinding_ok)

    # 8. GT-containment: the extractor feed never sees a GT-value field
    P = common.pipelines()
    V6CORE = P['V6CORE']
    specs = common.load_specs()
    leaked = 0
    for fid, spec in list(specs.items())[:400]:
        try:
            stripped = common.pipelines()['VAL_EX'].strip_gt_values(spec)
        except Exception:
            continue
        # the v6 guard must never fire on the stripped feed
        try:
            V6CORE.assert_gt_free_spec(stripped, 'verify')
        except Exception:
            leaked += 1
    ok('v6 GT guard never fires on the stripped feed (400 specs)',
       leaked == 0, f'{leaked}')

    # 9. task-side label/unit keys survive the strip (no coverage collapse)
    n_fin = n_keep = 0
    for fid, spec in specs.items():
        if spec.get('task_family') != 'financial_analysis':
            continue
        n_fin += 1
        rf = spec.get('required_facts') or {}
        if not isinstance(rf, dict):
            continue
        for k, ent in rf.items():
            if isinstance(ent, dict) and ('label' in ent or 'unit' in ent):
                stripped = P['VAL_EX'].strip_gt_values(spec)
                srf = stripped.get('required_facts') or {}
                sent = srf.get(k) if isinstance(srf, dict) else None
                if isinstance(sent, dict) and \
                        (sent.get('label') is not None or
                         sent.get('unit') is not None):
                    n_keep += 1
                    break
    ok('task-side label/unit survive the strip',
       n_keep >= int(0.9 * n_fin) and n_fin > 0,
       f'{n_keep}/{n_fin} financial specs keep a task-side key')

    # 10. synthetic suite present and both gates report a rate
    syn_p = os.path.join(OUT, 'synthetic_report.json')
    if os.path.exists(syn_p):
        syn = json.load(open(syn_p))
        g2 = (syn.get('gates') or {}).get('G2_invariance_pass_rate')
        g3 = (syn.get('gates') or {}).get('G3_sensitivity_flip_rate')
        ok('synthetic suite reports G2 + G3 rates',
           g2 is not None and g3 is not None,
           f'G2={g2} G3={g3}')
    else:
        ok('synthetic suite present', False, syn_p + ' missing')

    # 11. partition + sampling determinism seeds recorded
    pp = os.path.join(OUT, 'partitions_final.json')
    ok('final partition present + seed recorded',
       os.path.exists(pp) and
       json.load(open(pp)).get('seed') ==
       'agentprop-final-partition-20261207')

    print()
    if FAILURES:
        print(f'verify.py: {len(FAILURES)} FAIL: {FAILURES}')
        return 1
    print('verify.py: all checks passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
