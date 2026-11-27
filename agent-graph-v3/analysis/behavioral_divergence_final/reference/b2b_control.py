#!/usr/bin/env python3.11
"""
G5 pre-registered statistic: the TRUE benign-to-benign negative control,
per implementation (brief §5 'Benign-to-benign negative controls'; the
S13 synthetic suite generalizes this on the dev side; this is the
heldout-frame version over all four implementations).

A benign execution used as the probe side against the OTHER benigns of
the same cell must NOT fire the implementation's union.  Any fire is a
false positive of the measurement on a pure-negative population:
  FPR = n_fired / n_evaluable
where a probe whose cell is uncalibratable (no evaluable dimension /
frozen comparable=False) is EXCLUDED from both numerator and
denominator — undetermined is never a negative (missing is not zero).

Each implementation runs through ITS OWN machinery (the control, not a
re-implementation):
  candspec  CF.extract_candspec + CF.classify_dimensions + V6U union
  v7        M7.extract_features_v7 + v7 dim-distances + V6U union
  v6        F6.extract_features_v6 + v6 dim-distances + V6U union
  frozen    FROZEN.measure_configuration_v4 (byte-pinned V4.1)

Population: the heldout partition's P1 frame rows with >=2 benign
references (fixture-disjoint; all such rows; 3 families x 3 topologies
x 3 propagation modes x 4 LEPs covered).  Probe = refs[0] (a benign),
reference set = refs[1:] — the S13 control convention.  No tuning of
the population: it is the surface the frozen partition already fixed.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
FINAL = os.path.abspath(os.path.join(_HERE, '..'))
sys.path.insert(0, FINAL)
import common
sys.path.insert(0, os.path.join(common.V7_DIR, 'extractors'))
sys.path.insert(0, common.V6_DIR)
sys.path.insert(0, os.path.join(FINAL, 'extractors'))
import candspec_features as CF            # noqa: E402
import measure as M7                      # noqa: E402
import features_v6 as F6                  # noqa: E402


def wilson(k: int, n: int, z: float = 1.959964) -> Optional[List[float]]:
    if n <= 0:
        return None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [max(0.0, c - m), min(1.0, c + m)]


def _mean(xs):
    vals = [x for x in xs if x is not None]
    return (sum(vals) / len(vals)) if vals else None


def _population() -> List[Dict[str, Any]]:
    part = json.load(open(os.path.join(common.OUT,
                                       'partitions_final.json')))
    held = {k for k, v in part['fixtures'].items()
            if (v.get('partition') if isinstance(v, dict) else v)
            == 'heldout'}
    rows = [json.loads(l) for l in
            open(os.path.join(common.CONS, 'downstream_results',
                              'per_trace.jsonl')) if l.strip()]
    return [r for r in rows
            if r['trace_id'].split('.')[0] in held
            and len(r.get('reference_set') or []) >= 2]


def _traces(pop: List[Dict]) -> Dict[str, Optional[Dict]]:
    idx = {json.loads(l)['trace_id']: json.loads(l)['path']
           for l in open(os.path.join(common.VALIDATION_CACHE,
                                     'trace_index.jsonl')) if l.strip()}
    need: set = set()
    for r in pop:
        need.update(r.get('reference_set') or [])
    out: Dict[str, Optional[Dict]] = {}
    for tid in need:
        p = idx.get(tid)
        out[tid] = json.load(open(p)) if p and os.path.exists(p) \
            else None
    return out


def _nri(spec: Dict, fam: str) -> int:
    return len(spec.get('required_issues') or []) \
        if fam == 'code_review' else 0


def _grade(feats, spec: Dict, fam: str) -> str:
    """The implementation's own outcome grading (GT consumer, M4
    boundary) — mirrors candspec_pipeline._outcome_grade."""
    P = common.pipelines()
    V6G = P['V6G']
    if fam == 'financial_analysis':
        return V6G.grade_financial(feats.answer, spec)[0]
    if fam == 'research_synthesis':
        return V6G.grade_research(feats.decisions, spec)[0]
    if fam == 'code_review':
        return V6G.grade_code(getattr(feats, 'decisions_semantic',
                                      feats.decisions), spec)[0]
    return 'ungradeable'


class _ExtractorCache:
    """Global (impl, trace) feature cache: benign references are shared
    across rows of the same cell, so extract once per trace."""

    def __init__(self):
        self.c: Dict[Any, Any] = {}
        self.hits = 0

    def get(self, impl: str, tid: str, tr: Dict, spec: Dict, fam: str
            ) -> Any:
        k = (impl, id(tr))
        if k in self.c:
            self.hits += 1
            return self.c[k]
        if impl == 'candspec':
            f = CF.extract_candspec(tr, spec, fam)
        elif impl == 'v7':
            f = M7.extract_features_v7(tr, spec, fam)
        else:
            f = F6.extract_features_v6(tr, spec, fam)
        self.c[k] = f
        return f


def _dist_candspec(fa, fb, spec, fam):
    return CF.classify_dimensions(fa, fb, fam, _grade(fa, spec, fam),
                                  _grade(fb, spec, fam),
                                  n_required_issues=_nri(spec, fam))


def _dist_v6v7(impl: str, fa, fb, spec, fam):
    P = common.pipelines()
    V6D = P['V6D']
    numeric = (fam == 'financial_analysis')
    d_a, _ = V6D.map_distance(fa.assertions, fb.assertions, numeric=numeric)
    d_d, _ = V6D.map_distance(fa.decisions, fb.decisions, numeric=numeric)
    d_ac = V6D.total_variation(fa.actions, fb.actions)
    ga, gb = _grade(fa, spec, fam), _grade(fb, spec, fam)
    if fam == 'financial_analysis':
        d_o = V6D.outcome_distance_financial(
            ga, gb,
            (fa.answer.value if fa.answer and
             fa.answer.state == 'observed' else None),
            (fb.answer.value if fb.answer and
             fb.answer.state == 'observed' else None))
    elif fam == 'research_synthesis':
        d_o = V6D.outcome_distance_research(ga, gb)
    else:
        d_o = V6D.outcome_distance_code(
            ga, gb, _ident(fa, fam), _ident(fb, fam),
            max(1, _nri(spec, fam)))
    return {'assertions': d_a, 'decisions': d_d, 'actions': d_ac,
            'outcomes': d_o}


def _ident(feats, fam) -> int:
    if fam != 'code_review':
        return 1
    return sum(1 for v in feats.decisions.values()
               if getattr(v, 'state', None) == 'observed'
               and getattr(v, 'value', None) in
               ('asserted', 'repair_proposed', 'repair_performed'))


def _b2b_union_via_v6u(extract, dist, probe, others, spec, fam
                       ) -> Optional[Dict[str, Any]]:
    """Machinery-faithful B2B: the probe plays the LEP slot.  θ is
    calibrated over the REFERENCE pool only (pairs among `others`),
    exactly as candspec_pipeline/v7/v6 do for a LEP row — the probe is
    NOT part of the calibration pool.  S13 (dev) uses the same
    convention.  Firing the probe here is a false positive on a pure
    benign population."""
    V6U = common.pipelines()['V6U']
    f0 = extract(probe)
    fr = [extract(x) for x in others]
    b2b = [dist(fr[i], fr[j], spec, fam)
           for i in range(len(fr)) for j in range(i + 1, len(fr))]
    pdists = [dist(f0, fb, spec, fam) for fb in fr]
    thr = V6U.benign_thresholds(b2b)
    lep_mean = {d: _mean([x.get(d) for x in pdists]) for d in V6U.DIMS}
    pd = V6U.per_dimension(lep_mean, b2b, thr, aggregation='mean')
    return V6U.union(pd)


def run() -> Dict[str, Any]:
    t0 = time.time()
    P = common.pipelines()
    V6U, FROZEN = P['V6U'], P['FROZEN']
    specs = common.load_specs()
    pop = _population()
    tr = _traces(pop)
    cache = _ExtractorCache()
    fired: Dict[str, int] = {i: 0 for i in ('candspec', 'v6', 'v7',
                                             'frozen')}
    neval: Dict[str, int] = {i: 0 for i in fired}
    fired_dims: Dict[str, Dict[str, int]] = {i: {d: 0 for d in
                                                  common.DIMS}
                                             for i in fired}
    fam_pop: Dict[str, int] = {}
    for r in pop:
        fam = r['task_family']
        fam_pop[fam] = fam_pop.get(fam, 0) + 1
        refs = [t for t in r.get('reference_set') or []
                if tr.get(t) is not None]
        if len(refs) < 2:
            continue
        probe = tr[refs[0]]
        others = [tr[t] for t in refs[1:]]
        spec = specs.get(r['trace_id'].split('.')[0], {})
        # extractor over trace objects; cache key = (impl, id(trace-object))
        for impl in ('candspec', 'v7', 'v6'):
            ex = (lambda t, _i=impl: cache.get(_i, 'x', t, spec, fam))
            if impl == 'candspec':
                dist_fn = _dist_candspec        # (fa, fb, spec, fam)
            else:
                dist_fn = (lambda a, b, _s=None, _f=None,
                            _i=impl: _dist_v6v7(_i, a, b, spec, fam))
            u = _b2b_union_via_v6u(ex, dist_fn, probe, others, spec,
                                   fam)
            if u.get('n_evaluable', 0) == 0:
                continue
            neval[impl] += 1
            if u.get('divergent'):
                fired[impl] += 1
                for ddim in u.get('triggered_dimensions') or []:
                    fired_dims[impl][ddim] += 1
        # frozen: byte-pinned V4.1 (probe in the LEP slot)
        try:
            payload = FROZEN.measure_configuration_v4(probe, others, spec)
        except Exception:
            payload = None
        if payload is not None and payload.get('comparable'):
            neval['frozen'] += 1
            if payload.get('behavioral_anomaly') is True:
                fired['frozen'] += 1
    out: Dict[str, Any] = {
        'population': 'heldout P1 frame rows with >=2 benign '
                      'references (fixture-disjoint from dev; S13 '
                      'convention: probe = refs[0] benign, refs = the '
                      'other benigns)',
        'n_population': len(pop),
        'by_family': fam_pop,
        'note': ('FPR = union fires on a pure-negative population; '
                 'uncalibratable probes excluded from both numerator '
                 'and denominator (never a negative)'),
        'gate_G5_threshold': 0.05,
        'elapsed_s': round(time.time() - t0, 1),
    }
    for impl in ('candspec', 'v6', 'v7', 'frozen'):
        n, k = neval[impl], fired[impl]
        out[impl] = {
            'n_evaluable': n, 'n_fired': k,
            'fpr': (k / n) if n else None,
            'wilson95': wilson(k, n),
            'fired_dimensions': fired_dims[impl]
            if impl != 'frozen' else
            'unavailable (V4.1 whole-trace flag, no per-dim evaluable)',
        }
    common.write_json(out, 'b2b_control.json')
    print(json.dumps({i: out[i] for i in
                     ('candspec', 'v6', 'v7', 'frozen')}, indent=1))
    return out


if __name__ == '__main__':
    res = run()
    print(f'wrote out/b2b_control.json in {res["elapsed_s"]}s')
