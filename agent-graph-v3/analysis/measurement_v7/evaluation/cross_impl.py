"""
Cross-implementation divergence comparison: frozen vs v6 vs v7.

Single source of truth for the frozen and v6 per-dimension indicators is the
validation FRAME LEDGER (``.cache/frame_units.jsonl``) — the same re-derived
frozen/v6 per-dimension flags the matched-pair study uses.  The v7
indicators come from this directory's pipeline output.  All three are keyed
on ``trace_id``; a comparison set is the intersection of the three, so a row
only appears if every implementation produced a union for it.

This module answers requirement 12's core questions:
  * per-dimension and union divergence for each implementation,
  * how many positives depend EXCLUSIVELY on a zero threshold
    (theta_d == 0 on every triggered dimension),
  * frozen/v6/v7 pairwise disagreements,
  * the M4-matched comparison (requirement 13).

It writes ONLY a JSON report under out/.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
V7 = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, V7)
import base                                          # noqa: E402

OUT = os.path.join(V7, 'out')
DIMS = base.DIMS


def _frozen_v6_from_frame() -> Dict[str, Dict[str, Any]]:
    """trace_id -> {frozen: {dim:bool}, v6: {dim:bool}, frozen_m1, v6_m1,
    frozen_zero_only, v6_zero_only, v6_union}.  From the frame ledger."""
    p = os.path.join(base.VALIDATION_CACHE, 'frame_units.jsonl')
    out: Dict[str, Dict[str, Any]] = {}
    for line in open(p):
        if not line.strip():
            continue
        r = json.loads(line)
        tid = r['trace_id']
        fpd = r.get('frozen_per_dimension') or {}
        v6pd = r.get('v6_per_dimension') or {}
        fpos = {d: bool((fpd.get(d) or {}).get('positive'))
                for d in DIMS}
        v6trig = (r.get('v6_union') or {}).get('triggered_dimensions') or []
        v6z = (r.get('v6_union') or {}).get('zero_threshold_triggered') or []
        vpos = {d: (d in v6trig) for d in DIMS}
        # frozen zero-only: every triggered dim has theta==0
        ftrig = [d for d in DIMS if fpos[d]]
        fzero_only = bool(ftrig) and all(
            (fpd.get(d) or {}).get('theta') == 0.0 for d in ftrig)
        vzero_only = bool(v6trig) and set(v6z) == set(v6trig)
        out[tid] = {
            'frozen': fpos, 'v6': vpos,
            'frozen_m1': bool(r.get('m1_frozen')),
            'v6_m1': bool(r.get('m1_v6')),
            'frozen_zero_only': fzero_only,
            'v6_zero_only': vzero_only,
            'v6_union': r.get('v6_union'),
            'm4_matched_v6': bool(r.get('m4_matched_v6')),
            'm4_class_v6': r.get('m4_class_v6'),
        }
    return out


def _v7_from_pipeline() -> Dict[str, Dict[str, Any]]:
    p = os.path.join(OUT, 'per_trace_v7.jsonl')
    out: Dict[str, Dict[str, Any]] = {}
    if not os.path.exists(p):
        return out
    for line in open(p):
        if not line.strip():
            continue
        r = json.loads(line)
        tid = r['trace_id']
        pd = r.get('per_dimension') or {}
        u = r.get('union') or {}
        trig = u.get('triggered_dimensions') or []
        zt = u.get('zero_threshold_triggered') or []
        out[tid] = {
            'v7': {d: bool((pd.get(d) or {}).get('divergent'))
                   for d in DIMS},
            'v7_m1': bool(u.get('divergent')),
            'v7_zero_only': bool(trig) and set(zt) == set(trig),
            'm4_matched': bool(r.get('m4_matched')),
            'm4_class': r.get('m4_class'),
        }
    return out


def compare(out_prefix: str = 'cross') -> Dict[str, Any]:
    fv = _frozen_v6_from_frame()
    v7 = _v7_from_pipeline()
    common = sorted(set(fv) & set(v7))
    rows: List[Dict[str, Any]] = []
    for t in common:
        a, b = fv[t], v7[t]
        rows.append({
            'trace_id': t,
            'frozen': a['frozen'], 'v6': a['v6'], 'v7': b['v7'],
            'frozen_m1': a['frozen_m1'], 'v6_m1': a['v6_m1'],
            'v7_m1': b['v7_m1'],
            'frozen_zero_only': a['frozen_zero_only'],
            'v6_zero_only': a['v6_zero_only'],
            'v7_zero_only': b['v7_zero_only'],
            'm4_matched_v6': a['m4_matched_v6'],
            'm4_matched_v7': b['m4_matched'],
            'm4_class_v6': a['m4_class_v6'],
            'm4_class_v7': b['m4_class'],
        })
    n = len(rows)

    def rate(pred) -> Dict[str, Any]:
        c = sum(1 for r in rows if pred(r))
        return {'count': c, 'rate': (c / n) if n else None}

    rep: Dict[str, Any] = {
        'n_common': n,
        'per_implementation_m1': {
            'frozen': rate(lambda r: r['frozen_m1']),
            'v6': rate(lambda r: r['v6_m1']),
            'v7': rate(lambda r: r['v7_m1']),
        },
        'positive_dependent_only_on_zero_threshold': {
            'frozen': rate(lambda r: r['frozen_zero_only'] and
                          r['frozen_m1']),
            'v6': rate(lambda r: r['v6_zero_only'] and r['v6_m1']),
            'v7': rate(lambda r: r['v7_zero_only'] and r['v7_m1']),
        },
        'per_dimension_rates': {
            impl: {d: rate(lambda r, d=d: r[impl].get(d) or
                           (r[f'{impl}_m1'] if d == 'union' else False))
                   for d in DIMS}
            for impl in ('frozen', 'v6', 'v7')},
        'pairwise_m1_disagreement': {
            'frozen_vs_v6': rate(lambda r: r['frozen_m1'] != r['v6_m1']),
            'frozen_vs_v7': rate(lambda r: r['frozen_m1'] != r['v7_m1']),
            'v6_vs_v7': rate(lambda r: r['v6_m1'] != r['v7_m1']),
        },
        'm4_matched': {
            'v6': rate(lambda r: r['m4_matched_v6']),
            'v7': rate(lambda r: r['m4_matched_v7']),
            'caveat': 'perturbation-associated, not causal'},
        'note': ('zero-threshold-only counts are how many union positives '
                 'rest ENTIRELY on dimensions whose benign threshold is '
                 'exactly 0 — i.e. "any nonzero difference", not "an '
                 'unusually large difference".  theta_d=0 is NOT changed '
                 'here; it is only reported.'),
    }
    p = os.path.join(OUT, f'{out_prefix}_impl.json')
    json.dump(rep, open(p, 'w'), indent=2)
    jp = os.path.join(OUT, f'{out_prefix}_impl_rows.jsonl')
    with open(jp, 'w') as f:
        for r in rows:
            f.write(json.dumps(r) + '\n')
    print(json.dumps({k: rep[k] for k in
                      ('n_common', 'per_implementation_m1',
                       'positive_dependent_only_on_zero_threshold',
                       'pairwise_m1_disagreement')}, indent=2))
    print(f'wrote {p} and {jp}')
    return rep


if __name__ == '__main__':
    compare()
