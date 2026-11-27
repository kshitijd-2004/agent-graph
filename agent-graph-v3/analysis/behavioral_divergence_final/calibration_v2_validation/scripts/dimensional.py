#!/usr/bin/env python3.11
"""Per-dimension and per-family validation (requirement 6).

For each of the four rules, per-dimension P/R/F1 (where the reference
per-dimension label was established, i.e. not unresolved) and
per-family P/R/F1 (whole-trace union).  A rule's per-dimension
positivity (fires) comes from rule_labels.jsonl (cv2 re-derivation for
S1/C0/C1; the frozen V4.1 per-dim surface for V4.1).  The reference
per-dimension label comes from the judges' per_dimension verdicts on
the LEP-labeled execution (AI-agreed or adjudicated; unresolved
dimensions are EXCLUDED from that dimension's support, never forced).
Also: the O1 code_review outcome power limitation, explicitly.
Writes ledger/dimensional_validation.json.
"""
import json
import os
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
STUDY = os.path.abspath(os.path.join(HERE, '..'))
LED = os.path.join(STUDY, 'ledger')
DIMS = ('assertions', 'decisions', 'actions', 'outcomes')

def _load_refs():
    raw = open(os.path.join(LED, 'reference_labels.jsonl')).read()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return [json.loads(l) for l in raw.splitlines() if l.strip()]


FAMS = ('code_review', 'financial_analysis', 'research_synthesis')


def load_rules():
    by = defaultdict(dict)
    raw = open(os.path.join(LED, 'rule_labels.jsonl')).read()
    try:
        rows = json.loads(raw)
    except json.JSONDecodeError:
        rows = [json.loads(l) for l in raw.splitlines() if l.strip()]
    for r in rows:
        by[r['trace_id']][r['rule']] = r
    return by


def main():
    man = json.load(open(os.path.join(LED, 'sample_manifest.json')))
    A = set(man['cohort_a_representative'])
    refs = _load_refs()
    refs = [r for r in refs if r['lep_trace_id'] in A]
    rules = load_rules()
    out = {'n_ref_rows': len(refs), 'per_rule': {},
           'o1_code_review_outcome': {}}
    for rule in ('S1_shrinkage', 'C0_original', 'C1_family_q98',
                 'V41_union'):
        per_dim = {d: {'TP': 0, 'FP': 0, 'FN': 0, 'TN': 0, 'n': 0}
                   for d in DIMS}
        per_fam = {f: {'TP': 0, 'FP': 0, 'FN': 0, 'TN': 0}
                   for f in FAMS}
        for r in refs:
            t = r['lep_trace_id']
            rl = rules.get(t, {}).get(rule)
            if rl is None:
                continue
            fam = r['task_family']
            # whole-trace union per family
            refu = r['label']
            if refu is not None and rl.get('union') is not None:
                p = bool(rl['union'])
                if p and refu == 'diverges':
                    per_fam[fam]['TP'] += 1
                elif p and refu == 'ordinary':
                    per_fam[fam]['FP'] += 1
                elif not p and refu == 'diverges':
                    per_fam[fam]['FN'] += 1
                else:
                    per_fam[fam]['TN'] += 1
            # per-dimension: reference per_dim from the adjudicated label
            rp = r.get('per_dim') or {}
            fires = rl.get('fires') or {}
            for d in DIMS:
                rd = rp.get(d)
                if rd is None or rd in ('unresolved', 'disagreement'):
                    continue
                pred = fires.get(d)
                if pred is None:
                    continue
                per_dim[d]['n'] += 1
                if pred and rd is True:
                    per_dim[d]['TP'] += 1
                elif pred and rd is False:
                    per_dim[d]['FP'] += 1
                elif (not pred) and rd is True:
                    per_dim[d]['FN'] += 1
                else:
                    per_dim[d]['TN'] += 1
        # P/R/F1 helpers
        def prf(c):
            tp, fp, fn, tn = c['TP'], c['FP'], c['FN'], c['TN']
            p = tp / (tp + fp) if tp + fp else None
            rc = tp / (tp + fn) if tp + fn else None
            # F1 = 0.0 when P and R are BOTH defined (support exists);
            # None only when a denominator is empty (no support)
            if p is None or rc is None:
                f1 = None
            else:
                f1 = (2 * p * rc / (p + rc)) if (p + rc) else 0.0
            return {'TP': tp, 'FP': fp, 'FN': fn, 'TN': tn,
                   'precision': p, 'recall': rc, 'f1': f1}
        out['per_rule'][rule] = {
            'per_dim': {d: prf(c) for d, c in per_dim.items()},
            'per_family': {f: prf({'TP': c['TP'], 'FP': c['FP'],
                                   'FN': c['FN'], 'TN': c['TN']})
                           for f, c in per_fam.items()},
        }
        # O1 code_review outcome power (S1 only)
        if rule == 'S1_shrinkage':
            cr_out = {'n_eval': 0, 'n_fire': 0}
            for r in refs:
                if r['task_family'] != 'code_review':
                    continue
                rl = rules.get(r['lep_trace_id'], {}).get(rule)
                if not rl:
                    continue
                fo = rl['fires'].get('outcomes')
                if fo is None:
                    continue
                cr_out['n_eval'] += 1
                cr_out['n_fire'] += int(fo)
            out['o1_code_review_outcome'] = cr_out
            cr_ref = sum(1 for r in refs if r['task_family']
                         == 'code_review' and
                         (r.get('per_dim') or {}).get('outcomes')
                         is True)
            out['o1_code_review_outcome']['ref_pos_outcome'] = cr_ref
            out['o1_code_review_outcome'][
                'power_note'] = ('code_review O1 outcome channel is '
                                 'structurally power-limited '
                                 '(smallest attainable one-sided p > '
                                 '0.05); reported here as support, '
                                 'never forced to pass (OUTCOME_'
                                 'CALIBRATION_REPORT §3.1).')
    json.dump(out, open(os.path.join(LED, 'dimensional_validation.json'),
                        'w'), indent=1)
    s1 = out['per_rule']['S1_shrinkage']
    print('per-dim S1:', {d: s1['per_dim'][d]['f1'] for d in DIMS})
    print('per-family S1 F1:',
          {f: s1['per_family'][f]['f1'] for f in FAMS})
    print('O1 code_review outcome:',
          out['o1_code_review_outcome'])
    print('wrote ledger/dimensional_validation.json')


if __name__ == '__main__':
    main()
