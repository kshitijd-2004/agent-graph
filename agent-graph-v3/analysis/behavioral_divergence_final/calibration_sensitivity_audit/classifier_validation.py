#!/usr/bin/env python3.11
"""Evaluate BOTH operating rules against the applicable independent
reference labels (audit requirement 3).

Reference surface (the ONLY classification-level reference that exists):
  out/reference_heldout_pairs.jsonl — 216 heldout LEP-vs-benign pairs
  (2 LEP references each) with an independently adjudicated UNION verdict
  (`union_ref`: True/False, or None = unresolved -> EXCLUDED, never a
  forced negative) + per-dimension `resolved_dims`.  Provenance: the
  frozen judge layer (2 frontier judges + adjudicator, blinded) + the
  independent deterministic layers (RT-1..6 repaired localizer,
  calibrated actions).

Construct note (the audit's critical check, requirement 3): the pair
reference measures OBSERVABLE LEP-vs-its-first-benign-reference
divergence (single-pair, semantic).  The calibrated operating rule
measures ANOMALY relative to a pooled benign null (multi-run,
statistical).  The two are related but NOT the same construct; a
reference label of "the executions differ" is not a label of "the
execution is anomalous vs repeated benign executions".  This script
therefore reports, separately and explicitly:
  (a) union P/R/F1 of each rule against the pair reference (observable-
      divergence construct — applies to the ORIGINAL rule by construction;
      a SENSITIVITY-against-observable-divergence measure for the
      CALIBRATED rule);
  (b) the same on the benign-to-benign control class (where the
      calibrated rule's FPR is the meaningful quantity, and the pair
      reference's 'diverges' label is NOT a false-positive signal —
      benign runs differ too; reported separately, never mixed);
  (c) per-dimension P/R/F1 against the resolved dimension labels;
  (d) stratification by pair_class, task_family, LEP code (sample sizes
      named; strata with <30 evaluable are marked low-power).
No label is fabricated; unresolved pairs stay excluded.
Writes ledger/classifier_validation.json.
"""
import json
import math
import os
import random
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audit_lib as A

OUT = os.path.join(A.AUDIT, 'ledger')
DIMS = A.DIMS


def cluster_ci_f1(preds, refs, fx, n_iter=2000, seed=20261207):
    """Fixture-cluster bootstrap CI of F1 (resample whole fixtures)."""
    byfx = {}
    for i, f in enumerate(fx):
        byfx.setdefault(f, []).append(i)
    keys = sorted(byfx)

    def f1_of(idxs):
        tp = fp = tn = fn = 0
        for i in idxs:
            p, r = preds[i], refs[i]
            if p and r:
                tp += 1
            elif p:
                fp += 1
            elif r:
                fn += 1
            else:
                tn += 1
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        return (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0

    # point: over the evaluable set
    eval_idx = [i for i in range(len(preds)) if refs[i] is not None]
    point = f1_of(eval_idx)
    if len(keys) < 5:
        return [point, point, point]
    rng = random.Random(seed)
    ests = []
    for _ in range(n_iter):
        idxs = []
        for _ in range(len(keys)):
            fxk = rng.choice(keys)
            idxs.extend(byfx[fxk])
        ests.append(f1_of(idxs))
    ests.sort()
    return [ests[int(0.025 * (n_iter - 1))],
            ests[int(0.975 * (n_iter - 1))], point]


def main():
    refs = A.ref_pairs()
    cal = A.cal_by()
    v6 = A.v6_by()
    items = A.items()

    # index reference rows by LEP trace id (each pair row carries the LEP)
    rows = []
    for p in refs:
        tid = p.get('lep_trace_id')
        lf = cal.get(tid)
        if lf is None:
            continue
        item = items.get(p.get('item_id'))
        refs_ = p.get('reference_set') if item else None
        rows.append({
            'item': p,
            'tid': tid,
            'pair_class': p.get('pair_class'),
            'fam': p.get('task_family'),
            'lep': (lf.get('lep_code')),
            'fx': tid.split('.')[0],
            'union_ref': p.get('union_ref'),
            'resolved': p.get('resolved_dims') or {},
            # candidate predictions
            'pred_orig': bool(lf['m1_union_orig']),
            'pred_cal': bool(lf['m1_union_cal']),
            'cal_state': lf.get('m1_cal_state'),
            'dims_orig': lf.get('m1_dims_orig') or {},
            'dims_cal': lf.get('m1_dims_cal') or {},
            # per-ref LEP-vs-refs distances are in the cal ledger
            'dist_cal': lf.get('m1_dist_cal') or {},
        })

    eval_rows = [r for r in rows if r['union_ref'] is not None]
    ex_rows = [r for r in rows if r['union_ref'] is None]

    def union_metrics(pred_key, rows_):
        preds = [r[pred_key] for r in rows_]
        refs_ = [r['union_ref'] for r in rows_]
        fx = [r['fx'] for r in rows_]
        cm = A.confusion(preds, refs_)
        cm['f1_ci95_cluster'] = cluster_ci_f1(preds, refs_, fx)
        return cm

    rep = {
        'reference_surface': {
            'n_pair_items': len(rows),
            'n_evaluable': len(eval_rows),
            'n_unresolved_excluded': len(ex_rows),
            'coverage': len(eval_rows) / len(rows),
            'note': ('union_ref None = at least one dimension undetermined '
                     '(ai_unresolved / judge_invalid / det-undetermined); '
                     'EXCLUDED, never a forced negative.  The reference '
                     'measures OBSERVABLE LEP-vs-first-benign divergence, '
                     'NOT statistical-anomaly-vs-benign-null — see '
                     'construct_alignment below.'),
            'pair_classes': dict(Counter(r['pair_class'] for r in rows)),
            'pair_classes_eval': dict(Counter(r['pair_class']
                                               for r in eval_rows)),
        },
        # (a) union-level evaluation, FULL evaluable surface
        'union_full_surface': {
            'original_frozen_theta_rule': union_metrics('pred_orig',
                                                       eval_rows),
            'calibrated_p3_q98_f0_family': union_metrics('pred_cal',
                                                         eval_rows),
        },
        # stratification (requirement 3: family + LEP where n permits)
        'union_strata': {},
        # (b) B2B control class — reported SEPARATELY
        'b2b_control_class': {},
        # (c) per-dimension against resolved dimension labels
        'per_dimension': {},
    }

    for strat, key in (('pair_class', 'pair_class'), ('fam', 'fam'),
                       ('lep', 'lep')):
        rep['union_strata'][strat] = {}
        for val in sorted({r[key] for r in eval_rows},
                          key=str):
            sub = [r for r in eval_rows if r[key] == val]
            rep['union_strata'][strat][str(val)] = {
                'n': len(sub),
                'low_power': len(sub) < 30,
                'original': union_metrics('pred_orig', sub),
                'calibrated': union_metrics('pred_cal', sub),
            }

    b2b_all = [r for r in rows if r['pair_class'] ==
               'benign_to_benign_control']
    b2b_eval = [r for r in b2b_all if r['union_ref'] is not None]
    if b2b_eval:
        rep['b2b_control_class'] = {
            'n': len(b2b_all), 'n_evaluable': len(b2b_eval),
            'n_unresolved': len(b2b_all) - len(b2b_eval),
            'note': ('FOR A PURE B2B CONTROL, a rule firing is a FALSE '
                     'POSITIVE by definition; the pair reference '
                     "'diverges' labels on B2B pairs mean 'benign runs "
                     "differ observably' and are NOT false positives of "
                     "the anomaly construct.  Rule firing on B2B pairs "
                     "is the empirical FPR contribution; ref+ rows are "
                     "reported for the record but never counted as TPs "
                     "of the anomaly construct."),
            'firing': {k: sum(1 for r in b2b_eval if r[k])
                      for k in ('pred_orig', 'pred_cal')},
            'ref_positive_rows': sum(1 for r in b2b_eval
                                     if r['union_ref']),
            'ref_negative_rows': sum(1 for r in b2b_eval
                                     if not r['union_ref']),
        }

    # per-dimension: candidate dim indicator vs reference resolved dim
    for d in DIMS:
        sub = [r for r in eval_rows
               if r['resolved'].get(d) is not None]
        if not sub:
            rep['per_dimension'][d] = {'n_resolved': 0}
            continue
        preds_o = [r['dims_orig'].get(d) for r in sub]
        preds_c = [r['dims_cal'].get(d) for r in sub]
        refs_ = [r['resolved'][d] for r in sub]
        rep['per_dimension'][d] = {
            'n_resolved': len(sub),
            'original': A.confusion(preds_o, refs_),
            'calibrated': A.confusion(preds_c, refs_),
        }

    # construct alignment: quantify the mismatch direction.  For LEP
    # pairs (not B2B): ref-True = observable divergence.  The calibrated
    # rule's FNs on this surface = observable divergences the anomaly
    # rule suppresses (moderate changes); its FPs = rows the anomaly
    # rule flags where the pair reference says 'agrees' (rare by
    # construction, since a flagged LEP statistic > pooled q98 while
    # the FIRST reference pair is judged 'agrees' — e.g. a pair-level
    # judge missing a change the statistic catches).  Report both.
    lep_eval = [r for r in eval_rows if r['pair_class'] !=
                'benign_to_benign_control']
    cal_cm = rep['union_full_surface']['calibrated_p3_q98_f0_family']
    rep['construct_alignment'] = {
        'lep_pairs_evaluable': len(lep_eval),
        'ref_positive_lep_pairs': sum(1 for r in lep_eval
                                      if r['union_ref']),
        'ref_negative_lep_pairs': sum(1 for r in lep_eval
                                      if not r['union_ref']),
        'cal_rule_fn_on_observable_divergence': cal_cm['fn'],
        'cal_rule_fp_on_observable_agreement': cal_cm['fp'],
        'interpretation': ('cal FN = observable LEP-vs-benign divergence '
                           'the calibrated anomaly rule does NOT flag '
                           '(suppressed sensitivity, incl. moderate '
                           'changes).  cal FP = rule flags an anomaly '
                           'level statistic but the single-pair '
                           'reference judges the pair as agreeing '
                           '(pair-level reference under-calls relative '
                           'to the pooled-null statistic; does not '
                           'invalidate the flag).'),
    }

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, 'classifier_validation.json'), 'w') as fh:
        json.dump(rep, fh, indent=1)
    uf = rep['union_full_surface']
    for name in ('original_frozen_theta_rule',
                 'calibrated_p3_q98_f0_family'):
        c = uf[name]
        print(f"{name}: n={c['n_evaluable']} TP={c['tp']} FP={c['fp']} "
              f"TN={c['tn']} FN={c['fn']} excl={c['excluded_unresolved']} "
              f"P={c['precision']:.3f} R={c['recall']:.3f} "
              f"F1={c['f1']:.3f} Spec={c['specificity']:.3f} "
              f"Bal={c['balanced_accuracy']:.3f}")
    print('per-dim resolved n:', {d: rep['per_dimension'][d].get(
        'n_resolved') for d in DIMS})
    print('written ledger/classifier_validation.json')


if __name__ == '__main__':
    main()
