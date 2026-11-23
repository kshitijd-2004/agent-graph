#!/usr/bin/env python3
"""
Real-trace validation and coverage (v6).

READ-ONLY.  Loads staged traces + frozen specs, runs the v6 extractors, and
reports extraction coverage by family.  It does NOT grade the corpus and does
NOT overwrite any historical artifact.

Also re-verifies the two audit counts against THIS dataset:
  * "2,173 voided LEP pairs"          (audit-era dataset, v1 slice)
  * "138 M3 rows without a matched baseline"
and reports whether they reproduce here (requirement 6 says do not assume).

Usage:
    python3.11 analysis/measurement_v6/validate_real.py --limit 1200
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from features_v6 import (action_counter, authored_text_all,           # noqa: E402
                         extract_features_v6, final_response_text)
from graders import (grade_code, grade_financial, grade_research)      # noqa: E402
from extract_core import strip_gt                                     # noqa: E402

REPO = '/u/kdhande/agent-graph/agent-graph-v3'
CONS = os.path.join(REPO, 'analysis', 'consolidated_20261121_bnd_v41')
TRACES = os.path.join(CONS, 'staged', 'merged', 'traces')
PER_TRACE = os.path.join(CONS, 'downstream_results', 'per_trace.jsonl')
SPECROOT = os.path.join(REPO, 'analysis',
                        'bnd_v41_cw_swe200_provisional', 'specroot')


def load_specs():
    """fixture_id -> manifest dict."""
    out = {}
    if not os.path.isdir(SPECROOT):
        return out
    for d in os.listdir(SPECROOT):
        p = os.path.join(SPECROOT, d, 'manifest.json')
        if os.path.isfile(p):
            try:
                m = json.load(open(p))
            except Exception:                              # noqa: BLE001
                continue
            fid = m.get('fixture_id') or d
            out[fid] = m
    return out


def fixture_of(trace_id):
    """Derive fixture_id from a trace_id (strip the cell suffix)."""
    parts = trace_id.split('.')
    if len(parts) >= 2:
        return parts[0]
    return trace_id.split('__')[0]


def iter_traces(limit=None):
    names = sorted(os.listdir(TRACES))
    if limit:
        names = names[:limit]
    for n in names:
        if not n.endswith('.json'):
            continue
        try:
            yield json.load(open(os.path.join(TRACES, n)))
        except Exception:                                  # noqa: BLE001
            continue


def family_of(spec, trace):
    fam = (spec or {}).get('task_family')
    if fam:
        return fam
    tid = trace.get('trace_id') or ''
    if tid.startswith('code_review'):
        return 'code_review'
    if tid.startswith('res_') or 'scifact' in tid:
        return 'research_synthesis'
    if tid.startswith('fin_'):
        return 'financial_analysis'
    return 'unknown'


def coverage(limit=1200):
    specs = load_specs()
    print('specs loaded:', len(specs))
    cov = collections.defaultdict(lambda: collections.Counter())
    grades = collections.defaultdict(lambda: collections.Counter())
    n = 0
    for tr in iter_traces(limit):
        tid = tr.get('trace_id') or ''
        fid = fixture_of(tid)
        spec = specs.get(fid) or {}
        fam = family_of(spec, tr)
        if fam == 'unknown' or not spec:
            continue
        n += 1
        f = extract_features_v6(tr, spec, fam)
        for d, mp in (('assertions', f.assertions), ('decisions', f.decisions)):
            for k, v in mp.items():
                cov[fam][f'{d}:{v.state}'] += 1
        cov[fam]['actions:present' if f.actions else 'actions:empty'] += 1
        # decisive: are intermediate authored events distinct from final?
        allt = authored_text_all(tr)
        fin = final_response_text(tr)
        cov[fam]['has_intermediate_text' if (fin and allt != fin)
            else 'no_intermediate_text'] += 1
        # grading (GT read ONLY here)
        try:
            if fam == 'financial_analysis':
                g, _ = grade_financial(f.answer, spec)
            elif fam == 'research_synthesis':
                g, _ = grade_research(f.decisions, spec)
            else:
                g, _ = grade_code(f.decisions, spec)
            grades[fam][g] += 1
        except Exception:                                  # noqa: BLE001
            grades[fam]['error'] += 1
    print('traces evaluated:', n)
    print()
    print('EXTRACTION COVERAGE (per family, states counted over keys):')
    for fam in sorted(cov):
        print(' ', fam)
        for k, v in sorted(cov[fam].items()):
            print('     %-28s %d' % (k, v))
    print()
    print('GRADE OUTCOMES (GT consumed only by graders):')
    for fam in sorted(grades):
        tot = sum(grades[fam].values()) or 1
        print(' ', fam)
        for k, v in sorted(grades[fam].items()):
            print('     %-14s %5d  (%.1f%%)' % (k, v, 100.0 * v / tot))
    return n


def verify_audit_counts():
    """Re-verify the two audit counts against THIS dataset."""
    if not os.path.isfile(PER_TRACE):
        print('per_trace.jsonl not found; skipping')
        return
    rows = [json.loads(l) for l in open(PER_TRACE) if l.strip()]
    print('per_trace rows:', len(rows))
    m3 = [r for r in rows if r.get('m3_reach_class') in ('verified',
                                                         'plausible')]
    print('M3-supported rows:', len(m3))
    # "138 M3 rows without a matched baseline": M3 rows whose task_grade is not
    # a real verdict, i.e. no gradeable matched comparison exists.
    nograde = [r for r in m3 if r.get('task_grade') not in ('correct',
                                                            'failure')]
    print('M3 rows with non-verdict task_grade (no matched baseline): %d'
          % len(nograde))
    print('  grade values:', dict(collections.Counter(
        r.get('task_grade') for r in nograde)))
    # reference-set availability
    withrefs = [r for r in rows if r.get('reference_set')]
    print('rows with a reference_set: %d / %d' % (len(withrefs), len(rows)))
    print()
    print('AUDIT-ERA CLAIMS vs THIS DATASET:')
    print('  audit "2,173 voided LEP pairs"      -> not reproducible here:')
    print('      voiding is a property of the strict-basis pair computation')
    print('      in the v1 frozen slice; this dataset reports')
    print('      delivery_unverified separately (see summary denominators).')
    print('  audit "138 M3 rows, no matched baseline" -> %d here'
          % len(nograde))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=1200)
    ap.add_argument('--audit-counts', action='store_true')
    a = ap.parse_args()
    if a.audit_counts:
        verify_audit_counts()
    else:
        coverage(a.limit)


if __name__ == '__main__':
    main()
