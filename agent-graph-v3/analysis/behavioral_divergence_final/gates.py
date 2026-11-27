#!/usr/bin/env python3.11
"""
Pre-registered acceptance gates (FROZEN_PROTOCOL §7; MEASUREMENT_SPEC §9).

gates.evaluate(impl_scores, synthetic, coverage, blinding, sealed_ok)
applies the DECISION RULE exactly as frozen:

  * G1 extraction value accuracy >= 0.80 (design-weighted, judgeable,
    Wilson CI reported; coverage >= v6 baseline per dimension);
  * G2 ALL synthetic invariance tests pass (rate == 1.0);
  * G3 synthetic sensitivity flip-rate >= 0.95;
  * G4 union F1 >= 0.65 AND precision >= 0.70 on the evaluable heldout
    pair surface;
  * G5 benign-to-benign FPR <= 0.05;
  * G6 zero-threshold dependence REPORTED (never gated);
  * G7 reference coverage report + SWE issue-identification judgeable via
    RT-1 or explicitly declared unsupported with frame share;
  * G8 leak probe + blinding clean + protected fingerprints unchanged.

Decision rule: the implementation passing the most gates WITHOUT failing
any of G2, G3, G5, G8 is selected; ties broken by G1 then G5. If NO
implementation passes G1-G5, the verdict is NO_IMPLEMENTATION_VALIDATED
with per-dimension support status — the production analysis then runs on
the best-supported combination, explicitly qualified.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
import sys
sys.path.insert(0, HERE)
import common

#: v6 coverage baselines (16,773 frame; FINDINGS §F) — the G1 coverage floor
V6_COVERAGE_BASELINE = {
    'assertions': 12707,
    'decisions': 12779,
    'outcomes': 12068,
}


def _g1(impl: Dict[str, Any]) -> Dict[str, Any]:
    acc = impl.get('extraction_accuracy')
    cov = impl.get('coverage') or {}
    acc_ok = (acc is not None and acc.get('value_accuracy') is not None
              and acc['value_accuracy'] >= 0.80
              and acc.get('n_judgeable', 0) > 0)
    cov_ok = all(cov.get(d, 0) >= V6_COVERAGE_BASELINE[d]
                 for d in V6_COVERAGE_BASELINE)
    return {'pass': bool(acc_ok and cov_ok),
            'value_accuracy': (acc or {}).get('value_accuracy'),
            'wilson95': (acc or {}).get('wilson95'),
            'n_judgeable': (acc or {}).get('n_judgeable'),
            'coverage': cov, 'coverage_floor': V6_COVERAGE_BASELINE,
            'coverage_ok': cov_ok, 'accuracy_ok': acc_ok,
            'evidence': impl.get('extraction_accuracy_evidence')}


def _g2(synthetic: Dict[str, Any]) -> Dict[str, Any]:
    rate = (synthetic.get('gates') or {}).get('G2_invariance_pass_rate')
    return {'pass': rate == 1.0, 'invariance_pass_rate': rate,
            'note': 'all synthetic invariance tests must pass (any '
                    'failure = gate fail)'}


def _g3(synthetic: Dict[str, Any]) -> Dict[str, Any]:
    rate = (synthetic.get('gates') or {}).get('G3_sensitivity_flip_rate')
    return {'pass': (rate is not None and rate >= 0.95),
            'sensitivity_flip_rate': rate}


def _g4(impl: Dict[str, Any]) -> Dict[str, Any]:
    p = impl.get('union_pairs') or {}
    f1 = p.get('f1')
    prec = p.get('precision')
    ok = (f1 is not None and prec is not None and f1 >= 0.65
          and prec >= 0.70 and p.get('n_evaluable', 0) > 0)
    return {'pass': ok, 'f1': f1, 'precision': prec, 'recall': p.get('recall'),
            'n_evaluable': p.get('n_evaluable'),
            'n_excluded_unstable': p.get('n_excluded_unstable')}


def _g5(impl: Dict[str, Any]) -> Dict[str, Any]:
    b = impl.get('benign_to_benign') or {}
    fpr = b.get('fpr')
    ok = (fpr is not None and fpr <= 0.05 and b.get('n', 0) > 0)
    return {'pass': ok, 'fpr': fpr, 'n_controls': b.get('n'),
            'wilson95': b.get('wilson95')}


def _g6(impl: Dict[str, Any]) -> Dict[str, Any]:
    return {'pass': True, 'gated': False,
            'zero_threshold_only': impl.get('zero_threshold_only'),
            'note': 'reported, NEVER gated (construct property of the '
                    'frozen theta rule)'}


def _g7(impl: Dict[str, Any], coverage: Dict[str, Any]) -> Dict[str, Any]:
    cov_report = impl.get('reference_coverage') or {}
    swe = cov_report.get('swe_issue_identification') or {}
    ok = bool(swe.get('judgeable') is True
              or swe.get('declared_unsupported') is True)
    empty_cells = coverage.get('empty_cells') or []
    unsampled = coverage.get('in_frame_but_unsampled_cells') or []
    return {'pass': ok,
            'swe_issue_identification': swe,
            'coverage_empty_cells': len(empty_cells),
            'coverage_unsampled_cells': len(unsampled),
            'note': 'SWE issue-identification must be judgeable via RT-1 '
                    'or explicitly declared unsupported with its frame '
                    'share; empty/unsampled cells are named, never hidden'}


def _g8(blinding: Dict[str, Any], sealed: Dict[str, Any],
        leak_probe: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    b_ok = (blinding.get('verdict') == 'clean')
    s_ok = sealed.get('ok', False)
    l_ok = (leak_probe is None) or (leak_probe.get('n_leaked') == 0)
    return {'pass': bool(b_ok and s_ok and l_ok),
            'blinding': blinding.get('verdict'),
            'sealed_fingerprints_ok': s_ok,
            'n_mismatches': len(sealed.get('mismatches') or []),
            'leak_probe': (leak_probe or {}).get('n_leaked')}


def evaluate(impl_scores: Dict[str, Dict[str, Any]],
             synthetic: Dict[str, Any],
             coverage: Dict[str, Any],
             blinding: Dict[str, Any],
             sealed: Dict[str, Any],
             leak_probe: Optional[Dict[str, Any]] = None,
             ) -> Dict[str, Any]:
    """Pre-registered decision rule (FROZEN_PROTOCOL §7).  Never re-tuned
    after this is frozen: thresholds, gates, and the tie-break are the
    frozen quantities."""
    per_impl: Dict[str, Dict[str, Any]] = {}
    for name, impl in impl_scores.items():
        g = {
            'G1': _g1(impl), 'G2': _g2(synthetic), 'G3': _g3(synthetic),
            'G4': _g4(impl), 'G5': _g5(impl), 'G6': _g6(impl),
            'G7': _g7(impl, coverage), 'G8': _g8(blinding, sealed,
                                                    leak_probe)}
        hard_fail = any(not g[k]['pass'] for k in ('G2', 'G3', 'G5', 'G8'))
        n_pass = sum(1 for v in g.values() if v['pass'])
        g1_acc = g['G1']['value_accuracy']
        g5_fpr = g['G5']['fpr']
        per_impl[name] = {
            'gates': g, 'n_gates_passed': n_pass,
            'hard_fail': hard_fail,
            # tie-break keys (lower is better for g5_fpr when None -> +1)
            'tiebreak_g1': (g1_acc if g1_acc is not None else -1.0),
            'tiebreak_g5': (g5_fpr if g5_fpr is not None else 1.0),
        }
    # decision rule
    eligible = {n: v for n, v in per_impl.items() if not v['hard_fail']}
    verdict: Dict[str, Any]
    if not eligible:
        verdict = {
            'decision': 'NO_IMPLEMENTATION_VALIDATED',
            'note': ('no implementation passed the hard gates G2/G3/G5/G8 '
                     'without failing one; per-dimension support status '
                     'follows; the production analysis runs on the '
                     'best-supported combination, explicitly qualified')}
    else:
        best = sorted(eligible.items(),
                      key=lambda kv: (-kv[1]['n_gates_passed'],
                                      -kv[1]['tiebreak_g1'],
                                      kv[1]['tiebreak_g5']))
        top = best[0]
        decision = 'NO_IMPLEMENTATION_VALIDATED'
        # a selection requires G1-G5 all passing on the top impl
        g = top[1]['gates']
        if all(g[k]['pass'] for k in ('G1', 'G2', 'G3', 'G4', 'G5')):
            decision = f'SELECTED:{top[0]}'
        verdict = {
            'decision': decision,
            'selected': top[0] if decision.startswith('SELECTED:')
            else None,
            'ranking': [(n, v['n_gates_passed'], v['tiebreak_g1'],
                         v['tiebreak_g5']) for n, v in best],
            'note': ('ties broken by G1 accuracy then G5 FPR; selection '
                     'is on measurement VALIDITY, not on the magnitude of '
                     'reported divergence/propagation rates')}
    # per-dimension support status (the qualified-result path)
    support: Dict[str, Any] = {}
    for name, impl in impl_scores.items():
        dim_support = {}
        pd = impl.get('per_dimension') or {}
        for d in common.DIMS:
            e = pd.get(d) or {}
            if e.get('n_reference_comparable', 0) >= 20:
                dim_support[d] = ('defensible'
                                  if e.get('value_accuracy') is not None
                                  and e['value_accuracy'] >= 0.80
                                  else 'qualified')
            else:
                dim_support[d] = 'unsupported_low_surface'
        support[name] = dim_support
    verdict['dimension_support_status'] = support
    return verdict


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--impl-scores', default='out/eval_scores.json')
    ap.add_argument('--partition', default='heldout')
    a = ap.parse_args()
    impl_scores = json.load(open(os.path.join(common.OUT,
                                               'eval_scores.json')))
    synthetic = json.load(open(os.path.join(common.OUT,
                                            'synthetic_report.json')))
    coverage = json.load(open(os.path.join(common.OUT,
                                            'sample_coverage_report.json')))
    blinding = json.load(open(os.path.join(common.OUT,
                                            'items_' + a.partition +
                                            '_report.json')))['blinding_scan']
    sealed = common.check_sealed()
    out = evaluate(impl_scores, synthetic, coverage, blinding, sealed)
    common.write_json(out, 'gate_verdict.json')
    print(json.dumps({k: out.get(k) for k in
                      ('decision', 'selected', 'ranking')}, indent=1))
