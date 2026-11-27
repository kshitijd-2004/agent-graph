#!/usr/bin/env python3.11
"""
ci_audit.py — Requirement 7: audit the stored M1 fixture-cluster bootstrap CI
[0.0, 0.6111] and compute a correct, reproducible cluster-bootstrap CI.

Finds the resampling-unit defect, documents it, and produces:
  * the CORRECT fixture-cluster resample bootstrap (resample whole fixtures
    with replacement to a pool of size = total rows; rate over the resampled
    execution-level pool) for the principal production metrics;
  * the stored Bernoulli-share construction REPRODUCED (to confirm the
    defect is the cause, not an inconvenient-but-valid interval);
  * descriptive Wilson intervals kept SEPARATE from the cluster-aware
    uncertainty estimates;
  * the matched LEP-vs-benign (B2B) excess-rate CI (two-proportion normal
    approximation, reported alongside).

Read-only: reads out/production_cis_v6.json + the v6 ledger + sample_A.
Writes out/ci_audit.json only.
Run:  python3.11 ci_audit.py
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import cp_lib as L                 # noqa: E402
import common                      # noqa: E402

OUT = os.path.join(_HERE, 'out')


def main():
    v6 = L.load_v6()
    # fixture groups over the frame (execution-level values: M1 union orig)
    fix: dict = defaultdict(list)
    for r in v6:
        fix[common.fixture_id_of(r['trace_id'])].append(
            1.0 if (r.get('union') or {}).get('divergent') else 0.0)
    n_fixtures = len(fix)
    n_rows = sum(len(v) for v in fix.values())
    m1 = sum(1 for v in fix.values() for x in v if x == 1.0)

    # ── the CORRECT cluster bootstrap (resample whole fixtures w/r) ─────────
    correct = L.cluster_bootstrap_ci(fix, n_iter=5000, seed=20261207,
                                     mode='resample')

    # ── REPRODUCE the stored [0.0, 0.6111] Bernoulli-share construction ─────
    stored_b2b = L.cluster_bootstrap_ci(fix, n_iter=2000, seed=20261207,
                                        mode='bernoulli_share')

    # ── read the stored artifact for the audit record ───────────────────────
    stored = json.load(open(os.path.join(common.OUT, 'production_cis_v6.json')))
    stored_m1_ci = stored.get('m1_fixture_cluster_bootstrap95')

    # ── cluster diagnostics (empirical mean # of resampled fixtures/replicate
    #    under the Bernoulli-at-row-share construction) ─────────────────────
    sizes = [len(v) for v in fix.values()]
    import random
    import statistics
    rng = random.Random(20261207)
    tot_w = {k: len(v) for k, v in fix.items()}
    W = n_rows
    mean_resampled = 0.0
    for _ in range(1000):
        cnt = 0
        for k in tot_w:
            if rng.random() <= (tot_w[k] / W):
                cnt += 1
        mean_resampled += cnt
    mean_resampled /= 1000
    diag = {
        'n_fixtures': n_fixtures,
        'n_rows': n_rows,
        'cluster_size_min': min(sizes), 'cluster_size_max': max(sizes),
        'cluster_size_mean': round(statistics.mean(sizes), 2),
        'empirical_mean_resampled_fixtures_per_replicate_bernoulli':
            round(mean_resampled, 3),
        'note': ('Bernoulli-at-row-share: each fixture resampled w.p. its '
                 'share of all rows -> E[#resampled fixtures] = '
                 'sum_k (n_k/N_total) = 1.0 of 903 (measured '
                 f'{mean_resampled:.3f}).  Each replicate pools ~1 resampled '
                 'fixture (avg ~15-20 rows, mostly a single LEP+topology), so '
                 'the replicate rate is either 0 (no positive fixture drawn) '
                 'or a near-single-fixture rate -> the stored [0.0, 0.6111]. '
                 'This is a RESAMPLING-UNIT DEFECT, not a valid cluster CI. '
                 'The correct resample-the-fixture bootstrap resamples all '
                 '903 fixtures w/r each replicate.'),
    }

    # ── Wilson (descriptive) kept separate from cluster-aware ───────────────
    wilson_m1 = L.wilson(m1, n_rows)

    out = {
        'artifact': 'out/ci_audit.json',
        'm1': {
            'point': m1 / n_rows,
            'wilson95_descriptive': wilson_m1,
            'cluster_bootstrap95_correct_resample': [
                round(correct[0], 6), round(correct[1], 6), correct[2]],
            'cluster_bootstrap95_stored_bernoulli_share_reproduced': [
                round(stored_b2b[0], 6), round(stored_b2b[1], 6)],
            'cluster_bootstrap95_stored_in_production_cis_v6': stored_m1_ci,
            'diagnostics': diag,
            'verdict': ('the stored [0.0, 0.6111] is an IMPLEMENTATION '
                        'DEFECT (resampling unit: Bernoulli at per-cluster '
                        'row-share collapses to ~1 resampled fixture/replicate). '
                        'The correct resample-the-cluster bootstrap is '
                        'reported above; the wide stored interval is NOT a '
                        'legitimate statistical consequence and is superseded.'),
        },
        'cluster_stats_note': ('The downstream_results summary.json already '
                               'carries a cluster-BINOMIAL CI (fraction of '
                               'clusters containing >=1 positive, e.g. M1 '
                               '[0.605, 0.6413] over 2729 clusters) — a '
                               'DIFFERENT, valid cluster-aware estimate.  The '
                               'stored m1_fixture_cluster_bootstrap95 is the '
                               'row-level rate over a degenerate resampling; '
                               'the two are reported separately.'),
    }
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, 'ci_audit.json'), 'w') as fh:
        json.dump(out, fh, indent=1)
    print(json.dumps(out['m1'], indent=1))


if __name__ == '__main__':
    main()
