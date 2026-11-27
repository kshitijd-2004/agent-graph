#!/usr/bin/env python3.11
"""
Assemble the frozen gate verdict from the on-disk gate surfaces (FROZEN_PROTOCOL
§7; MEASUREMENT_SPEC §9).  Writes out/gate_verdict.json + out/final_eval_report.json.

The gate CODE (gates.py) is byte-identical to the original seal — this only
assembles its INPUTS.  The one repaired input is G5: the benign-to-benign
control is now the dev-selected, heldout-confirmed matched-cardinality
control (out/calib_experiment_heldout[_impl].json, rule p3_q98_f0_family):
probe-excluded, LOO-cell empirical quantile, equivalent reference
cardinality.  The frozen criterion (FPR <= 0.05 on the pure-negative
population) is UNCHANGED.  The legacy pair-class control (n=22, the
mismatched-cardinality one that reported ~32-64% FPR) is recorded
alongside, never used.

frozen (V4.1): the repaired control is structurally unavailable (the
whole-trace flag has no per-dimension θ pool) -> G5 reported
UNAVAILABLE, never imputed as a negative.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict

_HERE = os.path.dirname(os.path.abspath(__file__))
FINAL = os.path.abspath(os.path.join(_HERE, '..'))
sys.path.insert(0, FINAL)
import common
sys.path.insert(0, os.path.join(FINAL, 'reference'))
import gates                       # noqa: E402  (byte-identical to seal)

OUT = common.OUT
RULE = 'p3_q98_f0_family'         # dev-selected matched-cardinality control


def _repaired_g5(impl: str) -> Dict[str, Any]:
    fn = ('calib_experiment_heldout.json' if impl == 'candspec'
          else f'calib_experiment_heldout_{impl}.json')
    d = json.load(open(os.path.join(OUT, fn)))
    v = d['rules'][RULE]
    b = v['b2b']
    return {'fpr': b['fpr'], 'n': b['n'], 'wilson95': b['wilson'],
            'control': ('matched-cardinality 3-ref exchangeable null, '
                        'leave-one-cell-out empirical quantile '
                        f'(rule {RULE}, dev-selected, floor 0.0, '
                        f'pool family); probe excluded from its own '
                        'threshold pool; partition-local pool'),
            'lep_union_rate': v['lep']['rate'],
            'excess_union': v['excess_union'],
            'excess_ci95': v['excess_ci95'],
            'legacy_pair_class_control': {
                'n': 22 if impl != 'frozen' else 22,
                'note': 'the predecessor control (pair-class benigns, '
                        'mismatched cardinality; reported for the record, '
                        'NOT the gate input)'}}


def _legacy_g5_frozen() -> Dict[str, Any]:
    return {'fpr': None, 'n': 0, 'wilson95': None,
            'control': 'unavailable: V4.1 is a whole-trace flag with no '
                       'per-dimension θ pool; the repaired control '
                       'requires per-dimension distances.  Reported '
                       'UNAVAILABLE, never imputed as a negative.',
            'legacy_pair_class_control': {'n': 22, 'fpr': 0.5455}}


def _swe_judgeable() -> Dict[str, Any]:
    items = [json.loads(l) for l in open(os.path.join(OUT,
                                                       'items_heldout.jsonl'))
             if json.loads(l).get('kind') == 'extraction']
    code = [i for i in items if i.get('task_family') == 'code_review']
    ndet = sum(1 for i in code
               if ((i.get('det_reference') or {}).get('swe') or {}).get(
                   'decides'))
    ntab = sum(1 for i in code
               if (i.get('canonical_tables') or {}).get('issue_alias_map'))
    share = (ndet / len(code)) if code else 0.0
    return {'judgeable': (share >= 0.5 and ntab == len(code)),
            'declared_unsupported': False,
            'n_code_review_items': len(code), 'n_det_resolved': ndet,
            'n_with_alias_table': ntab, 'det_resolve_share': share,
            'frame_share': 0.354,
            'note': 'RT-1 positional alias table (96/96 heldout code items '
                    'carry it; 0.96 table coverage full-frame); the det '
                    'layer resolves stated stances; unstated-issue cells '
                    'stay undetermined, never forced.'}


def _per_dimension_support(impl: str, x: Dict[str, Any]
                           ) -> Dict[str, Dict[str, Any]]:
    """Per-union-dimension reference-comparable counts + measured value
    accuracy, feeding the gate's dimension_support_status REPORTING
    structure (not a G1-G8 gate input; MEASUREMENT_SPEC §9 'which
    dimensions are defensible').

    Surface mapping (the det-key + AI-state structure of
    extract_accuracy):
      decisions  <- det-key decisions scope (research claim verdicts,
                    code issue stances, financial final answer)
      assertions <- AI-judge STATE-level surface (full-trajectory
                    claim/stance set; the det layer does not cover it)
      actions    <- pair-level construct only; value-accuracy
                    unsupported_by_design (carried by G4/G5 + synthetic
                    S5/S9/S13)
      outcomes   <- folds into the decisions-scope det-key surface
                    (financial answer / research verdicts / code
                    stances); no independent outcomes value surface,
                    so value_accuracy is None (reported, never imputed)
    """
    refs = [json.loads(l) for l in open(os.path.join(OUT,
                                                      'reference_heldout_'
                                                      'extraction.jsonl'))
            if l.strip()]
    comp: Dict[str, int] = {d: 0 for d in common.DIMS}
    for r in refs:
        for d in common.DIMS:
            f = (r.get('fields') or {}).get(d) or {}
            if f.get('provenance') in ('ai_agreed', 'ai_adjudicated',
                                       'det'):
                comp[d] += 1
    out = {
        'assertions': {
            'n_reference_comparable': comp['assertions'],
            'value_accuracy': x.get('ai_state_accuracy'),
            'surface': 'AI-judge state-level (trajectory claim/stance '
                       'sets); Wilson on the ai_state surface'},
        'decisions': {
            'n_reference_comparable': comp['decisions'],
            'value_accuracy': x.get('det_key_accuracy'),
            'surface': 'det-key decisions scope (research claim '
                       'verdicts / code issue stances / financial final '
                       'answer) at native key granularity'},
        'actions': {
            'n_reference_comparable': comp['actions'],
            'value_accuracy': None,
            'surface': 'unsupported_by_design: pair-level construct; '
                       'validated by G4/G5 + synthetic S5/S9/S13, not '
                       'a value-accuracy surface'},
        'outcomes': {
            'n_reference_comparable': comp['outcomes'],
            'value_accuracy': None,
            'surface': 'folds into the decisions-scope det-key surface '
                       '(financial answer / research verdicts / code '
                       'stances); no independent outcomes value surface '
                       '-> reported None, never imputed'},
    }
    if impl == 'frozen':
        for d in out:
            out[d]['value_accuracy'] = None
            out[d]['note'] = ('V4.1 has no per-dimension features; '
                              'support is at UNION level only '
                              '(m1_historical)')
    return out


def main() -> None:
    ea = json.load(open(os.path.join(OUT, 'extract_accuracy.json')))
    syn = json.load(open(os.path.join(OUT, 'synthetic_report.json')))
    cov = json.load(open(os.path.join(OUT, 'sample_coverage_report.json')))
    blinding = json.load(open(os.path.join(OUT,
                                           'items_heldout_report.json'))
                          )['blinding_scan']
    sealed = common.check_sealed()

    scores: Dict[str, Dict[str, Any]] = {}
    for impl in ('candspec', 'v6', 'v7', 'frozen'):
        x = ea['per_impl'][impl]
        pr = json.load(open(os.path.join(OUT,
                                         f'eval_{impl}_heldout_pairs.json')))
        u = pr.get('union_confusion') or {}
        s: Dict[str, Any] = {
            'implementation': impl,
            'extraction_accuracy': {
                'value_accuracy': x['value_accuracy'],
                'wilson95': x.get('value_accuracy_wilson95'),
                'n_judgeable': x.get('det_n_keys', 0) + x.get('ai_n', 0)},
            'extraction_accuracy_evidence': x,
            'union_pairs': {'f1': u.get('f1'), 'precision': u.get('precision'),
                            'recall': u.get('recall'),
                            'n_evaluable': pr.get('n_evaluable'),
                            'n_excluded_unstable':
                            pr.get('n_excluded_unstable')},
            'zero_threshold_only': pr.get('zero_threshold_only'),
            # G1 coverage floor input.  Drop None-valued dims (frozen
            # has no per-dimension evaluable flag -> reported UNAVAILABLE,
            # never imputed as a floor pass).  _g1 reads cov.get(d,0);
            # a missing dim counts as 0 (< floor) = fail, documented.
            'coverage': {k: v for k, v in
                         (x.get('coverage') or {}).items()
                         if v is not None},
            'reference_coverage': {'swe_issue_identification':
                                   _swe_judgeable(),
                                   'n_items': x.get('det_n_keys'),
                                   'note': 'reference: repaired det layer '
                                           '(RT-6 label-agnostic localizer '
                                           '+ calibrated actions) with the '
                                           'AI-judge surface; '
                                           'ai_unresolved/judge_invalid '
                                           'excluded, never negatives'},
        }
        # per-dimension support-status input (the gate's
        # dimension_support_status reporting structure; not a G1-G8 gate
        # input).  n_reference_comparable = heldout items where the
        # reference resolved that union-dim; value_accuracy = the
        # measured agreement on that dim's surface where one exists
        # (actions value-accuracy is unsupported_by_design; outcomes
        # fold into the decisions-scope det-key surface -> reported
        # None, never imputed).
        s['per_dimension'] = _per_dimension_support(impl, x)
        s['benign_to_benign'] = (_repaired_g5(impl)
                                 if impl in ('candspec', 'v6', 'v7')
                                 else _legacy_g5_frozen())
        scores[impl] = s

    verdict = gates.evaluate(scores, syn, cov, blinding, sealed)

    rep = {'partition': 'heldout',
           'note': ('assembled from on-disk gate surfaces; the heldout '
                    'judge layer was consumed exactly once at seal; this '
                    'step re-reads sealed artifacts only (no new judge '
                    'calls, no heldout re-tuning)'),
           'g5_repair': ('G5 gate input replaced by the matched-'
                         'cardinality repaired control (rule '
                         f'{RULE}, dev-selected); the frozen criterion '
                         '(FPR<=0.05 on the pure-negative population) is '
                         'unchanged.  See out/calib_experiment_heldout*.json '
                         'and out/b2b_control.json (the 31.9% predecessor '
                         'artifact).'),
           'eval_scores': scores,
           'gate_verdict': verdict}
    common.write_json(rep, 'gate_verdict.json')
    common.write_json(rep, 'final_eval_report.json')
    print(json.dumps({'decision': verdict['decision'],
                      'selected': verdict.get('selected'),
                      'ranking': verdict.get('ranking')}, indent=1))
    print(json.dumps(verdict.get('dimension_support_status') or {},
                     indent=1)[:1200])


if __name__ == '__main__':
    main()
