#!/usr/bin/env python3
"""
End-to-end v6 measurement pipeline (four-dimensional union, M1-M4).

READ-ONLY with respect to all historical artifacts: writes ONLY under
``analysis/measurement_v6/out/``.

WHAT IT COMPUTES
  For each LEP trace in the consolidated dataset:
    1. features for the LEP and for each of its benign references
    2. per-dimension distances (assertions, decisions, actions, outcomes)
    3. benign-calibrated thresholds from BENIGN-TO-BENIGN variation
    4. per-dimension divergence + the UNION (no weights, no composite)
    5. M1 (whole-trace union), M2 (cross-agent), M3 (reach), M4 / M4-matched

M2/M3 reuse the FROZEN downstream attribution (origin roles, delivery
verification, reach class) so the corrected numbers differ from the historical
ones ONLY through the measurement corrections, never through a redefinition of
the propagation criteria.

USAGE
  python3.11 analysis/measurement_v6/run_pipeline.py --limit 3000
  python3.11 analysis/measurement_v6/run_pipeline.py            # full corpus
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import distances as D                                              # noqa: E402
import m4_matched                                                  # noqa: E402
import union as U                                                  # noqa: E402
from extract_core import strip_gt                                  # noqa: E402
from features_v6 import (action_counter, extract_features_v6)       # noqa: E402
from graders import grade_code, grade_financial, grade_research     # noqa: E402

REPO = '/u/kdhande/agent-graph/agent-graph-v3'
CONS = os.path.join(REPO, 'analysis', 'consolidated_20261121_bnd_v41')
TRACES = os.path.join(CONS, 'staged', 'merged', 'traces')
PER_TRACE = os.path.join(CONS, 'downstream_results', 'per_trace.jsonl')
SPECROOT = os.path.join(REPO, 'analysis',
                        'bnd_v41_cw_swe200_provisional', 'specroot')
OUT = os.path.join(HERE, 'out')


def load_specs():
    out = {}
    for d in sorted(os.listdir(SPECROOT)):
        p = os.path.join(SPECROOT, d, 'manifest.json')
        if os.path.isfile(p):
            try:
                out[d] = json.load(open(p))
            except Exception:                              # noqa: BLE001
                continue
    return out


def index_traces():
    """trace_id -> trace object (streamed; only ids we need are kept)."""
    need = set()
    for line in open(PER_TRACE):
        if not line.strip():
            continue
        r = json.loads(line)
        need.add(r['trace_id'])
        for ref in r.get('reference_set') or []:
            need.add(ref)
    got = {}
    for n in os.listdir(TRACES):
        if not n.endswith('.json'):
            continue
        try:
            t = json.load(open(os.path.join(TRACES, n)))
        except Exception:                                  # noqa: BLE001
            continue
        tid = t.get('trace_id')
        if tid in need:
            got[tid] = t
    return got


def _mean(xs):
    vals = [x for x in xs if x is not None]
    return (sum(vals) / len(vals)) if vals else None


def outcome_of(feats, spec, fam):
    """(grade, detail) — the ONLY place ground truth is consumed."""
    if fam == 'financial_analysis':
        return grade_financial(feats.answer, spec)
    if fam == 'research_synthesis':
        return grade_research(feats.decisions, spec)
    if fam == 'code_review':
        return grade_code(feats.decisions, spec)
    return 'ungradeable', {}


def dim_distances(fa, fb, fam, ga, gb, da, db):
    """Per-dimension distances between two executions."""
    numeric = (fam == 'financial_analysis')
    d_a, _ = D.map_distance(fa.assertions, fb.assertions, numeric=numeric)
    d_d, _ = D.map_distance(fa.decisions, fb.decisions, numeric=numeric)
    d_ac = D.total_variation(fa.actions, fb.actions)
    if fam == 'financial_analysis':
        d_o = D.outcome_distance_financial(
            ga, gb, (fa.answer.value if fa.answer
                     and fa.answer.state == 'observed' else None),
            (fb.answer.value if fb.answer
             and fb.answer.state == 'observed' else None))
    elif fam == 'research_synthesis':
        d_o = D.outcome_distance_research(ga, gb)
    else:
        d_o = D.outcome_distance_code(
            ga, gb, da.get('identified'), db.get('identified'),
            max(1, (da.get('gradeable') or 1)))
    return {'assertions': d_a, 'decisions': d_d, 'actions': d_ac,
            'outcomes': d_o}


def run(limit=None, aggregation='max', args=None):
    specs = load_specs()
    rows = [json.loads(l) for l in open(PER_TRACE) if l.strip()]
    if args.balanced:
        # Family-balanced slice: the file is grouped by family, so a plain
        # head(--limit) is a single-family sample and yields a misleading
        # comparison against corpus-wide frozen rates.
        byfam = collections.defaultdict(list)
        for i, r in enumerate(rows):
            byfam[r.get('task_family')].append(i)
        keep = sorted(i for idxs in byfam.values() for i in idxs[:args.balanced])
        rows = [rows[i] for i in keep]
    elif limit:
        rows = rows[:limit]
    traces = index_traces()
    cache = {}

    def feats(tid, fam):
        k = (tid, fam)
        if k not in cache:
            tr = traces.get(tid)
            if tr is None:
                cache[k] = None
            else:
                fid = tid.split('.')[0]
                spec = specs.get(fid) or {}
                cache[k] = extract_features_v6(tr, spec, fam)
        return cache[k]

    out_rows = []
    skipped = collections.Counter()
    for r in rows:
        fam = r.get('task_family')
        tid = r['trace_id']
        fid = tid.split('.')[0]
        spec = specs.get(fid) or {}
        fl = feats(tid, fam)
        if fl is None:
            skipped['lep_trace_missing'] += 1
            continue
        gl, dl = outcome_of(fl, spec, fam)

        refs = r.get('reference_set') or []
        ben_feats, ben_grades, ben_dists = [], [], []
        for ref in refs:
            fb = feats(ref, fam)
            if fb is None:
                skipped['benign_trace_missing'] += 1
                continue
            gb, db = outcome_of(fb, spec, fam)
            ben_feats.append(fb)
            ben_grades.append(gb)
            ben_dists.append(dim_distances(fl, fb, fam, gl, gb, dl, db))
        if not ben_feats:
            skipped['no_benign_reference'] += 1
            continue

        # benign-to-benign thresholds (requirement 6: calibrate per dimension
        # ONLY from benign variation)
        b2b = []
        for i in range(len(ben_feats)):
            for j in range(i + 1, len(ben_feats)):
                gi, di = outcome_of(ben_feats[i], spec, fam)
                gj, dj = outcome_of(ben_feats[j], spec, fam)
                b2b.append(dim_distances(ben_feats[i], ben_feats[j], fam,
                                         gi, gj, di, dj))
        thr = U.benign_thresholds(b2b)
        # The reference distance MUST come from BENIGN-TO-BENIGN pairs
        # (frozen: component_benign = mean over pair_benign).  Passing the
        # per-reference LEP distances here compared the LEP against a mean that
        # included itself, collapsing every delta toward zero.
        lep_mean = {d: _mean([x.get(d) for x in ben_dists]) for d in U.DIMS}
        pd = U.per_dimension(lep_mean, b2b, thr, aggregation=aggregation)
        u = U.union(pd)

        m3 = r.get('m3_reach_class')
        m3_ok = m3 in ('verified', 'plausible')
        m4h = m4_matched.m4_historical(gl, m3_ok)
        cls = m4_matched.classify_matched(gl, ben_grades, m3_ok)

        out_rows.append({
            'trace_id': tid,
            'task_family': fam,
            'lep_code': r.get('lep_code'),
            'topology': r.get('topology'),
            'propagation_mode': r.get('propagation_mode'),
            'm3_reach_class': m3,
            'delivery_verified': r.get('delivery_verified'),
            'lep_grade': gl,
            'benign_grades': ben_grades,
            'per_dimension': pd,
            'union': u,
            'm1_union': u['divergent'],
            'm1_historical': r.get('m1_whole_trace_positive'),
            'm2_historical': r.get('m2_cross_agent_positive'),
            'm3_historical': m3_ok,
            'm4_historical_v6': m4h,
            'm4_historical_frozen': r.get('m4_task_failure'),
            'm4_class': cls['class'],
            'm4_matched': cls['class'] == m4_matched.DEGRADED,
            'm4_detail': cls,
        })

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, 'per_trace_v6.jsonl'), 'w') as fh:
        for r in out_rows:
            fh.write(json.dumps(r) + '\n')

    summary = U.summarise(out_rows)
    summary['skipped'] = dict(skipped)
    summary['m4'] = m4_matched.summarise(
        [{'class': r['m4_class'], 'lep_grade': r['lep_grade'],
          'm3_reach_class': r['m3_reach_class']} for r in out_rows])
    summary['historical_comparison'] = _historical(out_rows, rows)
    summary['aggregation'] = aggregation
    summary['n_rows'] = len(out_rows)
    with open(os.path.join(OUT, 'summary_v6.json'), 'w') as fh:
        json.dump(summary, fh, indent=2)
    return summary


def _historical(out_rows, frozen_rows):
    """Old vs new on the SAME rows (only rows present in both)."""
    n = len(out_rows)
    m1v = sum(1 for r in out_rows if r['m1_union'])
    m1f = sum(1 for r in out_rows if r.get('m1_historical'))
    dis = sum(1 for r in out_rows
              if bool(r.get('m1_historical')) != bool(r['m1_union']))
    return {
        'n': n,
        'M1_v6_union': m1v, 'M1_v6_rate': m1v / max(1, n),
        'M1_frozen': m1f, 'M1_frozen_rate': m1f / max(1, n),
        'M1_disagreements': dis,
        'M4_frozen': sum(1 for r in out_rows if r.get('m4_historical_frozen')),
        'M4_historical_v6': sum(1 for r in out_rows
                                if r.get('m4_historical_v6')),
        'M4_matched': sum(1 for r in out_rows if r.get('m4_matched')),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--balanced', type=int, default=0,
                    help='take N rows per task family')
    ap.add_argument('--aggregation', default='mean',
                    choices=['mean', 'min', 'median'])
    a = ap.parse_args()
    s = run(a.limit, a.aggregation, a)
    print(json.dumps({k: v for k, v in s.items()
                      if k in ('n_rows', 'n', 'union_divergence_count',
                               'union_divergence_rate',
                               'dimension_divergence_rate',
                               'dimension_evaluable_count',
                               'dimension_unavailable_count',
                               'union_zero_threshold_only_count',
                               'historical_comparison', 'skipped')},
                     indent=2))


if __name__ == '__main__':
    main()
