#!/usr/bin/env python3.11
"""V4.4 development evaluation on the round-2 surface + the valid
specificity objects (pre-registered candidate set; no post-hoc tuning).

Inputs (all read-only):
  * the V4.4 production ledger (full-corpus recompute — done ONCE, see
    scripts/analyze_baseline_deviation_v44.py);
  * the round-2 reference labels — DEVELOPMENT evidence only (221 scored:
    84 pos / 137 neg).  The labels are NEVER modified; missing dimensional
    attribution on a union-positive row is scored at union level only.

Pre-registered operating points (the small principled candidate set; the
mean statistic is retained for frozen-comparability and the sparse/max
statistic is the dilution-repair candidate — a candidate is only
selected on its matched-null behavior + dev P/R/F1, NEVER because it
merely produces larger distances):

  V44A  self-cell recentered MEAN:   (T_d - B_d) > th_d, OR over 4 dims
       (the V4.3 OP0 gate form on the V4.4 representations — isolates
        the representation/evidence-scope corrections from the
        statistic change)
  V44B  self-cell recentered MAX:    (max_p T_p - B_d) > th_d, strict >
       (the anti-dilution within-dimension statistic; strict, per the
        G2/G2s ablation — tie-inclusive >= collapsed specificity)
  V44C  cross-fixture q98 family threshold (fixture-disjoint LOO of the
        LEP-free probe pool), MEAN statistic
  V44D  cross-fixture q98 family threshold, MAX statistic
  V44E  V44B on the recentered continuous dims + the discrete-support
        null on decisions/outcomes (recorded integer disagree counts,
        one-sided empirical tail on the LEP-free family k pool; the
        binomial-count reconstruction is NOT used); saturated self-cell
        dims are NAMED uncalibrated, not silently dropped.

Every candidate is scored with:
  * the round-2 dev confusion (P/R/F1; per-dim; per-LEP) — the 84/137
    semantic surface, development evidence, no confirmatory claim;
  * THREE SEPARATELY-NAMED specificity objects (the V4.3 circular
    control is replaced, not re-labeled):
      S1 structural in-cell probe OR — the 4th-exchangeable-benign
         probe under the self-cell recentered form cannot exceed its
         own threshold; verified numerically on every evaluable P1
         cell and reported as a property (0 violations expected);
      S2 cross-fixture execution-level OR — probes vs the fixture-
         disjoint family threshold over the FULL P1 population (the
         object that bounds an UNSEEN execution; the <=5% target is
         read off here, with a fixture-cluster bootstrap CI and a
         Wilson CI);
      S3 semantic dev FPs — FP/137 on the round-2 reference-negatives
         (imperfect labels; reported, never a specificity certificate);
  * the CORRECTED signal-availability ceiling (nonzero distance vs
    positive EXCESS T > B vs achievable; the V4.3 ceiling's
    not-both-zero bug is fixed).

Selection (pre-registered): among candidates with NO S1 violation and
dev P>=0.70 & R>=0.70 & F1>=0.65, the highest dev recall, tie-broken by
F1.  If no candidate satisfies the constraints, the candidate with the
best (P>=0.70, then F1) is reported as the strongest DEFENSIBLE point
with its failures NAMED; if S2 does not support <=5%, the specificity
claim is explicitly qualified ("the available benign repetitions do
not support a <=5% execution-level FPR; the cross-fixture estimate is
X with CI Y — independent benign repetitions are required for the
target").  Writes out_v44/dev_eval_v44.json + per-trace rows.
"""
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', 'calibration_v2', 'scripts'))
import m44  # noqa: E402

OUT = os.path.join(HERE, 'out_v44')


def load_round2_refs():
    """The round-2 reference labels (frozen dev evidence; the V4.3 loader
    is reused read-only — its inputs are the judge caches + corrected
    sample manifest, all frozen)."""
    import importlib.util as ilu
    spec = ilu.spec_from_file_location(
        'm1g43', os.path.join(
            m44.FINAL, 'calibration_v2', 'scripts', 'm1_gate_v43.py'))
    g = ilu.module_from_spec(spec)
    sys.modules['m1g43'] = g
    spec.loader.exec_module(g)
    return g.load_round2_refs()


OPS = ('V44A', 'V44B', 'V44C', 'V44D', 'V44E')


def op_gate(op, row, thr):
    """The pre-registered per-dimension + logical-OR gate forms."""
    fam = row.get('task_family') or 'unknown'
    fx = m44.fixture_of(row['trace_id'])
    fires: dict = {}
    states: dict = {}
    if op == 'V44E':
        CONT = ('assertions', 'actions')   # continuous channels
        DISC = ('decisions', 'outcomes')   # discrete-support channels
        for d in m44.DIMS:
            s = m44.cell_stats(row, d)
            if d in CONT:
                # continuous dims: the anti-dilution max form (V44B)
                if s['T_max'] is None or s['B'] is None or s['th'] is None:
                    fires[d] = None
                    states[d] = 'uncalibrated'
                    continue
                fires[d] = bool((s['T_max'] - s['B']) > s['th'])
                states[d] = ('saturated' if s['saturated']
                             else ('below' if not fires[d] else ''))
            else:
                # discrete dims: the recorded integer count vs the
                # LEP-free family support pool (one-sided empirical
                # tail on the actual support; ties counted; the
                # binomial reconstruction is NOT used).  A saturated
                # self-cell channel is NAMED uncalibrated here — the
                # pool null is the channel, not the saturated th.
                fires[d] = m44.discrete_null_fires(fam, d, s['k'],
                                                   POOLS_DISC)
                states[d] = (None if fires[d] is None
                             else ('below' if not fires[d] else ''))
        pos = [d for d in m44.DIMS if fires.get(d) is True]
        ev = [d for d in m44.DIMS if fires.get(d) is not None]
        return {'fires': fires, 'union': bool(pos) if ev else None,
                'fired_dims': pos, 'n_eval': len(ev), 'states': states,
                'undetermined': (not ev)}
    stat = 'max' if op in ('V44B', 'V44D') else 'mean'
    fam_thr = op in ('V44C', 'V44D')
    return m44.gate_fires(row, stat, thr, family_threshold=fam_thr)


def confusion(rows, refs, op, thr):
    TP = FP = FN = TN = 0
    per_dim = {d: Counter() for d in m44.DIMS}
    per_lep = Counter()
    missed, fired_neg = [], []
    for t, ref in refs.items():
        row = rows.get(t)
        if row is None or not row.get('comparable'):
            continue
        if ref.get('label') is None:
            continue
        g = op_gate(op, row, thr)
        pred = g['union'] is True
        pos = ref['label'] == 'diverges'
        refpos = set(d for d in m44.DIMS
                     if (ref.get('per_dim') or {}).get(d) is True)
        lep = row.get('lep_code') or 'unknown'
        if pos and pred:
            TP += 1
            per_lep[('tp', lep)] += 1
        elif pos:
            FN += 1
            per_lep[('fn', lep)] += 1
            missed.append(t)
        elif pred:
            FP += 1
            per_lep[('fp', lep)] += 1
            fired_neg.append(t)
        else:
            TN += 1
        fired_dims = set(g['fired_dims'])
        for d in m44.DIMS:
            if d in refpos and d in fired_dims:
                per_dim[d]['tp'] += 1
            elif d in refpos:
                per_dim[d]['fn'] += 1
            elif d in fired_dims:
                per_dim[d]['fp'] += 1
    P = TP / (TP + FP) if TP + FP else None
    R = TP / (TP + FN) if TP + FN else None
    F = (2 * P * R / (P + R)) if (P and R and (P + R)) else None
    return {'TP': TP, 'FP': FP, 'FN': FN, 'TN': TN,
            'P': P, 'R': R, 'F1': F,
            'n_pos': TP + FN, 'n_neg': FP + TN,
            'per_dim': {d: dict(per_dim[d]) for d in m44.DIMS},
            'per_lep': {k[1]: {'tp': per_lep.get(('tp', k[1]), 0),
                               'fp': per_lep.get(('fp', k[1]), 0),
                               'fn': per_lep.get(('fn', k[1]), 0)}
                        for k in per_lep},
            'missed': missed, 'fired_neg': fired_neg}


POOLS_DISC: dict = {}


def main():
    global POOLS_DISC
    led = m44.load_ledger()
    refs = load_round2_refs()
    scored = [t for t, r in refs.items() if r.get('label') is not None]
    P1 = [t for t, r in led.items() if r.get('comparable')]
    pools = m44.build_pools(led, P1)
    THRESH = m44.Thresholds(pools)
    POOLS_DISC = m44.discrete_support_pools(led)

    rep = {'note': 'round-2 references are DEVELOPMENT evidence (used to '
                   'guide the V4.4 corrections; NO independent '
                   'confirmatory claim on them).  The three specificity '
                   'objects are reported SEPARATELY and never '
                   'conflated: S1 structural (a property, verified), '
                   'S2 cross-fixture execution-level OR (the target '
                   '<=5% object, where supportable), S3 semantic dev '
                   'FPs (imperfect labels).',
           'dev_surface': {'n_scored': len(scored),
                          'n_pos': sum(1 for t in scored
                                       if refs[t]['label'] == 'diverges'),
                          'n_neg': sum(1 for t in scored
                                       if refs[t]['label'] == 'ordinary')},
           'acceptance_criteria': {'P': 0.70, 'R': 0.70, 'F1': 0.65,
                                   'exec_level_fpr_target': 0.05},
           'ops': {}}
    for op in OPS:
        c = confusion(led, refs, op, THRESH)
        rep['ops'][op] = c
    rep['s1_structural_incell_probe_or'] = m44.s1_structural(led)
    rep['s2_cross_fixture_exec_or'] = m44.s2_cross_fixture(led, pools)
    rep['s3_note'] = 'S3 = the dev-FP column of each op (FP/137), ' \
        'reported with its Wilson CI; it is the LEP-surface behavior on ' \
        'imperfect labels, NOT a benign specificity certificate.'
    for op, c in rep['ops'].items():
        c['wilson95_fp'] = (m44.wilson(c['FP'], c['n_neg'])
                            if c['n_neg'] else None)

    # corrected ceiling
    rep['corrected_ceiling'] = m44.corrected_ceiling(led, refs)

    # pre-registered selection
    elig = []
    for op in OPS:
        c = rep['ops'][op]
        ok = (c['P'] is not None and c['P'] >= 0.70
              and c['R'] is not None and c['R'] >= 0.70
              and c['F1'] is not None and c['F1'] >= 0.65)
        elig.append((ok, op))
    passing = [op for ok, op in elig if ok]
    if passing:
        sel = max(passing,
                  key=lambda o: (rep['ops'][o]['R'] or 0,
                                 rep['ops'][o]['F1'] or 0))
        sel_note = ('highest dev recall among P>=0.70 & R>=0.70 & '
                    'F1>=0.65 candidates (tie: F1)')
    else:
        sel = max(OPS,
                  key=lambda o: ((rep['ops'][o]['P'] or 0) >= 0.70,
                                 rep['ops'][o]['F1'] or 0))
        sel_note = ('NO candidate satisfies all dev constraints; the '
                    'strongest defensible point is reported with its '
                    'failures named (no forced passing operating '
                    'point)')
    s2 = rep['s2_cross_fixture_exec_or']
    s2_rate = s2['rate'] or 0.0
    spec_ok = s2_rate <= 0.05
    rep['selected'] = sel
    rep['selection_note'] = sel_note
    ci = [round(x, 4) for x in s2['fixture_cluster_bootstrap95']]
    rep['specificity_verdict'] = {
        'exec_level_fpr_target': 0.05,
        's2_cross_fixture_rate': s2_rate,
        'ci': ci,
        'supported': spec_ok}
    if spec_ok:
        rep['specificity_verdict']['note'] = (
            'cross-fixture execution-level OR supports the <=5% target: '
            'rate %.4f, CI %s' % (s2_rate, ci))
    else:
        rep['specificity_verdict']['note'] = (
            'the available benign repetitions DO NOT support the '
            'execution-level FPR target (<=0.05): the cross-fixture '
            'estimate is %.4f (CI %s); an independent benign repetition '
            'set (a new fixture population without reused reference '
            'sets) is required before the target can be claimed.  The '
            'specific estimate is reported, never manufactured.'
            % (s2_rate, ci))
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, 'dev_eval_v44.json'), 'w') as fh:
        json.dump(rep, fh, indent=1, sort_keys=True)
    # per-trace rows for the selected op
    selrows = []
    for t in scored:
        row = led.get(t)
        if row is None or not row.get('comparable'):
            continue
        g = op_gate(sel, row, THRESH)
        selrows.append({'trace_id': t, 'op': sel,
                        'fires': {d: g['fires'][d] for d in m44.DIMS},
                        'states': g['states'],
                        'union': g['union'],
                        'ref': refs[t]['label'],
                        'provenance': refs[t].get('provenance')})
    with open(os.path.join(OUT, f'dev_rows_{sel}.jsonl'), 'w') as fh:
        for r in selrows:
            fh.write(json.dumps(r, sort_keys=True) + '\n')
    for op in OPS:
        c = rep['ops'][op]
        print('%s: P=%.3f R=%.3f F1=%.3f (TP%d FP%d FN%d)' % (
            op, c['P'] or 0, c['R'] or 0, c['F1'] or 0,
            c['TP'], c['FP'], c['FN']))
    print('S1 structural violations:',
          rep['s1_structural_incell_probe_or']['violations'])
    print('S2 cross-fixture exec-OR:', round(s2_rate, 4),
          'CI', [round(x, 4) for x in s2['fixture_cluster_bootstrap95']])
    print('selected:', sel, '|', sel_note)
    print('specificity supported:', spec_ok)
    print('ceiling:', rep['corrected_ceiling'])
    print('wrote', OUT)


if __name__ == '__main__':
    if 'PYTHONHASHSEED' not in os.environ:
        os.environ['PYTHONHASHSEED'] = '0'
        os.execve(sys.executable, [sys.executable] + sys.argv,
                  os.environ)
    main()
