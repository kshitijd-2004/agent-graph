#!/usr/bin/env python3.11
"""Fixture-level dev/confirmatory split + reference-independence audit.

The 342 ELIGIBLE-DEV fixtures (pool=eligible, partition=dev in
partitions_final.json) were inspected by NO reference build (heldout =
280 eligible-heldout, v7 = 281 inspected) — so they are the only
genuinely un-inspected pool.  We split them at fixture level
(0.55 confirmatory / 0.45 development) with the fresh seed
`agentprop-cv2-devsplit-202702`.  A new seed alone is NOT independence;
what matters is the fixture set is un-inspected, which the audit below
proves.  The confirmatory partition is then used for the single
confirmatory evaluation; the development partition for candidate
selection.  The historical heldout + v7 pools are EXCLUDED from any new
claim (retrospective cross-checks only).

Writes ledger/partition_split.json + ledger/reference_independence.json.
"""
import hashlib
import json
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
V2 = os.path.abspath(os.path.join(HERE, '..'))
FINAL = os.path.abspath(os.path.join(HERE, '..', '..'))
REPO = os.path.abspath(os.path.join(FINAL, '..', '..'))
OUT = os.path.join(V2, 'ledger')
sys.path.insert(0, V2)
SEED = 'agentprop-cv2-devsplit-202702'
HO_FRAC = 0.55  # confirmatory fraction of the eligible-dev pool


def _rank(seed: str, key: str) -> int:
    return int(hashlib.sha256((seed + key).encode()).hexdigest()[:20], 16)


def main():
    os.makedirs(OUT, exist_ok=True)
    parts = json.load(open(os.path.join(FINAL, 'out', 'partitions_final.json')))
    fxts = parts['fixtures']
    inspected = set(parts.get('inspected_fixtures', []))
    v7 = json.load(open(os.path.join(REPO, 'analysis', 'measurement_v7',
                                     'out', 'partitions.json')))
    v7f = set((v7.get('fixtures') or {}).keys())

    eligible_dev = sorted(k for k, v in fxts.items()
                          if v.get('pool') == 'eligible'
                          and v.get('partition') == 'dev')
    heldout = [k for k, v in fxts.items()
               if v.get('pool') == 'eligible' and v.get('partition') == 'heldout']

    # independent?  cross-check vs every inspected pool
    indep = {
        'eligible_dev': eligible_dev,
        'n_eligible_dev': len(eligible_dev),
        'overlap_with_v7_inspected': sorted(set(eligible_dev) & inspected),
        'overlap_with_v7_fixtures': sorted(set(eligible_dev) & v7f),
        'overlap_with_heldout': sorted(set(eligible_dev) & set(heldout)),
    }
    assert not indep['overlap_with_v7_inspected']
    assert not indep['overlap_with_heldout']

    # fixture-level 0.55/0.45 split of the eligible-dev pool (deterministic
    # rank by seed; a fixture stays whole — all its executions land in one
    # partition; the confirmatory split never re-uses the historical
    # heldout or the v7 inspected pool)
    ranked = sorted(eligible_dev, key=lambda f: _rank(SEED, f))
    n_confirm = int(round(HO_FRAC * len(ranked)))
    confirm_fx = ranked[:n_confirm]
    dev_fx = ranked[n_confirm:]
    split = {
        'seed': SEED,
        'confirm_fraction_target': HO_FRAC,
        'n_eligible_dev': len(eligible_dev),
        'n_confirm_fixtures': len(confirm_fx),
        'n_dev_fixtures': len(dev_fx),
        'confirm_fixtures': confirm_fx,
        'dev_fixtures': dev_fx,
        'note': ('confirmatory = un-inspected eligible-dev fixtures split '
                 '0.55; the development partition is used ONLY for '
                 'candidate selection (pre-registration §4); the '
                 'confirmatory partition is consumed exactly once, at the '
                 'final evaluation, and never for operating-point choice'),
    }
    json.dump(split, open(os.path.join(OUT, 'partition_split.json'), 'w'),
              indent=1)
    # map fixtures -> P1* trace_ids so the reference surface is known
    cells, _ = __import__('cv2').load_cells()
    fx_to_rows = defaultdict(list)
    for c in cells:
        fx_to_rows[c['trace_id'].split('.')[0]].append(c['trace_id'])
    confirm_rows = sorted(t for f in confirm_fx for t in fx_to_rows.get(f, []))
    dev_rows = sorted(t for f in dev_fx for t in fx_to_rows.get(f, []))
    split['n_confirm_rows'] = len(confirm_rows)
    split['n_dev_rows'] = len(dev_rows)
    split['confirm_rows'] = confirm_rows
    split['dev_rows'] = dev_rows
    json.dump(split, open(os.path.join(OUT, 'partition_split.json'), 'w'),
              indent=1)

    json.dump(indep, open(os.path.join(OUT, 'reference_independence.json'),
                          'w'), indent=1)
    print('eligible-dev fixtures:', len(eligible_dev),
          '-> confirm', len(confirm_fx), 'dev', len(dev_fx))
    print('confirm rows:', len(confirm_rows), 'dev rows:', len(dev_rows))
    print('independence: v7-inspected overlap',
          len(indep['overlap_with_v7_inspected']),
          '| heldout overlap', len(indep['overlap_with_heldout']))


if __name__ == '__main__':
    main()
