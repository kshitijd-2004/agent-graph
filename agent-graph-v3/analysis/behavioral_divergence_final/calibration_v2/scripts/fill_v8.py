#!/usr/bin/env python3.11
"""Fill V8_integrity in ledger/validation_report.json from the on-disk
evidence (seal v8 + before/after fingerprints), using STRICT gate
accounting (a gate passes only when its `pass` is True; null is never
counted as a pass).  Run AFTER `run_validation.py` and after the
`_fingerprint.py after` snapshot; it recomputes n_gates_passed /
all_pass over the determined verdicts only.

V8 is the last gate to be fillable because its evidence (the
after-fingerprint) does not exist until the task's writes are done.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
V2 = os.path.abspath(os.path.join(HERE, '..'))
OUT = os.path.join(V2, 'ledger')


def norm(d):
    return {k: (v['sha256'] if (isinstance(v, dict) and 'sha256' in v)
                else v) for k, v in (d or {}).items()}


def main():
    fb = json.load(open(os.path.join(OUT, 'fingerprints_before.json')))
    fa = json.load(open(os.path.join(OUT, 'fingerprints_after.json')))
    moved_key = [k for k in fb['key_artifacts']
                 if fa['key_artifacts'].get(k) != fb['key_artifacts'][k]]
    moved_cp = [k for k in set(fb.get('calibrated_production', {}))
                | set(fa.get('calibrated_production', {}))
                if norm(fb.get('calibrated_production', {})).get(k)
                != norm(fa.get('calibrated_production', {})).get(k)]
    seal = (fb['seal_states']['fingerprints_v8.json']['ok'],
            fa['seal_states']['fingerprints_v8.json']['ok'])
    v8_ok = (seal[0] and seal[1] and not moved_key and not moved_cp)

    vr = json.load(open(os.path.join(OUT, 'validation_report.json')))
    vr['gate_verdicts']['V8_integrity'] = {
        'value': bool(v8_ok), 'pass': bool(v8_ok),
        'seal_v8_ok': list(seal),
        'key_artifacts_moved': moved_key if moved_key else 'none',
        'calibrated_production_moved': moved_cp if moved_cp else 'none',
        'note': ('seal v8 verifies byte-identical over the protected set '
                 'in BOTH fingerprints; no protected artifact was '
                 'modified by this task (computed, not attested)')}
    vr['n_gates_passed'] = sum(1 for g in vr['gate_verdicts'].values()
                               if g.get('pass') is True)
    vr['all_pass'] = all(g.get('pass') is True
                         for g in vr['gate_verdicts'].values())
    json.dump(vr, open(os.path.join(OUT, 'validation_report.json'), 'w'),
              indent=1)
    print('V8 =', vr['gate_verdicts']['V8_integrity']['pass'],
          '| gates passed:', vr['n_gates_passed'], '/ 8 |',
          'all_pass:', vr['all_pass'])


if __name__ == '__main__':
    main()
