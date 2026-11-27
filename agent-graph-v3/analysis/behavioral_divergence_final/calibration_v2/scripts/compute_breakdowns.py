#!/usr/bin/env python3.11
"""M1 full-frame breakdowns + dimension overlap + availability accounting.

Under the FROZEN S1 candidate (ledger/frozen_candidate.json), over P1*
(16,767 unique-trace canonical population):
  * M1 per-dimension rates + union + trigger profiles + 4-way overlap
    counts;
  * breakdowns by task_family × LEP × topology × propagation_mode
    (all 108 strata named; empty cells reported);
  * excess deviation vs the B2B control (LEP rate − probe-union rate,
    CI on the difference);
  * availability / missingness per dimension (P0 = 1,164 named in every
    denominator; P1* vs P1 disclosed);
  * benign failure baseline context (frozen grader artifact, re-used
    not re-computed; context only, never causal).
Writes ledger/production_breakdowns.json.
"""
import json
import math
import os
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
V2 = os.path.abspath(os.path.join(HERE, '..'))
OUT = os.path.join(V2, 'ledger')
sys.path.insert(0, V2)
import cv2  # noqa: E402

DIMS = cv2.DIMS


def main():
    os.makedirs(OUT, exist_ok=True)
    cells, dupes = cv2.load_cells()
    full = cv2.load_full_cells()
    v6dim = cv2.load_v6_dim()
    pools = cv2.build_pools(full)
    pair_pools = cv2.build_pair_pools(full)
    ztab = cv2.shrinkage_weights(full, v6dim, pair_pools, pools)
    outnull = cv2.outcome_null(full)
    rows = cv2.score_candidate(cells, v6dim, pools, ztab, outnull,
                               'S1_shrinkage')
    by_tid = {r['trace_id']: r for r in rows}

    # ── M1 per-dimension + union + overlap ─────────────────────────────
    dim_pos = Counter()
    overlap = Counter()
    for r in rows:
        trig = tuple(sorted(r['triggered']))
        overlap['|'.join(trig) or 'none'] += 1
        for d in r['triggered']:
            dim_pos[d] += 1
    union_n = sum(1 for r in rows if r['union'])

    # ── 108-stratum breakdowns ────────────────────────────────────────
    strat = defaultdict(lambda: {'n': 0, 'union': 0,
                                  'dims': Counter(), 'undet': 0})
    for r in rows:
        key = (r['fam'], r['lep_code'], r['stratum'][1],
               r['stratum'][2])
        s = strat[key]
        s['n'] += 1
        s['union'] += int(r['union'])
        for d in r['triggered']:
            s['dims'][d] += 1
        s['undet'] += int(r['undetermined'])

    # ── availability per dimension (None = undetermined/unevaluable) ──
    avail = {d: {'evaluable': 0, 'undetermined': 0} for d in DIMS}
    for r in rows:
        for d in DIMS:
            if r['fires'].get(d) is None:
                avail[d]['undetermined'] += 1
            else:
                avail[d]['evaluable'] += 1

    # ── excess deviation vs B2B control (probe-union rate) ────────────
    th, _ = cv2.build_policy_theta(full, v6dim, pools, ztab,
                                   'S1_shrinkage')
    b2b = cv2.b2b_control(full, th, outnull)
    lep_rate = union_n / len(rows)
    b2b_rate = b2b['probe_union_fpr']
    excess = lep_rate - b2b_rate
    se = math.sqrt(lep_rate * (1 - lep_rate) / len(rows)
                   + (b2b_rate or 0) * (1 - (b2b_rate or 0))
                   / (b2b['probe_union_n'] or 1))
    excess_ci = [excess - 1.959964 * se, excess + 1.959964 * se]

    # ── fixture-cluster CI on the M1 union rate ───────────────────────
    ci = cv2.union_rate_ci(rows)

    # ── benign failure baseline (frozen grader; context only, never causal;
    #    denominator = GRADEABLE benign grades only — ungradeable is never a
    #    failure (requirement 10)) ────────────────────────────────────
    v6 = cv2._jsonl(cv2.V6_LEDGER)
    baseline = {}
    fams_grade = defaultdict(lambda: [0, 0])
    for r in v6:
        f = r.get('task_family')
        for g in (r.get('benign_grades') or []):
            if g in ('correct', 'incorrect'):
                fams_grade[f][1] += 1
                if g == 'incorrect':
                    fams_grade[f][0] += 1
    for f, (bad, tot) in sorted(fams_grade.items()):
        baseline[f] = {'benign_failure': bad, 'gradeable': tot,
                       'rate': (bad / tot) if tot else None}
    baseline['total'] = {
        'benign_failure': sum(v[0] for v in fams_grade.values()),
        'gradeable': sum(v[1] for v in fams_grade.values()),
        'rate': None}
    tb = baseline['total']
    tb['rate'] = (tb['benign_failure'] / tb['gradeable']) \
        if tb['gradeable'] else None

    rep = {
        'population': {'P1_full': len(full), 'P1star': len(cells),
                       'P0_noncomparable': 1164,
                       'duplicates_excluded': len(dupes)},
        'm1': {
            'union': union_n, 'denominator': len(rows),
            'rate': lep_rate, 'union_ci_fixture_cluster': ci,
            'per_dimension': {d: dim_pos[d] for d in DIMS},
            'per_dimension_rate': {d: dim_pos[d] / len(rows)
                                   for d in DIMS},
            'trigger_profiles': dict(overlap),
            'undetermined_rows': sum(1 for r in rows
                                     if r['undetermined']),
        },
        'excess_deviation': {'lep_rate': lep_rate,
                             'b2b_probe_union_rate': b2b_rate,
                             'excess': excess, 'excess_ci95': excess_ci},
        'b2b_control': b2b,
        'availability': {**avail,
                         'note': ('P0 = 1,164 non-comparable rows are '
                                   'named in every denominator, never '
                                   'forced; undetermined dims are None, '
                                   'not negatives')},
        'strata_108': {
            '%s|%s|%s|%s' % k: {'n': s['n'], 'union': s['union'],
                                 'rate': (s['union'] / s['n'])
                                 if s['n'] else None,
                                 'dims': dict(s['dims']),
                                 'undetermined': s['undet']}
            for k, s in sorted(strat.items())},
        'empty_strata': _missing_strata(cells),
        'benign_failure_baseline_context': baseline,
    }
    json.dump(rep, open(os.path.join(OUT, 'production_breakdowns.json'),
                        'w'), indent=1)
    print('M1 union:', union_n, '/', len(rows), '=', lep_rate,
          'CI', ci)
    print('excess over B2B probe-union:', excess, excess_ci)
    print('per-dim:', dict(dim_pos))
    print('empty strata:', len(rep['empty_strata']))


def _missing_strata(cells):
    fams = ('code_review', 'financial_analysis', 'research_synthesis')
    leps = ('LEP_INDIRECT_PROMPT_INJECTION', 'LEP_HANDOFF_CORRUPTION',
            'LEP_INPUT_DISREGARD', 'LEP_TOOL_RESULT_CORRUPTION')
    topos = ('branch_and_verify', 'coordinator_workers', 'review_loop')
    modes = ('many_to_one', 'one_to_many', 'single_origin')
    present = set()
    for c in cells:
        present.add((c['fam'], c['lep_code'], c['stratum'][1],
                     c['stratum'][2]))
    return [f'{f}|{l}|{t}|{m}' for f in fams for l in leps
            for t in topos for m in modes
            if (f, l, t, m) not in present]


if __name__ == '__main__':
    if 'PYTHONHASHSEED' not in os.environ:
        os.environ['PYTHONHASHSEED'] = '0'
        os.execve(sys.executable, [sys.executable] + sys.argv,
                  os.environ)
    main()
