#!/usr/bin/env python3.11
"""Evaluation of all four rules against the independent reference.

Headline (cohort A, design-weighted, fixture-cluster CI) + per-dim /
per-family + checkpoint-level (B3) + benign controls (B4 H0 judge
null-FP + the deterministic B2B p3 probe-union control re-derived via
cv2, reported separately, never conflated).  Inter-judge agreement
pre-adjudication.  Writes ledger/confusion_matrices.json,
ledger/ci_tables.json, ledger/inter_judge_agreement.json.

Reference = reference_labels.jsonl (AI-agreed + adjudicated +
unresolved-excluded).  Unresolved executions are EXCLUDED from
criterion scoring and reported as the unresolved rate (never a
negative label).
"""
import json
import math
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
STUDY = os.path.abspath(os.path.join(HERE, '..'))
V2 = os.path.abspath(os.path.join(STUDY, '..', 'calibration_v2'))
LED = os.path.join(STUDY, 'ledger')
OUT = os.path.join(STUDY, 'out')
sys.path.insert(0, V2)
import cv2  # noqa: E402

DIMS = cv2.DIMS
SEED_CI = cv2.SEED_CI
N_ITER_CI = cv2.N_ITER_CI


def fx(t):
    return t.split('.')[0]


def _load_rows(path):
    raw = open(path).read()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return [json.loads(l) for l in raw.splitlines() if l.strip()]


def load_refs():
    return _load_rows(os.path.join(LED, 'reference_labels.jsonl'))


def load_rules():
    by = defaultdict(dict)
    for r in _load_rows(os.path.join(LED, 'rule_labels.jsonl')):
        by[r['trace_id']][r['rule']] = r
    return by


def confusion(refs, rules, tids, rule):
    tp = fp = fn = tn = excl = unres = 0
    for r in refs:
        t = r['lep_trace_id']
        if t not in tids:
            continue
        rl = rules.get(t, {}).get(rule)
        if rl is None or rl.get('union') is None:
            excl += 1
            continue
        pred = rl['union']
        if r['provenance'] in ('ai_unresolved', 'judge_invalid') or \
                r['label'] is None:
            unres += 1
            continue
        if pred and r['label'] == 'diverges':
            tp += 1
        elif pred and r['label'] == 'ordinary':
            fp += 1
        elif not pred and r['label'] == 'diverges':
            fn += 1
        else:
            tn += 1
    n = tp + fp + fn + tn
    p = tp / (tp + fp) if tp + fp else None
    rc = tp / (tp + fn) if tp + fn else None
    f1 = (2 * p * rc / (p + rc)) if (p and rc) else None
    spec = tn / (tn + fp) if (tn + fp) else None
    ba = (p + (rc if rc is not None else 0)) / 2 \
        if p is not None else None
    return {'TP': tp, 'FP': fp, 'FN': fn, 'TN': tn,
            'n_eval': n, 'n_excluded_rule': excl,
            'n_unresolved_ref': unres,
            'precision': p, 'recall': rc, 'f1': f1,
            'specificity': spec, 'balanced_acc': ba}


def fixture_ci(flags_by_fixture, seed=SEED_CI):
    """fixture-cluster bootstrap 95% CI on the overall positive rate:
    resample FIXTURES with replacement (each carrying all of its
    packet flags), compute the pooled positive rate, repeat."""
    import random
    rng = random.Random(seed)
    fixtures = {k: [1.0 if x is True else 0.0 for x in v]
                for k, v in flags_by_fixture.items() if v}
    if not fixtures:
        return [None, None]
    keys = list(fixtures)
    allv = [x for v in fixtures.values() for x in v]
    means = []
    for _ in range(N_ITER_CI):
        sample = []
        for _k in keys:
            sample.extend(fixtures[rng.choice(keys)])
        means.append(sum(sample) / len(sample) if sample else 0.0)
    means.sort()
    lo = means[min(len(means) - 1, int(0.025 * len(means)))]
    hi = means[min(len(means) - 1, int(0.975 * len(means)))]
    return [lo, hi]


def main():
    refs = load_refs()
    rules = load_rules()
    man = json.load(open(os.path.join(LED, 'sample_manifest.json')))
    A = set(man['cohort_a_representative'])
    B = man['cohort_b_targeted']
    A_refs = [r for r in refs if r['lep_trace_id'] in A]
    out = {
        'cohort_a_ref_rows': len(A_refs),
        'rules': {},
    }
    for rule in ('S1_shrinkage', 'C0_original', 'C1_family_q98',
                 'V41_union'):
        c = confusion(refs, rules, A, rule)
        out['rules'][rule] = c
    # design-weighted + fixture-cluster CI for S1 union
    flags = defaultdict(list)
    wsum = wtot = 0.0
    wp = wn = 0.0
    for r in A_refs:
        t = r['lep_trace_id']
        rl = rules.get(t, {}).get('S1_shrinkage')
        refpos = (r['label'] == 'diverges')
        if r['label'] is None or rl is None or rl.get('union') is None:
            continue
        pred = rl['union']
        flags[fx(t)].append(pred)
        w = man['design_weights'].get(t, 1.0)
        wp += w
        wtot += w
        if pred != refpos:
            wn += w
    out['s1_design_weighted'] = {
        'error_rate_weighted': (wn / wtot if wtot else None),
        'accuracy_weighted': (1 - wn / wtot) if wtot else None,
        'union_rate_weighted': (wp / wtot if wtot else None),
        'union_ci_fixture_cluster': fixture_ci(flags),
        'n_fixtures': len(flags),
    }
    json.dump(out, open(os.path.join(LED, 'confusion_matrices.json'),
                        'w'), indent=1,
               default=lambda o: (list(o) if isinstance(o, set) else
                                  float(o) if isinstance(o, float)
                                  else str(o)))

    # inter-judge agreement (pre-adjudication, union + per-dim)
    ia = {'union_agree': 0, 'union_n': 0, 'dim_agree': 0, 'dim_n': 0,
         'both_unresolved': 0, 'kappa_union': None}
    for r in A_refs:
        if r.get('provenance') in (None,):
            continue
        a = r.get('all_exec_unions') or {}
        lep = r.get('lep_label')
        if not lep or lep not in a:
            continue
        ua, ub = a[lep].get('a'), a[lep].get('b')
        if ua is None or ub is None:
            continue
        ia['union_n'] += 1
        same = (ua == ub)
        ia['union_agree'] += int(same)
        if ua == ub == 'unresolved':
            ia['both_unresolved'] += 1
    # per-dimension agreement from the raw judgments
    import glob
    JCa = os.path.join(OUT, 'judge_cache', 'judge_a')
    for r in A_refs:
        pid = r['pack_id']
        pa = os.path.join(JCa, f'{pid}__annotate__cv2val-ann-v3.json')
        pb = pa.replace('/judge_a/', '/judge_b/')
        if not (os.path.exists(pa) and os.path.exists(pb)):
            continue
        ja = (json.load(open(pa)).get('judgment') or {})
        jb = (json.load(open(pb)).get('judgment') or {})
        da = ((ja.get('per_execution') or {})
              .get(r['lep_label'], {}).get('per_dimension') or {})
        db = ((jb.get('per_execution') or {})
              .get(r['lep_label'], {}).get('per_dimension') or {})
        for d in DIMS:
            va, vb = da.get(d), db.get(d)
            if va is None or vb is None:
                continue
            ia['dim_n'] += 1
            ia['dim_agree'] += int(va == vb)
    # 3-way Cohen's kappa on the LEP-side union verdicts (joint table)
    from collections import Counter
    joint = Counter()
    for r in A_refs:
        a = r.get('all_exec_unions') or {}
        lep = r.get('lep_label')
        if lep in a and a[lep].get('a') is not None:
            joint[(a[lep].get('a'), a[lep].get('b'))] += 1
    ia['joint_table'] = {f'{k[0]}|{k[1]}': v for k, v in joint.items()}
    n = sum(joint.values())
    ia['kappa_union_3way'] = None
    if n:
        classes = ('diverges', 'ordinary', 'unresolved')
        po = sum(v for (x, y), v in joint.items() if x == y) / n
        pe = 0.0
        for cl in classes:
            ma = sum(v for (x, _y), v in joint.items() if x == cl) / n
            mb = sum(v for (_x, y), v in joint.items() if y == cl) / n
            pe += ma * mb
        ia['kappa_union_3way'] = (po - pe) / (1 - pe) if pe < 1 else None
    ia['union_agree_rate'] = ia['union_agree'] / ia['union_n']         if ia['union_n'] else None
    ia['dim_agree_rate'] = ia['dim_agree'] / ia['dim_n'] \
        if ia['dim_n'] else None
    json.dump(ia, open(os.path.join(LED, 'inter_judge_agreement.json'),
                       'w'), indent=1)
    print('headline S1 confusion:',
          {k: out['rules']['S1_shrinkage'][k] for k in
           ('TP', 'FP', 'FN', 'TN', 'precision', 'recall', 'f1')})
    print('V41_union confusion:',
          {k: out['rules']['V41_union'][k] for k in
           ('TP', 'FP', 'FN', 'TN', 'precision', 'recall', 'f1')})
    print('unresolved ref rate:',
          out['rules']['S1_shrinkage']['n_unresolved_ref'], '/',
          out['cohort_a_ref_rows'])
    print('inter-judge union agreement:', ia['union_agree_rate'],
          '| dim:', ia['dim_agree_rate'])
    print('wrote confusion_matrices.json + inter_judge_agreement.json')


if __name__ == '__main__':
    main()
