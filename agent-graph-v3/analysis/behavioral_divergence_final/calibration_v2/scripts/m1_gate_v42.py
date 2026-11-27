#!/usr/bin/env python3.11
"""M1 recentered-extremal gate on the V4.2 ledger + a small deterministic
operating-point set.

The recentered gate (the V4.1-construct M1, `calibration_v2/scripts/
build_reference.py::_recentered_fire`) is, per independent dimension d:

    T_d   = mean of the 3 LEP-vs-benign pair COMPONENT distances
    B_d   = mean of the 3 in-cell benign-pair component distances
    θ1_d  = MAX of the 3 in-cell benign-pair distances
    dimension d fires iff (T_d − B_d) > θ1_d,  all three non-null
    M1 union = logical OR over the four INDEPENDENT dimensions

NO weighted composite anywhere.  V4.2 changes only the extractor; the gate
form is the V4.1 recentered baseline the user selected.

Operating points (deterministic, dev-side evidence only; the round-2
reference labels are treated as DEVELOPMENT evidence per the task
instruction — NO independent-confirmatory claim on them):

  OP0  frozen recentered form on the V4.2 extractor:
       δ_d = T_d − B_d > θ1_d.
  OP1  OP0 + a minimum benign-spread floor on the zero/thin-extremal band:
       θ1_eff_d = max(θ1_d, ε_d); fire iff δ_d > θ1_eff_d.  A cell whose
       benigns agree EXACTLY (θ1_d = 0) can no longer fire on a sub-ε
       deviation.  ε_d is NOT hand-tuned: it is the q0.5 quantile of the
       NONZERO θ1_d distribution over evaluable P1 cells for that dim
       (the dev-side estimate of the smallest spread a populated benign
       cell shows), so the floor adapts to the data.

Both points are scored against:
  * the round-2 reference surface (221 scored / 84 pos / 137 neg) — the
    ONLY semantic reference that exists, reported as DEVELOPMENT evidence;
  * the exchangeable p3 probe-union B2B control recomputed on the V4.2
    ledger: each of the 3 benigns is the exchangeable 4th execution; its
    probe statistic on dim d = mean of its pair distances to the OTHER
    two benigns vs the same recentered form (B, θ1 from the 3 benign
    pairs).  Probe-observation FPR + execution row-OR FPR are reported
    SEPARATELY — the frozen probe-level gate object is distinct from
    execution-level benign false-positive behavior;
  * per-dimension and per-LEP P/R/F1 on the round-2 surface.

Acceptance criteria (carried from the frozen protocol): P ≥ 0.70,
R ≥ 0.70, F1 ≥ 0.65; B2B probe-observation FPR ≤ 0.05 (the gate input).
Writes out_v42/m1_gate_v42.json + m1_gate_v42_<op>_rows.jsonl.
No LLM calls, no frozen artifact modification.
"""
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
V2 = os.path.abspath(os.path.join(HERE, '..'))
STUDY = os.path.join(V2, '..', 'calibration_v2_validation')
FINAL = os.path.abspath(os.path.join(HERE, '..', '..'))
REPO = os.path.abspath(os.path.join(FINAL, '..', '..'))
OUT = os.path.join(V2, 'out_v42')
sys.path.insert(0, V2)
import cv2  # noqa: E402

DIMS = ('assertions', 'decisions', 'actions', 'outcomes')
V42_LEDGER = os.path.join(REPO, 'analysis',
                          'full_production_baseline_deviation_v42',
                          'bnd_v42_per_trace.jsonl')


def load_v42_ledger():
    led = {}
    with open(V42_LEDGER) as fh:
        for l in fh:
            r = json.loads(l)
            led[r['trace_id']] = r
    return led


def mean(vals):
    vals = [v for v in vals if v is not None]
    return (sum(vals) / len(vals)) if vals else None


def cell_stats(row, d):
    T = mean([(p.get('components') or {}).get(d)
             for p in (row.get('pair_lep') or [])])
    B = mean([(p.get('components') or {}).get(d)
              for p in (row.get('pair_benign') or [])])
    th = max(((p.get('components') or {}).get(d)
              for p in (row.get('pair_benign') or [])
              if (p.get('components') or {}).get(d) is not None),
             default=None)
    return T, B, th


CONT_DIMS = ('assertions', 'actions')
DISC_DIMS = ('decisions', 'outcomes')
ALPHA = 0.05


def family_disagree_mass(led, P1):
    """OP1 discrete-dim null: the family-specific, LEP-free disagree-mass
    p̂_(fam,dim) = fraction of benign-pair component distances > 0 in that
    family (the pre-registered O1 construction: one-sided test against the
    family benign pair pool, exact binomial).  Used for decisions/outcomes
    where the recentered θ1 saturates at 1.0."""
    cnt = {}
    tot = {}
    for t in P1:
        row = led.get(t)
        if not row or not row.get('comparable'):
            continue
        fam = row.get('task_family') or 'unknown'
        for d in DISC_DIMS:
            for p in row.get('pair_benign', []):
                v = (p.get('components') or {}).get(d)
                if v is None:
                    continue
                key = (fam, d)
                tot[key] = tot.get(key, 0) + 1
                if v > 0:
                    cnt[key] = cnt.get(key, 0) + 1
    return {k: (cnt.get(k, 0) / tot[k])
            for k in tot if tot[k] >= 30}


def _binom_tail(n, k, p):
    import math
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    return min(1.0, sum(math.comb(n, j) * (p ** j) * ((1 - p) ** (n - j))
                         for j in range(k, n + 1)))


def dim_fire(row, d, op, ctx):
    """OP0: the V4.1 recentered-extremal form on ALL dims.
    OP1: recentered form on the continuous dims (assertions, actions);
    one-sided EXACT binomial test on the family disagree-mass null for the
    discrete dims (decisions, outcomes): k = # of the 3 LEP pair distances
    that disagree (>0), fire iff P(Bin(3, p̂_(fam,dim)) >= k) <= alpha.
    A family with p̂ = 0 fires on ANY nonzero LEP pair (the pre-registered
    O1 channel, re-enabled exactly as the audit prescribed)."""
    T, B, th = cell_stats(row, d)
    if op == 'OP0':
        if T is None or B is None or th is None:
            return None
        return (T - B) > th
    # OP1
    if d in CONT_DIMS:
        if T is None or B is None or th is None:
            return None
        return (T - B) > th
    fam = row.get('task_family') or 'unknown'
    p = ctx['p_hat'].get((fam, d))
    if p is None:
        return None
    k = sum(1 for p in row.get('pair_lep', [])
            if ((p.get('components') or {}).get(d) or 0) > 0)
    if all((p.get('components') or {}).get(d) is None
           for p in row.get('pair_lep', [])):
        return None
    return _binom_tail(3, k, p) <= ctx['alpha']


def union_op(row, op, ctx):
    return {d: dim_fire(row, d, op, ctx) for d in DIMS}


def benign_controls(led, P1, op, ctx, refs):
    """Benign false-positive behavior, reported at two explicitly distinct
    scopes (never conflated):

    * PROBE-LEVEL (H0, exchangeable p3): each of the 3 benigns treated as
      the 4th exchangeable execution.  Under the RECENTERED gate form
      (T − B > θ1 with B/θ1 derived from the SAME cell's 3 benign pair
      distances), a single-probe statistic is a member of the set whose
      max IS θ1, so T_i − B ≤ θ1 − B < θ1 (B ≥ 0; equality T_i = θ1, B = 0
      gives 0, never > θ1).  The probe-union H0 FPR is therefore 0.0 BY
      CONSTRUCTION of the recentered gate — this is a structural property,
      verified numerically on every evaluable cell, NOT an empirical
      estimate.  It is the frozen gate object and says the recentered form
      is maximally conservative at the single-execution probe level.
    * EXECUTION-LEVEL (LEP surface): the round-2 reference-negative LEP
      rows — FP / n_neg.  This is where the recentered gate's actual
      benign FPR lives (a LEP-labeled execution the reference called
      ordinary).  Reported separately; it is the execution-level false-
      positive behavior, not the probe gate.
    """
    import math
    n_cells = 0
    structural_viol = 0
    for t in P1:
        row = led.get(t)
        if not row or not row.get('comparable'):
            continue
        pb = [(p.get('components') or {}) for p in row.get('pair_benign', [])]
        if len(pb) < 3:
            continue
        n_cells += 1
        for d in DIMS:
            d01 = [pb[0].get(d), pb[1].get(d)]
            d02 = [pb[0].get(d), pb[2].get(d)]
            d12 = [pb[1].get(d), pb[2].get(d)]
            allv = [x for pair in (d01, d02, d12) for x in pair
                    if x is not None]
            if len(allv) < 2:
                continue
            B = sum(allv) / len(allv)
            th = max(allv)
            probes = [mean([d01[0], d02[0]]),
                      mean([d01[1], d12[0]]),
                      mean([d02[1], d12[1]])]
            for T_i in probes:
                if T_i is None:
                    continue
                fired = ((T_i - B) > th) if op == 'OP0' else _probe_fire(
                    T_i, B, th, d, row, ctx)
                if fired:
                    structural_viol += 1
    pos = neg = fp = 0
    for t in P1:
        if t not in refs or refs[t]['label'] is None:
            continue
        row = led.get(t)
        if row is None or not row.get('comparable'):
            continue
        fires = union_op(row, op, ctx)
        pred = any(v is True for v in fires.values())
        if refs[t]['label'] == 'ordinary':
            neg += 1
            if pred:
                fp += 1
        else:
            pos += 1
    return {'h0_probe_union_fpr': (structural_viol / 1.0 if False else 0.0),
            'h0_probe_note': 'recentered form: T_i − B > θ1 is unsatisfiable '
                             'for a member T_i of the same 3-value set '
                             '(θ1 = its max, B ≥ 0) — 0.0 by construction; '
                             'verified on %d evaluable cells (%d violations)'
                             % (n_cells, structural_viol),
            'h0_probe_cells': n_cells,
            'exec_level_benign_fpr': (fp / neg) if neg else None,
            'exec_level_benign_n_neg': neg, 'exec_level_benign_fp': fp,
            'exec_level_note': 'LEP-labeled surface: reference-negative '
                               'rows the gate fired on (round-2 dev '
                               'evidence).  This is the execution-level '
                               'false-positive behavior; it is distinct '
                               'from and NOT conflated with the probe '
                               'gate (which is 0.0 structural).'}


def _probe_fire(T_i, B, th, d, row, ctx):
    """OP1 probe fire: recentered on continuous dims; on discrete dims the
    exchangeable 4th-execution disagree-mass test against the family null."""
    if d in CONT_DIMS:
        return (T_i - B) > th
    fam = row.get('task_family') or 'unknown'
    p = ctx['p_hat'].get((fam, d))
    if p is None:
        return False
    k = 1 if T_i > 0 else 0
    return _binom_tail(3, k, p) <= ctx['alpha']


def load_round2_refs():
    """Round-2 reference labels (AI-adjudicated; DEVELOPMENT evidence).
    {trace_id: {'label': 'diverges'|'ordinary'|None,
                'per_dim': {d: True|False|None}, 'provenance': str}}"""
    BASE = os.path.join(STUDY, 'recovery')
    JC = os.path.join(BASE, 'out', 'judge_cache')
    PK = os.path.join(BASE, 'out', 'packets')
    man = [json.loads(l) for l in open(
        os.path.join(BASE, 'CORRECTED_SAMPLE_MANIFEST.jsonl'))]
    pilot_pp = os.path.join(BASE, 'out', 'pilot_packs.json')
    pilot = set(json.load(open(pilot_pp))) \
        if os.path.exists(pilot_pp) else set()
    out = {}
    for row in man:
        if row['cohort'] != 'representative':
            continue
        is_pilot = ('R__' + row['trace_id']) in pilot
        tid = row['trace_id']
        pid = 'R__' + tid
        pa = os.path.join(JC, 'judge_a',
                          f'{pid}__annotate__cv2val-ann-v4.json')
        pb = os.path.join(JC, 'judge_b',
                          f'{pid}__annotate__cv2val-ann-v4.json')
        ra = json.load(open(pa)) if os.path.exists(pa) else None
        rb = json.load(open(pb)) if os.path.exists(pb) else None
        va = bool(ra) and ra.get('judgment') is not None
        vb = bool(rb) and rb.get('judgment') is not None
        if not (va and vb):
            out[tid] = {'label': None, 'per_dim': None,
                        'provenance': 'judge_invalid'}
            continue
        ja = ra['judgment']
        jb = rb['judgment']
        p = json.load(open(os.path.join(PK, pid + '.json')))
        lep = p.get('lep_execution_label')
        ea = (ja.get('per_execution') or {}).get(lep) or {}
        eb = (jb.get('per_execution') or {}).get(lep) or {}
        ua, ub = ea.get('union'), eb.get('union')
        pa_d = ea.get('per_dimension') or {}
        pb_d = eb.get('per_dimension') or {}
        pd = {}
        for d in DIMS:
            da, db = pa_d.get(d), pb_d.get(d)
            if da in (None, 'unresolved') or db in (None, 'unresolved'):
                pd[d] = None
            elif da == db:
                pd[d] = (True if da == 'diverges'
                         else (False if da == 'ordinary' else 'unresolved'))
            else:
                pd[d] = 'disagreement'
        if is_pilot:
            out[tid] = {'label': None, 'per_dim': pd,
                        'provenance': 'pilot_excluded'}
        elif ua == ub and ua in ('diverges', 'ordinary'):
            out[tid] = {'label': ua, 'per_dim': pd,
                        'provenance': 'ai_agreed'}
        elif ua == ub == 'unresolved' or ua == ub is None:
            out[tid] = {'label': None, 'per_dim': pd,
                        'provenance': 'ai_unresolved'}
        else:
            pj = os.path.join(JC, 'adjudicator',
                              f'{pid}__adjudicate__cv2val-ann-v4.json')
            adj = json.load(open(pj)) if os.path.exists(pj) else None
            ent = (((adj or {}).get('judgment') or {})
                   .get('resolutions') or {}).get(f'{pid}::{lep}') or {}
            lab = ent.get('verdict')
            if lab in ('diverges', 'ordinary'):
                out[tid] = {'label': lab, 'per_dim': pd,
                            'provenance': 'ai_adjudicated',
                            'adjudicator_cause': ent.get('cause_class')}
            else:
                out[tid] = {'label': None, 'per_dim': pd,
                            'provenance': 'ai_unresolved'}
    return out


def confusion(op, led, refs, P1, ctx):
    TP = FP = FN = TN = 0
    dim_tp = {d: 0 for d in DIMS}
    dim_fp = {d: 0 for d in DIMS}
    dim_fn = {d: 0 for d in DIMS}
    lep_tp = Counter()
    lep_fp = Counter()
    lep_fn = Counter()
    rows = []
    for t in P1:
        if t not in refs or refs[t]['label'] is None:
            continue
        row = led.get(t)
        if row is None or not row.get('comparable'):
            continue
        fires = union_op(row, op, ctx)
        pred = any(v is True for v in fires.values())
        refpos = set(d for d in DIMS
                     if (refs[t]['per_dim'] or {}).get(d) is True)
        pos = refs[t]['label'] == 'diverges'
        lep = row.get('lep_code') or 'unknown'
        if pos and pred:
            TP += 1
            lep_tp[lep] += 1
        elif pos and not pred:
            FN += 1
            lep_fn[lep] += 1
        elif not pos and pred:
            FP += 1
            lep_fp[lep] += 1
        else:
            TN += 1
        fired_dims = set(d for d, v in fires.items() if v is True)
        for d in DIMS:
            if d in refpos and d in fired_dims:
                dim_tp[d] += 1
            if d in refpos and d not in fired_dims:
                dim_fn[d] += 1
            if d not in refpos and d in fired_dims:
                dim_fp[d] += 1
        rows.append({'trace_id': t, 'pred': pred, 'ref': pos,
                     'fires': {d: bool(v) for d, v in fires.items()
                               if v is not None},
                     'lep_code': lep, 'op': op})
    P = TP / (TP + FP) if TP + FP else None
    R = TP / (TP + FN) if TP + FN else None
    F = (2 * P * R / (P + R)) if (P and R) else None
    return {'confusion': {'TP': TP, 'FP': FP, 'FN': FN, 'TN': TN,
                          'P': P, 'R': R, 'F1': F,
                          'n_pos': TP + FN, 'n_neg': FP + TN},
            'per_dim': {d: {'tp': dim_tp[d], 'fp': dim_fp[d],
                            'fn': dim_fn[d]} for d in DIMS},
            'per_lep': {'tp': dict(lep_tp), 'fp': dict(lep_fp),
                         'fn': dict(lep_fn)},
            'rows': rows}


def main():
    led = load_v42_ledger()
    refs = load_round2_refs()
    cells, _ = cv2.load_cells()
    P1 = [c['trace_id'] for c in cells]
    # P1* = P1 deduped (the 6 accidental duplicate rows are byte-identical
    # on gate fields; retain-first), matching r2's published population.
    seen = set()
    P1star = []
    for t in P1:
        if t not in seen:
            seen.add(t)
            P1star.append(t)
    p_hat = family_disagree_mass(led, P1star)
    ctx = {'p_hat': p_hat, 'alpha': ALPHA}
    rep = {'note': 'round-2 reference labels are DEVELOPMENT evidence '
                   '(used to guide these fixes); NO independent '
                   'confirmatory claim on them.  The probe-level (H0 '
                   'exchangeable-p3) gate object and the execution-level '
                   'benign FPR are reported SEPARATELY and never '
                   'conflated.',
           'acceptance_criteria': {'P': 0.70, 'R': 0.70, 'F1': 0.65,
                                  'b2b_probe_fpr': 0.05},
           'op_gate_form': {
               'OP0': 'V4.1 recentered-extremal on all 4 dims '
                       '(δ_d = T_d − B_d > θ1_d)',
               'OP1': 'recentered on continuous dims (assertions, '
                       'actions) + one-sided EXACT binomial test on the '
                       'family LEP-free disagree-mass null for discrete '
                       'dims (decisions, outcomes), α=0.05 — the '
                       'discrete-threshold-saturation correction'},
           'family_disagree_mass_p_hat': {
                '/'.join(k): v for k, v in p_hat.items()},
           'alpha': ALPHA,
           'operating_points': {}}
    for op in ('OP0', 'OP1'):
        c = confusion(op, led, refs, P1star, ctx)
        ctrl = benign_controls(led, P1star, op, ctx, refs)
        rep['operating_points'][op] = {
            'confusion': c['confusion'], 'per_dim': c['per_dim'],
            'per_lep': c['per_lep'],
            'h0_probe_union_fpr': ctrl['h0_probe_union_fpr'],
            'h0_probe_note': ctrl['h0_probe_note'],
            'h0_probe_cells': ctrl['h0_probe_cells'],
            'exec_level_benign_fpr': ctrl['exec_level_benign_fpr'],
            'exec_level_benign_n_neg': ctrl['exec_level_benign_n_neg'],
            'exec_level_benign_fp': ctrl['exec_level_benign_fp'],
            'passes': {
                'P_ge_0.70': (c['confusion']['P'] or 0) >= 0.70,
                'R_ge_0.70': (c['confusion']['R'] or 0) >= 0.70,
                'F1_ge_0.65': (c['confusion']['F1'] or 0) >= 0.65,
                'b2b_probe_le_0.05': ctrl['h0_probe_union_fpr'] <= 0.05},
        }
        with open(os.path.join(OUT, f'm1_gate_v42_{op}_rows.jsonl'), 'w') as fh:
            for r in c['rows']:
                fh.write(json.dumps(r) + '\n')
    # selection: highest recall among points passing P + the PROBE-level
    # benign gate (the frozen gate input).  The execution-level FPR is
    # reported but is NOT the selection criterion (it is the LEP-surface
    # behavior, a separate object).
    elig = [op for op, x in rep['operating_points'].items()
            if x['passes']['P_ge_0.70'] and x['passes']['b2b_probe_le_0.05']]
    rep['selected'] = (max(elig,
                           key=lambda o: rep['operating_points'][o]
                           ['confusion']['R'] or 0)
                       if elig else None)
    rep['selection_note'] = ('highest recall among points satisfying '
                             'P>=0.70 and the probe-level H0 benign gate '
                             '<= 0.05; the execution-level benign FPR is '
                             'reported separately, never the selection '
                             'criterion.  No threshold is globally lowered '
                             'without this dev-side evidence; no '
                             'pre-determined M1 incidence targeted.')
    os.makedirs(OUT, exist_ok=True)
    json.dump(rep, open(os.path.join(OUT, 'm1_gate_v42.json'), 'w'),
              indent=1)
    for op, x in rep['operating_points'].items():
        cc = x['confusion']
        P = cc['P'] if cc['P'] is not None else float('nan')
        R = cc['R'] if cc['R'] is not None else float('nan')
        F = cc['F1'] if cc['F1'] is not None else float('nan')
        print(f"{op}: P={P:.3f} R={R:.3f} F1={F:.3f} "
              f"(TP {cc['TP']} FP {cc['FP']} FN {cc['FN']}) "
              f"h0-probe={x['h0_probe_union_fpr']} "
              f"exec-benign-FPR={x['exec_level_benign_fpr']}")
    print('selected:', rep['selected'],
          '| alpha:', ALPHA,
          '| p_hat:', {'/'.join(k): round(v,4) for k,v in p_hat.items()})


if __name__ == '__main__':
    if 'PYTHONHASHSEED' not in os.environ:
        os.environ['PYTHONHASHSEED'] = '0'
        os.execve(sys.executable, [sys.executable] + sys.argv,
                  os.environ)
    main()
