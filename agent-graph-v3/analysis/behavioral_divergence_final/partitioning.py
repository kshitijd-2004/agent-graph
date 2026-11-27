#!/usr/bin/env python3.11
"""
Fresh fixture-level dev / heldout partition (FROZEN_PROTOCOL §1).

Seed  agentprop-final-partition-20261207   (verified unused at seal time)
Rule  every EXECUTION of a fixture stays in one partition (a fixture's benign
      references and its LEP executions cannot straddle the boundary, or
      heldout extraction would be informed by dev inspection of a sibling
      execution).  0.45 of the fixture pool -> heldout by rank.

Contamination control (brief requirement 5: fixture-disjoint from all
development): the heldout pool EXCLUDES every fixture that was inspected in
any prior dev/eval work:
  * the v7 dev + heldout partitions (out/partitions.json, seed
    agentprop-v7-partition-20261123) — v7 extractors were developed on dev
    and its heldout was CONSUMED by the v7 reference eval,
  * the measurement_validation 300-trace sample + 150-pair graph,
  * the v6 case-study fixtures.
Excluded fixtures remain eligible as DEV rows (development use) but never as
heldout rows.  Disjointness is machine-checked and written to the report.

Determinism: fixture rank = sha256(f'{SEED}:{fixture}') — independent of
builtin hash randomization, dict ordering, and process (the v7
partitioning.py convention).

Writes ONLY analysis/behavioral_divergence_final/out/.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import common

SEED = 'agentprop-final-partition-20261207'
HELDOUT_FRACTION = 0.45

FRAME = os.path.join(common.CONS, 'downstream_results', 'per_trace.jsonl')
V7_PARTITIONS = os.path.join(common.V7_DIR, 'out', 'partitions.json')
MV_SAMPLE = os.path.join(common.VALIDATION_ROOT, 'sample_manifest.jsonl')
MV_PAIRS = os.path.join(common.VALIDATION_ROOT, 'pair_manifest.jsonl')


def rank_of(fixture: str) -> int:
    return int(hashlib.sha256(f'{SEED}:{fixture}'.encode()).hexdigest()[:16],
               16)


def _v7_inspected_fixtures() -> set:
    """v7 dev + heldout fixtures (both sides inspected during development)."""
    if not os.path.exists(V7_PARTITIONS):
        return set()
    return set(json.load(open(V7_PARTITIONS))['fixtures'].keys())


def _sample_fixtures() -> set:
    out = set()
    for p in (MV_SAMPLE, MV_PAIRS):
        if not os.path.exists(p):
            continue
        for line in open(p):
            try:
                d = json.loads(line)
            except Exception:
                continue
            for k in ('fixture_id', 'unit_id'):
                v = d.get(k)
                if v:
                    out.add(str(v).split('#')[0].split('.')[0])
            for t in (d.get('trace_ids') or {}).values() if isinstance(
                    d.get('trace_ids'), dict) else (d.get('trace_ids') or []):
                out.add(str(t).split('.')[0])
            # pair manifests: lep + benigns
            for k in ('lep_trace_id', 'benign_trace_ids'):
                vals = d.get(k)
                if isinstance(vals, str):
                    vals = [vals]
                for t in vals or []:
                    if t:
                        out.add(str(t).split('.')[0])
    return out


def _case_study_fixtures() -> set:
    """v6 case-study fixtures (named traces quoted in FINDINGS §E)."""
    import re
    md = os.path.join(common.V6_DIR, 'FINDINGS.md')
    out = set()
    if os.path.exists(md):
        txt = open(md).read()
        for m in re.finditer(r'\b([a-z]+_[a-z]+\d+[^\s`"\']+?\.(?:bv|rl|cw|'
                             r'regen|legacy)\.\d{2}\.(?:std)\.(?:\w+)__\w)\b',
                             txt):
            out.add(m.group(1).split('.')[0])
    return out


def build() -> dict:
    rows = [json.loads(l) for l in open(FRAME) if l.strip()]
    by_fixture: dict = {}
    for r in rows:
        fx = common.fixture_id_of(r['trace_id'])
        by_fixture.setdefault(fx, []).append(r)
    all_fixtures = sorted(by_fixture)

    inspected = (_v7_inspected_fixtures() | _sample_fixtures()
                 | _case_study_fixtures())
    eligible = [f for f in all_fixtures if f not in inspected]
    pool_size = len(eligible)
    n_ho = int(round(HELDOUT_FRACTION * pool_size))

    ranked = sorted(eligible, key=rank_of)
    heldout = set(ranked[:n_ho])          # top-45% ranks -> heldout
    dev = set(ranked[n_ho:])

    # machine-checked disjointness
    leaked = sorted(heldout & inspected)
    assert not leaked, f'heldout not disjoint from inspected fixtures: {leaked[:5]}'

    dev_rows = sum(len(by_fixture[f]) for f in dev)
    ho_rows = sum(len(by_fixture[f]) for f in heldout)

    fixtures_out = {f: {'partition': 'heldout' if f in heldout else 'dev',
                         'rank': rank_of(f), 'n_rows': len(by_fixture[f]),
                         'pool': 'eligible' if f in eligible else 'excluded'}
                    for f in all_fixtures}
    out = {
        'seed': SEED,
        'heldout_fraction_target': HELDOUT_FRACTION,
        'n_fixtures_total': len(all_fixtures),
        'n_fixtures_eligible': pool_size,
        'n_fixtures_inspected_excluded': len(inspected),
        'n_heldout_fixtures': len(heldout),
        'n_dev_fixtures': len(dev),
        'n_heldout_rows': ho_rows,
        'n_dev_rows': dev_rows,
        'n_frame_rows': len(rows),
        'row_note': ('dev+heldout counts cover the ELIGIBLE pool only; '
                     'rows of inspected fixtures are dev-side by construction '
                     'and are reported under n_dev_rows_excluded_below'),
        'n_rows_from_inspected_fixtures': len(rows) - dev_rows - ho_rows,
        'inspected_fixtures': sorted(inspected),
        'fixtures': fixtures_out,
        'disjointness_check': {'leaked': leaked, 'clean': not leaked},
    }
    common.write_json(out, 'partitions_final.json')
    # human-readable report
    rep = [
        f'# Final partition report (seed {SEED})',
        '',
        f'- frame rows: {len(rows)}; fixtures: {len(all_fixtures)}',
        f'- eligible (uninspected) fixtures: {pool_size}; heldout: {len(heldout)} '
        f'({len(heldout) / pool_size:.3f} of pool)',
        f'- rows: dev {dev_rows}, heldout {ho_rows}, '
        f'from inspected fixtures {len(rows) - dev_rows - ho_rows} (dev-side)',
        f'- disjointness vs v7 dev+heldout, measurement_validation sample '
        f'({len(_sample_fixtures())} fixtures), v6 case studies: '
        f'{"CLEAN" if not leaked else leaked}',
    ]
    common.write_text('\n'.join(rep) + '\n', 'partition_report.md')
    print(json.dumps({k: out[k] for k in ('n_fixtures_total',
                                           'n_fixtures_eligible',
                                           'n_heldout_fixtures',
                                           'n_dev_fixtures',
                                           'n_heldout_rows', 'n_dev_rows',
                                           'n_rows_from_inspected_fixtures')},
                     indent=1))
    print('wrote out/partitions_final.json + out/partition_report.md')
    return out


if __name__ == '__main__':
    build()
