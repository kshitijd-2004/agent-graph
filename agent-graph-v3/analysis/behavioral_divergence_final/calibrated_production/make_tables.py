#!/usr/bin/env python3.11
"""
make_tables.py — Requirement 5 (full breakdowns) + Requirement 4 headline
suites, all re-derived from the per-execution ledger (independent of the
extraction caches).  Produces machine-readable tables under out/:

  out/tables_suites.json      — M1/M2/M3/M4 suites, both operating rules
  out/tables_breakdowns.json  — family x LEP x topology x mode (valid combos)
  out/tables_side_by_side.json — original-vs-calibrated flip reconciliation
  out/tables_cis.json         — Wilson + correct cluster bootstrap + excess CIs

Read-only over the ledger; writes only into out/.  Run:
  python3.11 make_tables.py
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from typing import Dict, List

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import cp_lib as L                 # noqa: E402

OUT = os.path.join(_HERE, 'out')

#: the supported topology x propagation-mode combinations (5 of the 9).  The
#: corpus only instantiates these; empty/structurally-invalid cells are named,
#: never filled with invented observations.
SUPPORTED_COMBOS = [
    ('branch_and_verify', 'many_to_one'),
    ('branch_and_verify', 'single_origin'),
    ('coordinator_workers', 'one_to_many'),
    ('coordinator_workers', 'single_origin'),
    ('review_loop', 'single_origin'),
]


def _load():
    ledger = [json.loads(l) for l in open(os.path.join(OUT,
                                                       'ledger_v6_calibrated.jsonl'))
              if l.strip()]
    core = json.load(open(os.path.join(OUT, 'metrics_core.json')))
    return ledger, core


def _grp_cells(ledger):
    """group key -> list of rows, over the 5 supported topo x mode combos."""
    cells: Dict[str, List[dict]] = defaultdict(list)
    for r in ledger:
        key = f"{r['task_family']}/{r['lep_code']}/" \
              f"{r['topology']}/{r['propagation_mode']}"
        cells[key].append(r)
    return cells


def suite_block(rows: List[dict]) -> Dict[str, any]:
    """M1/M2/M3/M4 counts for a population of ledger rows (both rules)."""
    n = len(rows)
    m1 = {
        'union_orig': sum(1 for r in rows if r['m1_union_orig']),
        'union_cal': sum(1 for r in rows if r['m1_union_cal']),
        'union_orig_zero_only': sum(1 for r in rows if r['m1_zero_only_orig']),
        'dims_cal': {d: sum(1 for r in rows if r['m1_dims_cal'][d])
                     for d in L.DIMS},
        'dims_orig': {d: sum(1 for r in rows if r['m1_dims_orig'][d])
                      for d in L.DIMS},
        'dim_unavailable_cal': {
            d: sum(1 for r in rows if not r['m1_dim_eval_cal'][d]) for d in L.DIMS},
        'dim_unavailable_orig': {
            d: sum(1 for r in rows if not r['m1_dim_eval_orig'][d])
            for d in L.DIMS},
    }
    m2 = {
        'cross_agent_orig': sum(1 for r in rows if r['m2_cross_agent']),
        'cross_agent_nonzero': sum(1 for r in rows
                                   if r['m2_cross_agent']
                                   and r['m2_nonzero_threshold']),
        'cross_agent_cal_true': sum(1 for r in rows
                                    if r['m2_cross_agent_cal'] is True),
        'cross_agent_cal_undetermined': sum(1 for r in rows
                                            if r['m2_cross_agent_cal'] is None),
        'diagnostic_m2_orig_AND_m1_cal': sum(
            1 for r in rows if r['m2_cross_agent'] and r['m1_union_cal']),
    }
    m3 = {
        'supported': sum(1 for r in rows if r['m3_reach_class']
                         in ('verified', 'plausible')),
        'verified': sum(1 for r in rows if r['m3_reach_class'] == 'verified'),
        'plausible': sum(1 for r in rows if r['m3_reach_class']
                         == 'plausible'),
        'unsupported': sum(1 for r in rows if r['m3_reach_class']
                           == 'unsupported'),
        'matched': sum(1 for r in rows if r['m3_reach_class']
                       in ('verified', 'plausible') and r['m4_matched']),
    }
    m4classes = defaultdict(int)
    for r in rows:
        m4classes[r['m4_class']] += 1
    return {'n': n, 'm1': m1, 'm2': m2, 'm3': m3,
            'm4_class_counts': dict(m4classes)}


def side_by_side(rows: List[dict]) -> Dict[str, int]:
    """How many executions stay positive / become negative / become
    unavailable when moving from the ORIGINAL (frozen per-cell max theta) to
    the CALIBRATED (p3_q98_f0_family) operating rule, on M1.  'become_'
    transitions are conditioned on the original state; an original-negative
    row that is calibrated-undetermined stays in the unavailable bucket,
    NEVER a negative."""
    out = {
        'orig_pos_cal_pos': 0, 'orig_pos_cal_neg': 0,
        'orig_pos_cal_unavail': 0,
        'orig_neg_cal_pos': 0, 'orig_neg_cal_neg': 0,
        'orig_neg_cal_unavail': 0,
        'n': 0,
    }
    for r in rows:
        o, c = r['m1_union_orig'], r['m1_union_cal']
        st = r['m1_cal_state']
        out['n'] += 1
        col = 'pos' if c else ('unavail' if st == 'unavail' else 'neg')
        out[f'{"orig_pos" if o else "orig_neg"}_cal_{col}'] += 1
    # convenience aggregates used by the report
    out['stay_positive'] = out['orig_pos_cal_pos']
    out['become_negative'] = out['orig_pos_cal_neg']
    out['become_unavailable'] = out['orig_pos_cal_unavail']
    out['new_positive'] = out['orig_neg_cal_pos']
    out['stay_negative_or_unavail'] = out['orig_neg_cal_neg'] + \
        out['orig_neg_cal_unavail']
    return out


def main():
    ledger, core = _load()
    os.makedirs(OUT, exist_ok=True)

    # ── headline suites (both rules) ────────────────────────────────────────
    suites = {
        'frame': suite_block(ledger),
        'by_family': {f: suite_block([r for r in ledger if r['task_family']
                                      == f]) for f in L.FAM},
        'by_lep': {l: suite_block([r for r in ledger if r['lep_code'] == l])
                   for l in L.LEP},
        'by_topology': {t: suite_block([r for r in ledger
                                        if r['topology'] == t])
                        for t in L.TOPO},
        'by_mode': {m: suite_block([r for r in ledger
                                    if r['propagation_mode'] == m])
                    for m in L.MODE},
        'by_dimension_cal_pos': {
            d: {
                'n_rows_triggered': core['m1']['dims_cal'][d],
                'n_rows': len(ledger),
                'note': 'rows with a positive indicator on calibrated dim '
                        f'{d}',
            } for d in L.DIMS},
    }

    # per-dimension overlap + trigger profiles (calibrated)
    trig: Dict[str, int] = defaultdict(int)
    overlap_pairs = defaultdict(int)
    for r in ledger:
        tc = [d for d in L.DIMS if r['m1_dims_cal'][d]]
        if tc:
            trig[','.join(tc)] += 1
        for i, a in enumerate(L.DIMS):
            for b in L.DIMS[i + 1:]:
                if a in tc and b in tc:
                    overlap_pairs[f'{a}+{b}'] += 1
    suites['trigger_profiles_cal'] = dict(trig)
    suites['pairwise_overlap_cal'] = dict(overlap_pairs)

    # ── 60-cell breakdowns (the 5 supported topo×mode combos; the 48
    #    structurally-invalid combos are EXCLUDED, not filled) ─────────────
    cells = _grp_cells(ledger)
    valid = set(f'{f}/{l}/{t}/{m}'
                for f in L.FAM for l in L.LEP
                for (t, m) in SUPPORTED_COMBOS)
    empty = [k for k in valid if not cells.get(k)]
    # the 4 structurally-invalid topo×mode combos (of the 9) and their
    # 3 fam x 4 LEP x 4 invalid-combo = 48 cell coordinates, named for
    # completeness so they are not misread as empty VALID cells.
    all_combos = [(t, m) for t in L.TOPO for m in L.MODE]
    invalid_combos = [list(x) for x in all_combos if x not in SUPPORTED_COMBOS]
    breakdowns = {
        'supported_topo_mode_combos': [list(x) for x in SUPPORTED_COMBOS],
        'invalid_topo_mode_combos_named': invalid_combos,
        'n_invalid_cells_excluded': len(invalid_combos) * len(L.FAM) * len(L.LEP),
        'cell_count_nonempty': len(valid) - len(empty),
        'cell_count_total_valid': len(valid),
        'empty_valid_cells_named': empty,
        'note': ('108 = 3 fam x 4 LEP x 9 topo-mode combos.  Only the 5 '
                 'supported topo-mode combos occur in the corpus, so the '
                 'measurement surface is 60 valid cells (all non-empty).  '
                 'The 48 cell coordinates over the 4 structurally-invalid '
                 'combos carry NO observations and are EXCLUDED, never '
                 'invented.'),
        'cells': {k: suite_block(v) for k, v in cells.items() if v},
    }

    # ── side-by-side original vs calibrated ────────────────────────────────
    sbs = {
        'frame': side_by_side(ledger),
        'by_family': {f: side_by_side([r for r in ledger
                                       if r['task_family'] == f])
                      for f in L.FAM},
        'note': 'M1 union flips.  "become_unavailable" = a row whose every '
                'calibrated dimension threshold is None (LEP statistic not '
                'evaluable on at least one side OR no pooled threshold); '
                'these are NEVER counted as calibrated negatives.',
    }

    # ── CIs: Wilson (descriptive) + correct cluster bootstrap + excess ─────
    fix: Dict[str, List[float]] = defaultdict(list)
    for r in ledger:
        fix[r['fixture_id']].append(
            1.0 if r['m1_union_cal'] else 0.0)
    fix_o: Dict[str, List[float]] = defaultdict(list)
    for r in ledger:
        fix_o[r['fixture_id']].append(1.0 if r['m1_union_orig'] else 0.0)
    # recompute the two-proportion excess CI directly from core
    k1, n1 = core['m1']['union_cal'], len(ledger)
    k2, n2 = core['m1']['b2b_fpr_num'], core['m1']['b2b_fpr_den']
    cis = {
        'm1_cal': {
            'point': k1 / n1,
            'wilson95_descriptive': L.wilson(k1, n1),
            'cluster_bootstrap95_resample': L.cluster_bootstrap_ci(
                fix, n_iter=5000, seed=20261207, mode='resample'),
        },
        'm1_orig': {
            'point': core['m1']['union_orig'] / n1,
            'wilson95_descriptive': L.wilson(core['m1']['union_orig'], n1),
            'cluster_bootstrap95_resample': L.cluster_bootstrap_ci(
                fix_o, n_iter=5000, seed=20261207, mode='resample'),
        },
        'b2b_fpr_cal': {
            'point': (k2 / n2),
            'wilson95_descriptive': L.wilson(k2, n2),
            'note': 'denominator is CONSTRUCTED probe observations '
                    f'({n2} p3 probes across the frame = 3 x 16,773), NOT '
                    'independent benign executions.  Wilson treats them as '
                    'exchangeable probes; cluster awareness is the p3-null '
                    'construction itself.',
        },
        'lep_minus_b2b_excess_ci95': L.excess_ci(k1, n1, k2, n2),
        'note': 'Descriptive Wilson intervals (execution-level binomial) are '
                'kept SEPARATE from the cluster-bootstrap uncertainty '
                'estimates.  The excess CI is the two-proportion normal '
                'approximation on the LEP-vs-benign difference.',
    }

    for name, obj in [('tables_suites.json', suites),
                      ('tables_breakdowns.json', breakdowns),
                      ('tables_side_by_side.json', sbs),
                      ('tables_cis.json', cis)]:
        with open(os.path.join(OUT, name), 'w') as fh:
            json.dump(obj, fh, indent=1)
    print(f'[tables] frame M1 orig {core["m1"]["union_orig"]} '
          f'/ cal {core["m1"]["union_cal"]}; M2 orig {core["m2"]["cross_agent"]} '
          f'/ cal-true {core["m2"]["cross_agent_cal_true"]} '
          f'(undet {core["m2"]["cross_agent_cal_undetermined"]}); '
          f'M3 matched {core["m3"]["matched"]}/{core["m3"]["supported"]}')
    print('[tables] wrote suites / breakdowns / side_by_side / cis')


if __name__ == '__main__':
    main()
