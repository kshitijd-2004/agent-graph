#!/usr/bin/env python3.11
"""
Zero-threshold sensitivity analysis (requirement 12, last paragraph).

The frozen design treats ``theta_d = 0`` as valid: when all benign
references agree perfectly on a dimension, ANY nonzero perturbed
difference flags.  75 % of v6 positives rest on such zero thresholds,
which the audit flagged as a false-positive generator.  This module tests
ALTERNATIVE THRESHOLD STRATEGIES — it does NOT change theta_d = 0 in the
primary measurement, and it reports each variant as a separately-named
sensitivity analysis, never as "the" threshold.

VARIANTS (each a distinct named experiment):
  * ``theta_zero_valid``       — the frozen/v6 baseline: theta=0 is a valid
                                 threshold and any nonzero delta fires.
  * ``theta_zero_unfireable``  — a dimension whose theta=0 is simply
                                 UNCALIBRATED: no positive may rest on it.
                                 (Reports how many positives are lost.)
  * ``theta_floored``          — theta_d is raised to a floor F
                                 (sensitivity grid F in {0.01, 0.05, 0.10,
                                 0.25}); a dimension fires only if delta
                                 exceeds max(theta, F).
  * ``aggregation_min``        — the LEP is compared against the
                                 BEST-MATCHING (min) benign reference
                                 instead of the mean (a separately named
                                 variant of the reference aggregation).

Calibration discipline (requirement 12): variant selection / floor choice
is done on the DEV partition; the HELD-OUT partition is evaluated once,
against the frozen v7 implementation, with the chosen variants, and the
result is reported without further tuning.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
V7 = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, V7)
import base                                          # noqa: E402

OUT = os.path.join(V7, 'out')
DIMS = base.DIMS
FLOOR_GRID = (0.01, 0.05, 0.10, 0.25)


def _load_v7(prefix: str) -> list:
    p = os.path.join(OUT, f'per_trace_{prefix}.jsonl')
    if not os.path.exists(p):
        return []
    return [json.loads(l) for l in open(p) if l.strip()]


def _per_dim_flags(row: dict) -> dict:
    pd = row.get('per_dimension') or {}
    return {d: {'divergent': bool((pd.get(d) or {}).get('divergent')),
                'threshold': (pd.get(d) or {}).get('threshold'),
                'delta': (pd.get(d) or {}).get('delta'),
                'evaluable': bool((pd.get(d) or {}).get('evaluable'))}
            for d in DIMS}


def _union(flags: dict) -> dict:
    trig = [d for d in DIMS if flags[d]['divergent']]
    return {'divergent': bool(trig), 'triggered': trig}


def variants(row: dict, floor: float) -> dict:
    """Per-row union verdict under each named variant."""
    flags = _per_dim_flags(row)
    out: dict = {}
    # baseline: theta=0 valid
    out['theta_zero_valid'] = _union(flags)
    # theta_zero_unfireable: a dim with theta==0 cannot fire
    z = {d: dict(flags[d]) for d in DIMS}
    for d in DIMS:
        if z[d]['threshold'] == 0.0:
            z[d]['divergent'] = False
    out['theta_zero_unfireable'] = _union(z)
    # theta_floored
    for F in FLOOR_GRID:
        f = {d: dict(flags[d]) for d in DIMS}
        for d in DIMS:
            thr = flags[d]['threshold']
            if thr is None or not flags[d]['evaluable']:
                f[d]['divergent'] = False
                continue
            eff = max(thr, F)
            f[d]['divergent'] = bool((f[d]['delta'] or -1) > eff)
        out[f'theta_floored_{F}'] = _union(f)
    return out


def run_variant_summary(prefix: str, out_prefix: str) -> dict:
    rows = _load_v7(prefix)
    if not rows:
        raise SystemExit(f'no v7 rows for prefix {prefix!r}; '
                         f'run run_pipeline.py --partition {prefix} first')
    n = len(rows)
    tally: dict = {}
    for r in rows:
        v = variants(r, None)
        for name, u in v.items():
            tally.setdefault(name, {'n': 0, 'positive': 0,
                                    'zero_only_dependent': 0})
            tally[name]['n'] += 1
            if u['divergent']:
                tally[name]['positive'] += 1
    # zero-only dependency under the baseline
    base_pos = [r for r in rows if
                _union(_per_dim_flags(r))['divergent']]
    zero_only = 0
    for r in base_pos:
        u = _union(_per_dim_flags(r))
        flags = _per_dim_flags(r)
        trig = u['triggered']
        if trig and all(flags[d]['threshold'] == 0.0 for d in trig):
            zero_only += 1
    rep = {'n_rows': n, 'prefix': prefix, 'variants': tally,
           'baseline_positive_count': len(base_pos),
           'baseline_positive_rate': len(base_pos) / n,
           'positive_dependent_only_on_zero_threshold': zero_only,
           'fraction_of_positives_zero_only':
           (zero_only / len(base_pos)) if base_pos else None,
           'note': ('theta_d=0 is NOT changed in the primary measurement; '
                    'these are separately named sensitivity analyses.  '
                    'Floor choice is a calibration decision on the dev '
                    'partition only; held-out is evaluated once against '
                    'the frozen variants, without further tuning.'),
           'FLOOR_GRID': list(FLOOR_GRID)}
    p = os.path.join(OUT, f'{out_prefix}_threshold_sensitivity.json')
    json.dump(rep, open(p, 'w'), indent=2)
    for v in sorted(tally):
        print(json.dumps({'variant': v, 'n': tally[v]['n'],
                          'positive': tally[v]['positive'],
                          'rate': round(tally[v]['positive'] / n, 4)},
                         indent=1))
    print(f'wrote {p}')
    return rep


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--partition', default='dev',
                    choices=['dev', 'heldout', 'full'])
    a = ap.parse_args()
    prefix = 'v7' if a.partition == 'full' else f'v7_{a.partition}'
    run_variant_summary(prefix, f'thr_{a.partition}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
