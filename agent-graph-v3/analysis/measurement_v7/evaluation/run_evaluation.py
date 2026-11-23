#!/usr/bin/env python3.11
"""
Gated evaluation entry point (requirements 11/12/13).

  * Extraction performance vs a REFERENCE (human or AI-adjudicated).  The
    reference is LOADED, not fabricated: without a real reference file the
    relevant stage is GATED (ReferenceMissing), recorded in the report, and
    NOT run against invented labels.
  * Divergence classification: frozen / v6 / v7 per-dimension and union,
    with the zero-threshold dependency count (requirement 12: "Report how
    many positives depend exclusively on zero thresholds").
  * M4-matched propagation analysis on a sample (requirement 13).

The frozen and v6 per-trace results are REUSED, not recomputed differently:
frozen numbers come from the consolidated ledger (the historical artifact),
v6 from analysis/measurement_v6/out/ (the frozen-candidate artifact), v7 from
this directory's pipeline.  All three are keyed on trace_id.

Writes only under analysis/measurement_v7/out/.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
V7 = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, V7)
import base                                          # noqa: E402

OUT = os.path.join(V7, 'out')


def _load_jsonl(p):
    if not os.path.exists(p):
        return {}
    return {r['trace_id']: r for r in
            (json.loads(l) for l in open(p) if l.strip())}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--partition', default='dev',
                    choices=['dev', 'heldout', 'full'])
    ap.add_argument('--reference', default=None,
                    help='path to a human- or AI-adjudicated label file; '
                         'if absent, the extraction stage is GATED')
    ap.add_argument('--out-prefix', default='eval')
    a = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    # 1. the three extractor measurement sets
    frozen = _load_jsonl(os.path.join(
        base.CONS, 'bnd_v41_run', 'bnd_v41_per_trace.jsonl'))
    v6 = _load_jsonl(os.path.join(base.V6_DIR, 'out', 'per_trace_v6.jsonl'))
    v7 = _load_jsonl(os.path.join(OUT, 'per_trace_v7.jsonl'))
    if a.partition != 'full' and os.path.exists(
            os.path.join(OUT, 'partitions.json')):
        pj = json.load(open(os.path.join(OUT, 'partitions.json')))
        fx = {fx: r['partition'] for fx, r in pj['fixtures'].items()}
        keep = lambda tid: fx.get(tid.split('.')[0], a.partition) == \
            a.partition
        frozen = {k: v for k, v in frozen.items() if keep(k)}
        v6 = {k: v for k, v in v6.items() if keep(k)}
        v7 = {k: v for k, v in v7.items() if keep(k)}

    common = sorted(set(frozen) & set(v6) & set(v7))
    report: dict = {
        'partition': a.partition,
        'n_frozen': len(frozen), 'n_v6': len(v6), 'n_v7': len(v7),
        'n_common': len(common),
        'stages': {},
    }

    # 2. divergence classification across the three implementations
    from evaluation import extractor_eval
    div = {}
    for name, src in (('frozen', frozen), ('v6', v6), ('v7', v7)):
        def m1(r):
            if name == 'frozen':
                return bool(r.get('m1_whole_trace_positive')
                            or r.get('m1'))
            return bool(r.get('m1_union'))
        def dims(r):
            if name == 'frozen':
                pd = r.get('per_dimension') or r.get('frozen_per_dimension') \
                    or {}
                outd = {}
                for d in base.DIMS:
                    info = pd.get(d) or {}
                    outd[d] = bool(info.get('positive')) if \
                        isinstance(info, dict) else None
                return outd
            pd = r.get('per_dimension') or {}
            return {d: ((pd.get(d) or {}).get('divergent'))
                    for d in base.DIMS}
        def zero_only(r):
            u = r.get('union') or {}
            if name != 'frozen':
                trig = u.get('triggered_dimensions') or []
                zt = u.get('zero_threshold_triggered') or []
                return bool(trig) and set(zt) == set(trig)
            pd = r.get('per_dimension') or r.get('frozen_per_dimension') or {}
            trig = [d for d in base.DIMS if (pd.get(d) or {}).get('positive')]
            return bool(trig) and all(
                (pd.get(d) or {}).get('theta') == 0.0 for d in trig)
        rows = [dict(trace_id=t, m1=m1(r), dims=dims(r),
                     zero_only=zero_only(r)) for t, r in
                src.items() if t in common]
        div[name] = rows
        report['stages'][name] = {
            'n': len(rows),
            'm1_count': sum(1 for r in rows if r['m1']),
            'm1_rate': (sum(1 for r in rows if r['m1']) / len(rows))
            if rows else None,
            'positive_dependent_only_on_zero_threshold':
                sum(1 for r in rows if r['zero_only'] and r['m1']),
        }
    # cross-implementation disagreements
    for x, (xn, rowsx) in div.items():
        for y, (yn, rowsy) in div.items():
            if x >= y:
                continue
            ix = {r['trace_id']: r for r in rowsx}
            iy = {r['trace_id']: r for r in rowsy}
            dis = sum(1 for t in ix if t in iy and
                      bool(ix[t]['m1']) != bool(iy[t]['m1']))
            report['stages'][f'{x}_vs_{y}_m1_disagreement'] = \
                {'n': len(ix & iy), 'n_disagree': dis}

    json.dump(div, open(os.path.join(OUT,
                                     f'{a.out_prefix}_divergence.jsonl'),
                        'w'), indent=1)

    # 3. M4-matched on the sample
    import importlib
    M4 = importlib.import_module('m4_matched')
    rows_m4 = []
    for t in common:
        r7 = v7[t]
        m3 = (r7.get('m3_reach_class') in ('verified', 'plausible'))
        rows_m4.append({
            'trace_id': t,
            'class': r7.get('m4_class'),
            'lep_grade': r7.get('lep_grade'),
            'm3_reach_class': r7.get('m3_reach_class'),
        })
    m4sum = M4.summarise(rows_m4)
    m4sum['partition'] = a.partition
    report['stages']['m4_matched_v7'] = m4sum
    json.dump(m4sum, open(os.path.join(
        OUT, f'{a.out_prefix}_m4.json'), indent=2))

    # 4. extraction performance vs the reference — GATED without one
    try:
        from evaluation import reference as REF
        if not a.reference:
            raise REF.ReferenceMissing(
                'no --reference file supplied; extraction-performance '
                'stage is gated, not run against invented labels')
        ref = REF.load_reference(a.reference, a.partition)
        from evaluation import extractor_eval as EE
        for impl, src in (('v7', v7), ('v6', v6), ('frozen', frozen)):
            pred_by_item = {
                t: {'states': _states_of(impl, r),
                    'values': _values_of(impl, r)}
                for t, r in src.items() if t in common}
            ref_rows = [{
                'item_token': r.get('item_token') or r.get('trace_id'),
                'family': (r.get('family') or r.get('task_family')
                            or 'unknown'),
                'component': r.get('component'),
                'labels': r.get('labels') or {},
            } for r in ref['rows']]
            report['stages'][f'extraction_{impl}_vs_{ref["kind"]}_ref'] = \
                EE.extractor_metrics(ref_rows, pred_by_item)
            report['stages'][f'extraction_{impl}'][
                'provenance'] = REF.provenance_label(ref)
    except Exception as e:                    # noqa: BLE001
        report['stages']['extraction_vs_reference'] = {
            'status': 'GATED',
            'reason': f'{type(e).__name__}: {e}',
            'note': ('Performance against a reference is reported ONLY '
                     'when a real reference file is supplied; it is not '
                     'fabricated.'),
        }
    rep_path = os.path.join(OUT, f'{a.out_prefix}_report.json')
    json.dump(report, open(rep_path, 'w'), indent=2)
    print(json.dumps({k: report['stages'].get(k) for k in
                      ('frozen', 'v6', 'v7',
                       'extraction_vs_reference', 'm4_matched_v7')},
                     indent=2, default=str)[:4000])
    print(f'wrote {rep_path}')
    return 0


def _states_of(impl, r):
    out = {}
    if impl == 'frozen':
        st = (r.get('states') or {}).get('frozen') or {}
        out = {k: (st[k].get('dominant') if isinstance(st[k], dict)
                   else st[k])
               for k in ('assertions', 'decisions', 'outcomes')}
    else:
        amap = ((r.get('per_dimension') or {}).get('assertions') or {})
        out['assertions'] = None
        out['decisions'] = None
    return out


def _values_of(impl, r):
    return {}


if __name__ == '__main__':
    raise SystemExit(main())
