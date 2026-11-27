"""
candspec measurement pipeline — row ledger over the P1 frame (16,773).

Read-only over everything except analysis/behavioral_divergence_final/out/.
Wraps v7 extraction + the RT normalizers + the separated action diagnostics,
and reuses the frozen union methodology (per-dimension benign thresholds,
logical union, NO composite).  Writes per_trace_candspec.jsonl + summary.

Row fields (the ledger is the join table for every downstream metric):
  trace_id, task_family, lep_code, topology, propagation_mode,
  per_dimension{d: evaluable/threshold/delta/divergent/zero_threshold},
  union{divergent, triggered_dimensions, evaluable, unknown,
        zero_threshold_triggered},
  action_diag{count_diff, retry_mult, order_dist, failures},
  lep_grade, benign_grades, m4_class, m4_matched,
  feature_coverage{dimension: n_evaluable_keys / n_keys} (RT-6 + missing-
  evidence accounting).
"""
from __future__ import annotations

import json
import os
import sys
from typing import Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_FINAL = os.path.abspath(os.path.join(_HERE, '..'))
sys.path.insert(0, _HERE)
sys.path.insert(0, _FINAL)
import common                                  # noqa: E402

sys.path.insert(0, os.path.join(common.V7_DIR, 'extractors'))
sys.path.insert(0, common.V6_DIR)
P = common.pipelines()
V6D, V6U, V6G, M4 = P['V6D'], P['V6U'], P['V6G'], P['M4']

import candspec_features as CF                    # noqa: E402


def load_frame_rows() -> List[Dict]:
    """The P1 frame: the 16,773 comparable LEP rows with their reference
    sets, from the consolidated downstream per-trace ledger (the same input
    the v7 pipeline reads; its row count is pinned by verify.py)."""
    per_trace = os.path.join(common.CONS, 'downstream_results',
                             'per_trace.jsonl')
    rows = [json.loads(l) for l in open(per_trace) if l.strip()]
    return rows


def _trace_index(rows: List[Dict]) -> Dict[str, Dict]:
    need: set = set()
    for r in rows:
        need.add(r['trace_id'])
        need.update(r.get('reference_set') or [])
    idx: Dict[str, Dict] = {}
    for line in open(os.path.join(common.VALIDATION_CACHE,
                                  'trace_index.jsonl')):
        d = json.loads(line)
        if d['trace_id'] in need:
            idx[d['trace_id']] = d['path']
    for tid, path in idx.items():
        if os.path.exists(path):
            idx[tid] = json.load(open(path))
        else:
            idx[tid] = None
    return idx


def _mean(xs):
    vals = [x for x in xs if x is not None]
    return (sum(vals) / len(vals)) if vals else None


def _outcome_grade(feats, spec, fam):
    """Grading = the sole GT consumer (M4 boundary).  The grader aligns on
    the SPEC's semantic issue ids, so it reads the semantic-keyed copies;
    the canonical (positional) copies are the comparison/judge surface."""
    if fam == 'financial_analysis':
        return V6G.grade_financial(feats.answer, spec)
    if fam == 'research_synthesis':
        return V6G.grade_research(feats.decisions, spec)
    if fam == 'code_review':
        return V6G.grade_code(feats.decisions_semantic, spec)
    return 'ungradeable', {}


def run(rows: List[Dict], out_prefix: str = 'candspec',
        aggregation: str = 'mean',
        policy: Optional[Dict] = None,
        partition_fixture_set: Optional[set] = None) -> Dict:
    specs = common.load_specs()
    if partition_fixture_set is not None:
        rows = [r for r in rows
                if common.fixture_id_of(r['trace_id']) in partition_fixture_set]
    traces = _trace_index(rows)
    cache: Dict = {}
    out_rows: List[Dict] = []
    skipped: Dict[str, int] = {}

    def feats(tid, fam):
        k = (tid, fam)
        if k not in cache:
            tr = traces.get(tid)
            if tr is None:
                cache[k] = None
            else:
                fid = common.fixture_id_of(tid)
                cache[k] = CF.extract_candspec(tr, specs.get(fid) or {}, fam,
                                                policy=policy)
        return cache[k]

    for r in rows:
        fam = r.get('task_family')
        tid = r['trace_id']
        spec = specs.get(common.fixture_id_of(tid)) or {}
        fl = feats(tid, fam)
        if fl is None:
            skipped['lep_trace_missing'] = \
                skipped.get('lep_trace_missing', 0) + 1
            continue
        gl, dl = _outcome_grade(fl, spec, fam)
        refs = r.get('reference_set') or []
        # RT-1: the code outcome distance is normalized over the REQUIRED
        # issue set (task-side), not the observed decision count.
        nri = len(spec.get('required_issues') or []) if fam == 'code_review' \
            else 0
        ben_feats, ben_grades, ben_dists = [], [], []
        for ref in refs:
            fb = feats(ref, fam)
            if fb is None:
                skipped['benign_trace_missing'] = \
                    skipped.get('benign_trace_missing', 0) + 1
                continue
            gb, db = _outcome_grade(fb, spec, fam)
            ben_feats.append(fb)
            ben_grades.append(gb)
            ben_dists.append(CF.classify_dimensions(fl, fb, fam, gl, gb,
                                                    n_required_issues=nri))
        if not ben_feats:
            skipped['no_benign_reference'] = \
                skipped.get('no_benign_reference', 0) + 1
            continue
        b2b = []
        for i in range(len(ben_feats)):
            for j in range(i + 1, len(ben_feats)):
                gi, _ = _outcome_grade(ben_feats[i], spec, fam)
                gj, _ = _outcome_grade(ben_feats[j], spec, fam)
                b2b.append(CF.classify_dimensions(ben_feats[i], ben_feats[j],
                                                  fam, gi, gj,
                                                  n_required_issues=nri))
        thr = V6U.benign_thresholds(b2b)
        lep_mean = {d: _mean([x.get(d) for x in ben_dists]) for d in V6U.DIMS}
        pd = V6U.per_dimension(lep_mean, b2b, thr, aggregation=aggregation)
        u = V6U.union(pd)
        m3 = r.get('m3_reach_class')
        cls = M4.classify_matched(gl, ben_grades, m3 in ('verified',
                                                          'plausible'))
        diag = CF.action_diagnostic_pair(
            traces.get(tid) or {}, traces.get(refs[0]) or {})
        # missing-evidence accounting (spec §2): per-dimension evaluable key
        # counts, so unavailable measurements are reported, never forced.
        coverage = {
            'assertions': {'n_keys': len(fl.assertions),
                           'n_observed': sum(
                               1 for v in fl.assertions.values()
                               if getattr(v, 'state', None) == 'observed')},
            'decisions': {'n_keys': len(fl.decisions),
                          'n_observed': sum(
                              1 for v in fl.decisions.values()
                              if getattr(v, 'state', None) == 'observed')},
            'actions': {'n_keys': len(fl.actions)},
            'outcomes': {'answer_state':
                         (fl.answer.state if fl.answer else 'no_answer_key')},
        }
        out_rows.append({
            'trace_id': tid, 'task_family': fam,
            'lep_code': r.get('lep_code'), 'topology': r.get('topology'),
            'propagation_mode': r.get('propagation_mode'),
            'm3_reach_class': m3, 'delivery_verified':
                r.get('delivery_verified'),
            'lep_grade': gl, 'benign_grades': ben_grades,
            'per_dimension': pd, 'union': u, 'm1_union': u['divergent'],
            'm1_historical': r.get('m1_whole_trace_positive'),
            'm4_class': cls['class'],
            'm4_matched': cls['class'] == M4.DEGRADED, 'm4_detail': cls,
            # frozen-frame M2/M4 fields (existing propagation definitions;
            # injection-boundary-gated, cross-agent role-gated — reused as
            # recorded, never recomputed):
            'm2_cross_agent_positive': r.get('m2_cross_agent_positive'),
            'm2_nonzero_threshold': r.get('m2_nonzero_threshold'),
            'm2_scope': r.get('m2_scope'),
            'm4_task_failure_historical': r.get('m4_task_failure'),
            'm1_dd0_positive': r.get('m1_dd0_positive'),
            'origin_roles': r.get('origin_roles'),
            'action_diag': diag,
            'feature_coverage': coverage,
            'decision_source': fl.decision_source,
        })

    summary = V6U.summarise(out_rows)
    summary.update({
        'skipped': skipped, 'n_rows': len(out_rows),
        'aggregation': aggregation, 'version': out_prefix,
        'policy': policy or {},
        'judge_construct': CF.JUDGE_CONSTRUCT,
        'm4': M4.summarise([{'class': x['m4_class'],
                              'lep_grade': x['lep_grade'],
                              'm3_reach_class': x['m3_reach_class']}
                             for x in out_rows]),
    })
    common.write_jsonl(out_rows, f'per_trace_{out_prefix}.jsonl')
    common.write_json(summary, f'summary_{out_prefix}.json')
    print(json.dumps({k: summary.get(k) for k in
                      ('n_rows', 'union_divergence_count',
                       'union_divergence_rate', 'dimension_divergence_rate',
                       'union_zero_threshold_only_count')}, indent=2))
    return summary


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--prefix', default='candspec')
    ap.add_argument('--aggregation', default='mean',
                    choices=['mean', 'min', 'median'])
    a = ap.parse_args()
    rows = load_frame_rows()
    if a.limit:
        rows = rows[:a.limit]
    run(rows, out_prefix=a.prefix, aggregation=a.aggregation)
