#!/usr/bin/env python3.11
"""
benign_baseline.py — Requirement 6: reconcile the benign task-failure baseline.

The production report's denominators (13,365 / 9,107 / 16,586 = 39,058 total)
EXCEED the canonical corpus (26,965 traces).  This module determines exactly
what is being counted by computing, from the v6 ledger + the frozen grader
outputs, the four DISTINCT quantities the brief names:

  1. UNIQUE benign executions          (dedup over the frame's reference sets)
  2. BENIGN REFERENCE APPEARANCES      (each LEP row lists its 3 refs -> the
                                        39,058 count: a benign ref shared by
                                        several LEP rows is counted once PER LEP
                                        row it appears in)
  3. UNIQUE GRADEABLE benign executions (unique benigns with a non-ungradeable
                                        v6 grader grade)
  4. REFERENCE-APPEARANCE-WEIGHTED     (the 39,058 convention, i.e. production.py)

All four, overall and per family, with the EXACT grader version + source.
The stored 34.50% is reconciled against (4).  Writes out/benign_baseline.json.
Run:  python3.11 benign_baseline.py
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import cp_lib as L                 # noqa: E402

OUT = os.path.join(_HERE, 'out')


def _rate(k: int, n: int):
    return (k / n) if n else None


def main():
    v6 = L.load_v6()
    ds = L.load_downstream()
    specs = L.load_specs()

    # grader version + source (the v6 GT-boundary graders, frozen)
    import hashlib
    def _sha(p):
        try:
            return hashlib.sha256(open(p, 'rb').read()).hexdigest()
        except Exception:
            return 'MISSING'
    v6_dir = L.common.V6_DIR
    grader_sha = _sha(os.path.join(v6_dir, 'graders.py'))

    # per LEP row: its 3 benign refs + the v6 grader grades (benign_grades list)
    # The v6 ledger's benign_grades[k] is the grade of the k-th reference in
    # that row's reference_set (order-aligned with run_pipeline's refs loop).
    appearances = 0                 # total (row, ref) grade observations
    app_fail = Counter(); app_graded = Counter(); app_ng = Counter()
    for r in v6:
        fam = r['task_family']
        refs = ds[r['trace_id']].get('reference_set') or []
        grades = r.get('benign_grades') or []
        for rid, g in zip(refs, grades):
            appearances += 1
            g2 = (str(g).strip().lower() if g else 'none')
            if g2 in ('ungradeable', 'none'):
                app_ng[fam] += 1
            else:
                app_graded[fam] += 1
                if g2 in ('failure', 'incorrect'):
                    app_fail[fam] += 1

    # unique benign executions (dedup) + gradeable-unique + appearance-weighted
    # A benign ref is shared by multiple LEP rows (same fixture cell); its v6
    # grade is the SAME object on each (recomputed per row, deterministic).
    ben_grade: dict = {}
    ben_fam: dict = {}
    for r in v6:
        fam = r['task_family']
        refs = ds[r['trace_id']].get('reference_set') or []
        for rid, g in zip(refs, r.get('benign_grades') or []):
            ben_grade[rid] = (str(g).strip().lower() if g else 'none')
            ben_fam[rid] = fam
    uniq = Counter()
    uniq_fail = Counter(); uniq_graded = Counter(); uniq_ng = Counter()
    for rid, g in ben_grade.items():
        fam = ben_fam[rid]
        uniq[fam] += 1
        if g in ('ungradeable', 'none'):
            uniq_ng[fam] += 1
        else:
            uniq_graded[fam] += 1
            if g in ('failure', 'incorrect'):
                uniq_fail[fam] += 1

    fams = ['code_review', 'financial_analysis', 'research_synthesis']

    def fam_block(f):
        return {
            'unique_benign_executions': uniq.get(f, 0),
            'unique_benign_gradeable': uniq_graded.get(f, 0),
            'unique_benign_failure': uniq_fail.get(f, 0),
            'unique_benign_failure_rate': _rate(uniq_fail.get(f, 0),
                                                 uniq_graded.get(f, 0)),
            'reference_appearances': app_graded.get(f, 0),
            'appearance_failure': app_fail.get(f, 0),
            'appearance_failure_rate': _rate(app_fail.get(f, 0),
                                              app_graded.get(f, 0)),
            'reference_appearance_total_incl_ungradeable': (
                app_graded.get(f, 0) + app_ng.get(f, 0)),
            'appearance_ungradeable': app_ng.get(f, 0),
        }

    out = {
        'artifact': 'out/benign_baseline.json',
        'grader_version': {
            'module': 'analysis/measurement_v6/graders.py (v6 GT-boundary '
                     'graders: grade_financial / grade_research / grade_code)',
            'sha256': grader_sha,
            'vocabulary': 'raw {correct, incorrect, ungradeable}; the '
                          'production.py baseline counts g in '
                          "(failure, incorrect) — 'incorrect' being the v6 "
                          'raw value for task failure.',
            'source_artifacts': ['analysis/measurement_v6/out/per_trace_v6.jsonl',
                                  'analysis/consolidated_20261121_bnd_v41/'
                                  'downstream_results/per_trace.jsonl'],
        },
        'reconciliation': {
            'stored_34pct_convention': (
                'reference-APPEARANCE-WEITED over graded benign refs: each of '
                'the 16,773 frame rows contributes its 3 reference-set entries '
                'to the numerator/denominator.  39,058 = sum of graded '
                'appearances across families (exceeds 26,965 traces because a '
                'benign trace shared by K LEP rows in the same cell is counted '
                'K times).  This is quantity (4) below.'),
            'per_family': {f: fam_block(f) for f in fams},
            'total': {
                'unique_benign_executions': sum(uniq.values()),
                'unique_gradeable': sum(uniq_graded.values()),
                'unique_failure': sum(uniq_fail.values()),
                'unique_failure_rate': _rate(sum(uniq_fail.values()),
                                             sum(uniq_graded.values())),
                'total_reference_appearances': appearances,
                'appearance_ungradeable': sum(app_ng.values()),
                'appearance_graded': sum(app_graded.values()),
                'appearance_failure': sum(app_fail.values()),
                'appearance_failure_rate': _rate(
                    sum(app_fail.values()), sum(app_graded.values())),
                'appearance_denominator_identity': (
                    '3 refs x 16,773 rows = 50,319 total appearances '
                    '(== the B2B null denominator); graded appearances '
                    f'{sum(app_graded.values())}; ungradeable appearances '
                    f'{sum(app_ng.values())}.  50,319 = '
                    f'{sum(app_graded.values())} + '
                    f'{sum(app_ng.values())}.'),
            },
            'note': ('The 34.50% figure = 13,474/39,058 graded reference-'
                     'appearances = quantity (4).  The UNIQUE-execution '
                     'failure rate (quantity 3) is the honest no-perturbation '
                     'baseline and is reported alongside.  Neither is causal '
                     '(benign refs are separate executions).  The corrected '
                     'unique baseline is the one to cite; the appearance-'
                     'weighted 34.50% is retained only for provenance.'),
        },
        'conditional_on_grader_audit': (
            'These baseline numbers use the CURRENT v6 grader artifacts '
            '(per_trace_v6.jsonl benign_grades).  The separate task-correctness '
            'audit is ongoing; if its corrected grades are finalized, the '
            'baseline must be recomputed from those.  The graders are NOT '
            'modified here.'),
    }
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, 'benign_baseline.json'), 'w') as fh:
        json.dump(out, fh, indent=1)
    print(json.dumps(out['reconciliation']['total'], indent=1))
    print('per-family unique-rate / appearance-rate:')
    for f in fams:
        b = fam_block(f)
        print(' ', f, 'unique', b['unique_benign_failure'], '/',
              b['unique_benign_gradeable'], '=', b['unique_benign_failure_rate'],
              '| appearance', b['appearance_failure'], '/',
              b['reference_appearances'], '=', b['appearance_failure_rate'])


if __name__ == '__main__':
    main()
