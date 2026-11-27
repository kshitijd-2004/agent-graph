#!/usr/bin/env python3.11
"""False-negative / removed-positive classification ledger (req 4).

For the 4,350 removed positives and 256 new positives, every row is
classified into the five evidence classes from requirement 4:
   1. benign_variability — ordinary benign variability the original rule
      mis-flagged (zero-theta band, T below pooled q98);
   2. moderate_change    — genuine but moderate change suppressed by the
      calibrated rule (T exceeds the cell's own b2b max, below pooled
      q98; the "suppressed" census of the pooling audit);
   3. large_change        — large behavioral change suppressed by pooled
      thresholds (T > pooled theta... impossible by construction at
      q98 family — reported exactly how many, i.e. 0, plus the
      margin-based "near-miss" band T/theta ratios);
   4. statistic_change    — differences caused by the statistic
      (T vs T-B); for REMOVALS this axis cannot be the cause (B>=0
      implication, verified in transition_attribution); for ADDITIONS
      it is the re-centering mechanism;
   5. undetermined        — cannot be confidently classified from
      available evidence.

Independent reference labels (heldout pair reference) are attached where
they exist (124 removed-positive rows carry a reference label: 92
ref-True = independent confirmation that the removed rows WERE
observable divergences; 2 ref-False; 30 unresolved).  The reference is
reported as CONFIRMATION evidence, not as the classifier — it does not
relabel the frame.

Writes ledger/false_negative_analysis.json +
ledger/false_negative_ledger.jsonl (one row per transition row, with
class + all evidence fields).
"""
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audit_lib as A

OUT = os.path.join(A.AUDIT, 'ledger')
REF = os.path.join(A.FINAL, 'out', 'reference_heldout_pairs.jsonl')
#: proximity bands for REMOVED positives (by construction no dimension
#: reaches theta_q98, so the max ratio is always < 1.0):
MARGINS = [(0.0, 0.5), (0.5, 0.8), (0.8, 0.9), (0.9, 1.0),
           (1.0, float('inf'))]


def main():
    os.makedirs(OUT, exist_ok=True)
    rows = [json.loads(l) for l in
            open(os.path.join(OUT, 'transition_ledger.jsonl'))]
    ref = {json.loads(l)['lep_trace_id']: json.loads(l)
           for l in open(REF) if l.strip()}

    led_out = []
    cls_removed = Counter()
    cls_added = Counter()
    ref_slicing = Counter()
    near_miss = Counter()

    for r in rows:
        if r['transition'] not in ('removed_positive', 'new_positive'):
            continue
        rp = ref.get(r['trace_id'])
        uref = rp.get('union_ref') if rp else None
        rec = {'trace_id': r['trace_id'], 'transition': r['transition'],
               'task_family': r['task_family'], 'lep_code': r['lep_code'],
               'independent_ref': uref,
               'independent_ref_pair_class': rp.get('pair_class')
               if rp else None}
        # per-dim evidence
        dim_ev = {}
        max_ratio = 0.0
        for d in A.DIMS:
            od, cd = r['orig']['dims'][d], r['cal']['dims'][d]
            T, th2 = cd['distance'], cd['threshold']
            th1, B = od['threshold'], od['benign_ref']
            ratio = None
            if T is not None and th2 not in (None, 0.0):
                ratio = T / th2
            max_ratio = max(max_ratio, ratio or 0.0)
            dim_ev[d] = {'T': T, 'theta_q98_family': th2,
                         'theta_max3_cell': th1, 'B_cell': B,
                         'orig_fired': od['divergent'],
                         'cal_fired': cd['divergent'],
                         'zero_thr_orig': od['zero_threshold'],
                         'ratio_T_over_theta_q98': ratio}
        rec['dims'] = dim_ev
        rec['max_ratio_T_over_theta_q98'] = max_ratio
        for lo, hi in MARGINS:
            if lo <= max_ratio < hi:
                near_miss[f'{r["transition"]}_ratio_{lo}-{hi}'] += 1
        # classification
        if r['transition'] == 'removed_positive':
            zero_classes = r.get('orig_zero_classes')
            if uref is True:
                rec['class'] = 'moderate_or_genuine_confirmed'
                cls_removed['confirmed_by_reference'] += 1
                ref_slicing['removed_ref_true'] += 1
            elif uref is False:
                rec['class'] = 'false_positive_removed'
                cls_removed['reference_agrees_negative'] += 1
            else:
                # no reference: structural evidence, prioritized
                if r['orig']['zero_only']:
                    rec['class'] = 'benign_variability_zero_theta'
                    cls_removed['benign_variability_zero_theta'] += 1
                elif max_ratio >= 0.9:
                    rec['class'] = 'suppressed_change_near_pooled_thr'
                    cls_removed['suppressed_change_near_pooled_thr'] += 1
                elif max_ratio >= 0.5:
                    rec['class'] = 'moderate_change_suppressed'
                    cls_removed['moderate_change_suppressed'] += 1
                elif max_ratio < 0.2:
                    rec['class'] = 'benign_variability_low_T'
                    cls_removed['benign_variability_low_T'] += 1
                else:
                    rec['class'] = 'undetermined'
                    cls_removed['undetermined'] += 1
        else:  # new_positive
            mech = rec.get('mech')
            if uref is True:
                rec['class'] = 'confirmed_by_reference'
                cls_added['confirmed_by_reference'] += 1
                ref_slicing['new_ref_true'] += 1
            else:
                # mechanism from transition_attribution (recompute)
                m = set()
                for d in A.DIMS:
                    T, B, th1 = dim_ev[d]['T'], dim_ev[d]['B_cell'], \
                        dim_ev[d]['theta_max3_cell']
                    th2 = dim_ev[d]['theta_q98_family']
                    if T is None or th2 is None or not T > th2:
                        continue
                    if B is None or th1 is None:
                        m.add('evaluable_gain')
                    elif T > th1:
                        m.add('recentering')
                    else:
                        m.add('pooling')
                rec['mechanism'] = sorted(m) or ['undetermined']
                cls_added['+'.join(sorted(m)) or 'undetermined'] += 1
                rec['class'] = ('+' .join(sorted(m)) or 'undetermined')
        ref_tag = ('true' if uref is True
                   else ('false' if uref is False else 'unresolved'))
        ref_slicing[r['transition'] + '_ref_' + ref_tag] += 1
        led_out.append(rec)

    with open(os.path.join(OUT, 'false_negative_ledger.jsonl'), 'w') as fh:
        for x in led_out:
            fh.write(json.dumps(x) + '\n')
    rep = {'removed_positive_classes': dict(cls_removed),
           'new_positive_mechanisms': dict(cls_added),
           'reference_slice': dict(ref_slicing),
           'ratio_bands_max_T_over_theta_q98': dict(near_miss),
           'notes': {
               'max_ratio_T_over_theta_q98': (
                   'max over evaluable dims of T_d / theta_q98_family; '
                   'ratio < 1.0 means not a single dim reaches the pooled '
                   'threshold — the removal is pure threshold/pooling '
                   'effect; ratio >= 1.0 cannot occur for a removed '
                   'positive (any dim with T>theta would re-fire the '
                   'union)'),
               'confirmed_by_reference': ('a heldout independently '
                                           'adjudicated pair reference '
                                           'says the executions OBSERVABLY '
                                           'DIVERGE — the removed row '
                                           'carries a real observable '
                                           'change the calibrated rule '
                                           'does not flag (sensitivity '
                                           'loss, not FP removal)'),
               'benign_variability_zero_theta': (
                   'every triggered dim rested on a zero in-cell theta '
                   '(benigns identical on that dim): the original '
                   'rule\'s zero-threshold band; the classic FP band'),
               'undetermined': ('no reference label and no decisive '
                                'structural band — honestly unclassifiable '
                                'from available evidence'),
           }}
    with open(os.path.join(OUT, 'false_negative_analysis.json'), 'w') as fh:
        json.dump(rep, fh, indent=1)
    print(json.dumps(rep['removed_positive_classes'], indent=1))
    print('new:', rep['new_positive_mechanisms'])
    print('ref slice:', rep['reference_slice'])
    print('ratio bands:', rep['ratio_bands_max_T_over_theta_q98'])


if __name__ == '__main__':
    main()
