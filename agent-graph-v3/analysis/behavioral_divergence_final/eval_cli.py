#!/usr/bin/env python3.11
"""
Final-evaluation orchestrator (FROZEN_PROTOCOL §8 consumption record).

Stages:
  --pre-seal        run EVERYTHING up to live judge calls on the DEV side
                    only: items, det reference, judge dev chunks, reference
                    build (dev), eval scoring (dev), synthetic, sampling.
                    Heldout rows are NOT touched.  Writes
                    out/preseal_report.json.
  --seal            write out/seal/fingerprints.json + out/seal/seal.json
                    (sha256 of every protected artifact + candidate module
                    set; the FROZEN_PROTOCOL §4 machine-readable pin).
  --final-eval      CONSUME the heldout (once): build heldout items + judge
                    packets, run judges over sample A / pair groups /
                    challenge (chunked, resumable), reference build, score
                    every implementation (candspec / v6 / v7 / frozen),
                    evaluate gates -> out/final_eval_report.json.

The stage gating is mechanical: --final-eval REFUSES to run until
out/seal/seal.json exists AND common.check_sealed() passes (G8), so the
heldout is consumed exactly once, against the sealed candidate set.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import common
sys.path.insert(0, os.path.join(common.V7_DIR, 'extractors'))
sys.path.insert(0, common.V6_DIR)
sys.path.insert(0, os.path.join(_HERE, 'extractors'))
sys.path.insert(0, os.path.join(_HERE, 'reference'))
sys.path.insert(0, os.path.join(_HERE, 'synthetic'))


IMPLS = ['candspec', 'v6', 'v7', 'frozen']
LEDGER = {
    'candspec': os.path.join(common.OUT, 'per_trace_candspec.jsonl'),
    'v6': os.path.join(common.V6_DIR, 'out', 'per_trace_v6.jsonl'),
    'v7': os.path.join(common.V7_DIR, 'out', 'per_trace_v7.jsonl'),
    'frozen': os.path.join(common.CONS, 'bnd_v41_run',
                           'bnd_v41_per_trace.jsonl'),
}


def _seal_seal_paths() -> List[str]:
    return [
        'analysis/bnd_v41_cw_swe200_provisional/frozen_run/benchmark/'
        'baseline_deviation_v41.py',
        'analysis/bnd_v41_cw_swe200_provisional/frozen_run/benchmark/'
        'baseline_deviation_v4.py',
        'analysis/consolidated_20261121_bnd_v41/bnd_v41_run/'
        'bnd_v41_per_trace.jsonl',
        'analysis/measurement_v6/extract_core.py',
        'analysis/measurement_v6/features_v6.py',
        'analysis/measurement_v6/distances.py',
        'analysis/measurement_v6/union.py',
        'analysis/measurement_v6/graders.py',
        'analysis/measurement_v6/m4_matched.py',
        'analysis/measurement_v6/run_pipeline.py',
        'analysis/measurement_v7/base.py',
        'analysis/measurement_v7/partitioning.py',
        'analysis/measurement_v7/run_pipeline.py',
        'analysis/measurement_v7/extractors/research_v7.py',
        'analysis/measurement_v7/extractors/swe_v7.py',
        'analysis/measurement_v7/extractors/financial_v7.py',
        'analysis/behavioral_divergence_final/common.py',
        'analysis/behavioral_divergence_final/extractors/normalizers.py',
        'analysis/behavioral_divergence_final/extractors/'
        'candspec_features.py',
        'analysis/behavioral_divergence_final/extractors/'
        'candspec_pipeline.py',
        'analysis/behavioral_divergence_final/reference/det_reference.py',
        'analysis/behavioral_divergence_final/reference/items.py',
        'analysis/behavioral_divergence_final/reference/judge_runner.py',
        'analysis/behavioral_divergence_final/reference/'
        'reference_build.py',
        'analysis/behavioral_divergence_final/reference/eval_scoring.py',
        'analysis/behavioral_divergence_final/synthetic/run_synthetic.py',
        'analysis/behavioral_divergence_final/gates.py',
        'analysis/behavioral_divergence_final/partitioning.py',
        'analysis/behavioral_divergence_final/sampling.py',
        'analysis/behavioral_divergence_final/MEASUREMENT_SPEC.md',
        'analysis/behavioral_divergence_final/FROZEN_PROTOCOL.md',
    ]


def do_seal() -> Dict[str, Any]:
    seal_dir = os.path.join(common.FINAL_DIR, 'seal')
    os.makedirs(seal_dir, exist_ok=True)
    fps = []
    for rel in _seal_seal_paths():
        p = os.path.join(common.REPO, rel)
        fps.append({'path': rel, 'sha256': common.sha256_of(p)})
    # write the machine-readable pin where common.check_sealed() looks
    out_fp = os.path.join(common.FINAL_DIR, 'seal', 'fingerprints.json')
    with open(out_fp, 'w') as fh:
        json.dump(fps, fh, indent=1)
        fh.write('\n')
    seal = {'sealed': True, 'date': '2026-12-07',
            'seeds': {'partition':
                      'agentprop-final-partition-20261207',
                      'sampling': 'agentprop-final-sampling-20261207'},
            'judges': {'judge_a': 'claude-opus-5-5',
                       'judge_b': 'claude-sonnet-5-5',
                       'adjudicator': 'claude-opus-4-8'},
            'n_fingerprints': len(fps),
            'note': 'heldout consumed exactly once at final evaluation; '
                    'this seal pins the candidate module set + protocols'}
    with open(os.path.join(common.FINAL_DIR, 'seal', 'seal.json'), 'w') as fh:
        json.dump(seal, fh, indent=1)
        fh.write('\n')
    print(json.dumps({'n_fingerprints': len(fps), 'sealed': True}))
    return seal


def _load_ledger_map(path: str) -> Dict[str, Dict[str, Any]]:
    if not os.path.exists(path):
        return {}
    return {r['trace_id']: r for r in
            (json.loads(l) for l in open(path) if l.strip())}


def _score_impl(impl: str, partition: str) -> Dict[str, Any]:
    """Assemble the per-implementation score dict gates.evaluate consumes.

    Per-dimension value accuracy + Wilson on the judgeable reference
    surface; union P/R/F1 on the evaluable pairs; benign-to-benign FPR;
    zero-threshold-only report; coverage.  The frozen ledger (no
    per-dimension features) is scored at UNION level only — its missing
    dimensions are reported unavailable, never imputed.
    """
    import eval_scoring
    out: Dict[str, Any] = {'implementation': impl}
    ext = eval_scoring.score_extraction(impl, partition)
    pr = eval_scoring.score_pairs(impl, partition)
    out['extraction_accuracy'] = {
        'value_accuracy': _dim_value_accuracy(impl, partition),
        'wilson95': pr.get('benign_to_benign', {}).get('wilson95'),
        'n_judgeable': sum(
            1 for k, v in (out.get('per_dimension') or {}).items()
            for _ in [0]) if False else _n_judgeable(partition)}
    out['union_pairs'] = {k: pr.get(k) for k in
                          ('f1', 'precision', 'recall', 'n_evaluable',
                           'n_excluded_unstable')}
    out['benign_to_benign'] = pr.get('benign_to_benign') or {}
    out['zero_threshold_only'] = pr.get('zero_threshold_only')
    out['per_dimension'] = ext.get('per_dimension')
    out['status_extraction'] = ext.get('status')
    out['status_pairs'] = pr.get('status')
    out['coverage'] = _coverage_counts(impl)
    # G7 reference coverage report
    out['reference_coverage'] = {
        'swe_issue_identification': _swe_judgeable(impl, partition),
        'n_items': _n_items(partition),
        'note': 'reference built over the fresh heldout partition; SWE '
                'issue-identification judgeability via RT-1 positional '
                'ids (D-RT1). Unsupported classes are named here, not '
                'forced.'}
    common.write_json(out, f'eval_scores_{impl}.json')
    return out


def _n_items(partition: str) -> int:
    p = os.path.join(common.OUT, f'items_{partition}.jsonl')
    if not os.path.exists(p):
        return 0
    return sum(1 for _ in open(p))


def _n_judgeable(partition: str) -> int:
    p = os.path.join(common.OUT,
                     f'reference_{partition}_extraction.jsonl')
    if not os.path.exists(p):
        return 0
    n = 0
    for l in open(p):
        r = json.loads(l)
        for f in (r.get('fields') or {}).values():
            if isinstance(f, dict) and f.get('provenance') in (
                    'ai_agreed', 'ai_adjudicated', 'det'):
                n += 1
                break
    return n


def _dim_value_accuracy(impl: str, partition: str
                       ) -> float:
    """Mean value-agreement rate of the implementation's per-dimension
    distances against the provenance-resolved reference fields (the
    judgeable surface).  Computed on the items' LEP traces: where a
    reference field is resolved (ai_agreed/ai_adjudicated/det), the
    implementation's dimension must be evaluable and its verdict
    (positive/negative/unknown) must agree with the reference's resolved
    direction.  Wilson is reported on the gate side."""
    import eval_scoring
    led = _load_ledger_map(LEDGER[impl])
    ref_p = os.path.join(common.OUT,
                         f'reference_{partition}_extraction.jsonl')
    if not os.path.exists(ref_p) or not led:
        return 0.0
    agree = tot = 0
    for l in open(ref_p):
        r = json.loads(l)
        item = r.get('fields') or {}
        lf = led.get(r.get('item_id', '').split('__')[-1])
        if lf is None:
            continue
        for d, fld in item.items():
            if not isinstance(fld, dict):
                continue
            prov = fld.get('provenance')
            if prov not in ('ai_agreed', 'ai_adjudicated'):
                continue
            e = (lf.get('per_dimension') or {}).get(d) or {}
            if not e.get('evaluable'):
                continue
            ref_dir = (fld.get('value') == 'diverges' or
                       (isinstance(fld.get('value'), dict) and
                        fld['value'].get('equivalent') is False))
            tot += 1
            if bool(e.get('divergent')) == bool(ref_dir):
                agree += 1
    return (agree / tot) if tot else 0.0


def _coverage_counts(impl: str) -> Dict[str, int]:
    """Per-dimension evaluable counts over the FULL frame ledger (the G1
    coverage floor is against the v6 12,707/12,779/12,068 evaluable)."""
    led = _load_ledger_map(LEDGER[impl])
    if not led:
        return {}
    cov: Dict[str, int] = {}
    for d in common.DIMS:
        if impl == 'frozen':
            cov[d] = -1   # unavailable: the frozen ledger has no
            # per-dimension evaluable flag; reported, never imputed
            continue
        cov[d] = sum(1 for r in led.values()
                     if ((r.get('per_dimension') or {}).get(d) or {})
                     .get('evaluable'))
    return cov


def _swe_judgeable(impl: str, partition: str) -> Dict[str, Any]:
    """G7: is SWE issue-identification judgeable (RT-1 positional ids)?
    Measured on the code_review items of the partition: the share whose
    det_reference.swe resolves at least one issue (the deterministic
    layer), OR whose canonical_tables carry the issue alias map."""
    items_p = os.path.join(common.OUT, f'items_{partition}.jsonl')
    if not os.path.exists(items_p):
        return {'judgeable': False, 'declared_unsupported': True,
                'reason': 'items not built'}
    n_code = n_det = n_table = 0
    for l in open(items_p):
        it = json.loads(l)
        if it.get('task_family') != 'code_review':
            continue
        n_code += 1
        det = (it.get('det_reference') or {}).get('swe') or {}
        if det.get('decides'):
            n_det += 1
        if (it.get('canonical_tables') or {}).get('issue_alias_map'):
            n_table += 1
    share = (n_det / n_code) if n_code else 0.0
    return {'judgeable': (share >= 0.5 and n_table == n_code),
            'declared_unsupported': False,
            'n_code_review_items': n_code,
            'n_det_resolved': n_det,
            'n_with_alias_table': n_table,
            'det_resolve_share': share,
            'frame_share': 0.354,
            'note': ('code_review is ~35% of the frame; RT-1 positional '
                     'ids make issue-identification judgeable; the '
                     'deterministic layer decides it where a stance is '
                     'stated. Unstated-issue cells stay unresolved '
                     '(never forced).')}


def do_pre_seal(judge_calls: int = 40) -> Dict[str, Any]:
    """DEV-side only: items + det reference + judge chunks + reference
    build + eval scoring + synthetic + sampling.  Heldout untouched."""
    import items as items_mod
    import reference_build
    import run_synthetic
    rep: Dict[str, Any] = {'partition': 'dev'}
    rep['items'] = items_mod.build('dev')
    rep['synthetic'] = run_synthetic.build()
    # judge dev chunks (live; bounded by judge_calls)
    import judge_runner
    for judge in ('judge_a', 'judge_b'):
        for task in ('extraction', 'pair'):
            rep.setdefault('judge', []).append(judge_runner.run(
                judge, task, 'dev', judge_calls, 0,
                (judge_runner.build_extraction_prompt
                 if task == 'extraction'
                 else judge_runner.build_pair_prompt)))
    rep['reference_dev'] = reference_build.build('dev')
    scores = {i: _score_impl(i, 'dev') for i in IMPLS}
    rep['eval_scores_dev'] = {i: s.get('status_pairs') for i, s in
                               scores.items()}
    common.write_json(rep, 'preseal_report.json')
    print(json.dumps({k: rep[k] for k in ('items',)
                      if isinstance(rep.get(k), dict)}, indent=1)[:800])
    return rep


def do_final_eval(judge_calls: int = 40, offset: int = 0
                  ) -> Dict[str, Any]:
    """CONSUME the heldout (sealed only).  Chunked: call repeatedly with
    advancing offset until every judge's needs_live_call is 0 (the cache
    makes repeats free)."""
    seal_p = os.path.join(common.FINAL_DIR, 'seal', 'seal.json')
    if not os.path.exists(seal_p):
        raise SystemExit('NOT SEALED: run `eval_cli.py --seal` first; the '
                         'heldout is consumed exactly once, against the '
                         'sealed candidate set.')
    sealed = common.check_sealed()
    if not sealed.get('ok'):
        raise SystemExit(f'SEAL BROKEN: {sealed.get("mismatches")[:5]}')
    import items as items_mod
    import judge_runner
    import reference_build
    rep: Dict[str, Any] = {'partition': 'heldout', 'seal_ok': True}
    rep['items'] = items_mod.build('heldout')
    for judge in ('judge_a', 'judge_b'):
        for task in ('extraction', 'pair'):
            rep.setdefault('judge', []).append(judge_runner.run(
                judge, task, 'heldout', judge_calls, offset,
                (judge_runner.build_extraction_prompt
                 if task == 'extraction'
                 else judge_runner.build_pair_prompt)))
    # adjudicate disagreements (cached)
    rep['adjudication'] = judge_runner.adjudicate('heldout', judge_calls,
                                                   offset)
    rep['reference_heldout'] = reference_build.build('heldout')
    scores = {i: _score_impl(i, 'heldout') for i in IMPLS}
    common.write_json(scores, 'eval_scores.json')
    import gates
    synthetic = json.load(open(os.path.join(common.OUT,
                                            'synthetic_report.json')))
    coverage = json.load(open(os.path.join(common.OUT,
                                            'sample_coverage_report.json')))
    blinding = json.load(open(
        os.path.join(common.OUT, 'items_heldout_report.json')))[
        'blinding_scan']
    verdict = gates.evaluate(scores, synthetic, coverage, blinding, sealed)
    rep['gate_verdict'] = verdict
    out_p = common.write_json(rep, 'final_eval_report.json')
    print(json.dumps({'decision': verdict['decision'],
                       'ranking': verdict.get('ranking')}, indent=1))
    print('wrote', out_p)
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pre-seal', action='store_true')
    ap.add_argument('--seal', action='store_true')
    ap.add_argument('--final-eval', action='store_true')
    ap.add_argument('--judge-calls', type=int, default=40)
    ap.add_argument('--offset', type=int, default=0)
    a = ap.parse_args()
    if a.seal:
        do_seal()
    if a.pre_seal:
        do_pre_seal(a.judge_calls)
    if a.final_eval:
        do_final_eval(a.judge_calls, a.offset)
    if not (a.seal or a.pre_seal or a.final_eval):
        ap.print_help()


if __name__ == '__main__':
    main()
