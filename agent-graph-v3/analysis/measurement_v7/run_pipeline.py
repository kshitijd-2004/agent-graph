#!/usr/bin/env python3.11
"""
v7 measurement pipeline — the CANDIDATE measurement implementation.

Runs the SAME four-dimensional benign-threshold union (v6/frozen methodology,
no composite) on the consolidated dataset, but with the v7 extractors.
Writes ONLY under analysis/measurement_v7/out/.

Usage:
  python3.11 analysis/measurement_v7/run_pipeline.py --limit 500
  python3.11 analysis/measurement_v7/run_pipeline.py            # full
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import base                                  # noqa: E402

# Load the frozen + v6 modules FIRST so 'distances'/'union'/'extract_core'
# resolve to the v6 implementations (required: the v7 extractor imports
# v6's extract_core etc., and the v7 distances/ package must not shadow
# them).
P = base.import_pipelines()
FROZEN, V6F, V6D, V6U, V6G, M4 = (P['FROZEN'], P['V6F'], P['V6D'],
                                   P['V6U'], P['V6G'], P['M4'])

sys.path.insert(0, os.path.join(HERE, 'extractors'))
from research_v7 import extract_claim_verdicts_v7          # noqa: E402
from swe_v7 import extract_issue_stances_v7                # noqa: E402
from financial_v7 import extract_facts_v7, extract_final_answer_v7  # noqa: E402
from measure import extract_features_v7, pair_diagnostics        # noqa: E402


def load_specs():
    return base.load_specs()


def index_traces(need):
    idx = {}
    for line in open(os.path.join(base.VALIDATION_CACHE, 'trace_index.jsonl')):
        d = json.loads(line)
        if d['trace_id'] in need:
            idx[d['trace_id']] = d['path']
    out = {}
    for tid, path in idx.items():
        if os.path.exists(path):
            out[tid] = json.load(open(path))
    return out


def _mean(xs):
    vals = [x for x in xs if x is not None]
    return (sum(vals) / len(vals)) if vals else None


def outcome_of(feats, spec, fam):
    """Grading is the sole GT consumer (requirement 6)."""
    if fam == 'financial_analysis':
        return V6G.grade_financial(feats.answer, spec)
    if fam == 'research_synthesis':
        return V6G.grade_research(feats.decisions, spec)
    if fam == 'code_review':
        return V6G.grade_code(feats.decisions, spec)
    return 'ungradeable', {}


def dim_distances(fa, fb, fam, ga, gb, da, db):
    numeric = (fam == 'financial_analysis')
    d_a, _ = V6D.map_distance(fa.assertions, fb.assertions, numeric=numeric)
    d_d, _ = V6D.map_distance(fa.decisions, fb.decisions, numeric=numeric)
    d_ac = V6D.total_variation(fa.actions, fb.actions)
    if fam == 'financial_analysis':
        d_o = V6D.outcome_distance_financial(
            ga, gb,
            (fa.answer.value if fa.answer and fa.answer.state == 'observed'
             else None),
            (fb.answer.value if fb.answer and fb.answer.state == 'observed'
             else None))
    elif fam == 'research_synthesis':
        d_o = V6D.outcome_distance_research(ga, gb)
    else:
        d_o = V6D.outcome_distance_code(ga, gb, da.get('identified'),
                                        db.get('identified'),
                                        max(1, (da.get('gradeable') or 1)))
    return {'assertions': d_a, 'decisions': d_d, 'actions': d_ac,
            'outcomes': d_o}


def _partition_fixture_sets(partition):
    """fixture -> partition from the frozen out/partitions.json; returns the
    fixture sets for 'dev' and 'heldout'.  'full' ignores the partition."""
    if partition == 'full':
        return None
    p = os.path.join(HERE, 'out', 'partitions.json')
    if not os.path.exists(p):
        raise SystemExit(f'partition {partition} requested but '
                         f'{p} not found; run partitioning.py first')
    pj = json.load(open(p))
    fx_dev, fx_ho = set(), set()
    for fx, rec in pj['fixtures'].items():
        (fx_ho if rec['partition'] == 'heldout' else fx_dev).add(fx)
    return fx_dev if partition == 'dev' else fx_ho


def run(limit=None, aggregation='mean', per_family=0, out_prefix='v7',
        policy=None, partition='full'):
    specs = base.load_specs()
    per_trace = os.path.join(base.CONS, 'downstream_results',
                             'per_trace.jsonl')
    rows = [json.loads(l) for l in open(per_trace) if l.strip()]
    fx_set = _partition_fixture_sets(partition)
    if fx_set is not None:
        rows = [r for r in rows if r['trace_id'].split('.')[0] in fx_set]
    if per_family:
        import collections
        byfam = collections.defaultdict(list)
        for i, r in enumerate(rows):
            byfam[r.get('task_family')].append(i)
        keep = sorted(i for idxs in byfam.values() for i in idxs[:per_family])
        rows = [rows[i] for i in keep]
    elif limit:
        rows = rows[:limit]
    need = set()
    for r in rows:
        need.add(r['trace_id'])
        need.update(r.get('reference_set') or [])
    traces = index_traces(need)
    cache: Dict = {}

    def feats(tid, fam):
        k = (tid, fam)
        if k not in cache:
            tr = traces.get(tid)
            if tr is None:
                cache[k] = None
            else:
                fid = tid.split('.')[0]
                cache[k] = extract_features_v7(tr, specs.get(fid) or {}, fam,
                                                policy=policy)
        return cache[k]

    out_rows = []
    skipped = {}
    for r in rows:
        fam = r.get('task_family')
        tid = r['trace_id']
        spec = specs.get(tid.split('.')[0]) or {}
        fl = feats(tid, fam)
        if fl is None:
            skipped['lep_trace_missing'] = skipped.get('lep_trace_missing', 0) + 1
            continue
        gl, dl = outcome_of(fl, spec, fam)
        refs = r.get('reference_set') or []
        ben_feats, ben_grades, ben_dists = [], [], []
        for ref in refs:
            fb = feats(ref, fam)
            if fb is None:
                skipped['benign_trace_missing'] = \
                    skipped.get('benign_trace_missing', 0) + 1
                continue
            gb, db = outcome_of(fb, spec, fam)
            ben_feats.append(fb)
            ben_grades.append(gb)
            ben_dists.append(dim_distances(fl, fb, fam, gl, gb, dl, db))
        if not ben_feats:
            skipped['no_benign_reference'] = \
                skipped.get('no_benign_reference', 0) + 1
            continue
        b2b = []
        for i in range(len(ben_feats)):
            for j in range(i + 1, len(ben_feats)):
                gi, di = outcome_of(ben_feats[i], spec, fam)
                gj, dj = outcome_of(ben_feats[j], spec, fam)
                b2b.append(dim_distances(ben_feats[i], ben_feats[j], fam,
                                         gi, gj, di, dj))
        thr = V6U.benign_thresholds(b2b)
        lep_mean = {d: _mean([x.get(d) for x in ben_dists]) for d in V6U.DIMS}
        pd = V6U.per_dimension(lep_mean, b2b, thr, aggregation=aggregation)
        u = V6U.union(pd)
        m3 = r.get('m3_reach_class')
        m3_ok = m3 in ('verified', 'plausible')
        cls = M4.classify_matched(gl, ben_grades, m3_ok)
        diag = {}
        if ben_feats:
            diag = pair_diagnostics(traces.get(tid, {}),
                                    traces.get(refs[0], {}))
        out_rows.append({
            'trace_id': tid, 'task_family': fam,
            'lep_code': r.get('lep_code'), 'topology': r.get('topology'),
            'propagation_mode': r.get('propagation_mode'),
            'm3_reach_class': m3, 'delivery_verified': r.get('delivery_verified'),
            'lep_grade': gl, 'benign_grades': ben_grades,
            'per_dimension': pd, 'union': u, 'm1_union': u['divergent'],
            'm1_historical': r.get('m1_whole_trace_positive'),
            'm4_class': cls['class'], 'm4_matched': cls['class'] == M4.DEGRADED,
            'm4_detail': cls, 'action_diagnostics': diag,
        })

    out = os.path.join(HERE, 'out')
    os.makedirs(out, exist_ok=True)
    pt_path = os.path.join(out, f'per_trace_{out_prefix}.jsonl')
    with open(pt_path, 'w') as fh:
        for r in out_rows:
            fh.write(json.dumps(r) + '\n')
    summary = V6U.summarise(out_rows)
    summary.update({
        'skipped': skipped, 'n_rows': len(out_rows),
        'aggregation': aggregation, 'version': out_prefix,
        'policy': policy or {},
        'm4': M4.summarise([{'class': r['m4_class'],
                              'lep_grade': r['lep_grade'],
                              'm3_reach_class': r['m3_reach_class']}
                             for r in out_rows]),
    })
    s_path = os.path.join(out, f'summary_{out_prefix}.json')
    json.dump(summary, open(s_path, 'w'), indent=2)
    print(json.dumps({k: summary.get(k) for k in
                      ('n_rows', 'union_divergence_count',
                       'union_divergence_rate', 'dimension_divergence_rate',
                       'union_zero_threshold_only_count')},
                     indent=2))
    print(f'wrote {pt_path} and {s_path}')
    return summary


from typing import Dict  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--per-family', type=int, default=0)
    ap.add_argument('--aggregation', default='mean',
                    choices=['mean', 'min', 'median'])
    ap.add_argument('--prefix', default='v7')
    ap.add_argument('--research-polarity', default='rubric',
                    choices=['rubric', 'v6'])
    ap.add_argument('--partition', default='full',
                    choices=['dev', 'heldout', 'full'])
    a = ap.parse_args()
    prefix = a.prefix if a.partition == 'full' else f'v7_{a.partition}'
    run(a.limit, a.aggregation, a.per_family, prefix,
        policy={'research_polarity': a.research_polarity},
        partition=a.partition)


if __name__ == '__main__':
    main()
