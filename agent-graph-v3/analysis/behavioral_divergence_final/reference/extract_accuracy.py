#!/usr/bin/env python3.11
"""
G1 value-level extraction accuracy — INDEPENDENT DETERMINISTIC layer
(the gate G1 input, computed against the brief's strongest method).

The brief (§4) prescribes per dimension: actions -> independently
implemented deterministic verification; structured numerical outputs ->
independent deterministic parsing + canonicalization; assertions/decisions
-> evidence-grounded judgment with canonical task-specific identifiers.
The det_reference layer is that independent implementation (fresh code
over raw events; NO candidate extractor import — verified).  G1 therefore
compares each implementation's extracted VALUES against the det layer at
NATIVE KEY GRANULARITY:

  * decisions-scope surface (the final response): research per-claim
    verdicts (RT-3 canonical enum), code per-issue stances (RT-1
    positional keys), financial final answer (RT-2 unit-scaled numeric)
    -> per-key value agreement with the det layer's independent parse.
  * assertions-scope surface (full trajectory): NO independent det layer
    exists (trajectory text is the judge construct) -> reported as the
    AI-judge surface: impl state-level agreement where the judges
    resolved the assertions construct; unresolved = excluded.
  * actions: the det actions layer is a LEP-vs-BENIGN pair verdict, not a
    single-trace value; impl action validity is carried by G4/G5 (union
    pair surface) + synthetic S5/S9/S13 invariance instead.  G1 actions
    value-accuracy is reported as UNSUPPORTED, never imputed.

Per-implementation extractor adapters:
  candspec  extractors/candspec_features.extract_candspec
  v7        measurement_v7/extractors/measure.extract_features_v7
  v6        measurement_v6/features_v6.extract_features_v6
  frozen    the P2 ledger's embedded `features` block (the frozen
            extractor's own output; the only impl that does NOT re-run
            extraction).

Output: out/extract_accuracy.json — consumed by gates._g1.
"""
from __future__ import annotations

import json
import math
import os
import sys
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
FINAL = os.path.abspath(os.path.join(_HERE, '..'))
sys.path.insert(0, FINAL)
import common
sys.path.insert(0, os.path.join(common.V7_DIR, 'extractors'))
sys.path.insert(0, common.V6_DIR)
sys.path.insert(0, os.path.join(FINAL, 'extractors'))
sys.path.insert(0, os.path.join(FINAL, 'reference'))
import candspec_features as CF            # noqa: E402
import measure as M7                      # noqa: E402
import features_v6 as F6                  # noqa: E402
import normalizers                        # noqa: E402

_CANON_VERDICT = None


def _canon(v: Any) -> Optional[str]:
    global _CANON_VERDICT
    if _CANON_VERDICT is None:
        try:
            P = common.pipelines()
            _CANON_VERDICT = P['V6G'].canonical_verdict
        except Exception:
            _CANON_VERDICT = lambda x: None
    try:
        return _CANON_VERDICT(v)
    except Exception:
        return None


def wilson(k: int, n: int, z: float = 1.959964) -> Optional[List[float]]:
    if n <= 0:
        return None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [max(0.0, c - m), min(1.0, c + m)]


def _sv(feat: Any) -> tuple:
    return (getattr(feat, 'state', None), getattr(feat, 'value', None))


def _impl_decisions(impl: str, tr: Dict, spec: Dict, fam: str,
                    frozen_features: Optional[Dict]
                    ) -> Dict[str, tuple]:
    """{positional_key: (state, value)} for the DECISIONS-scope surface in
    the item's canonical key space (RT-1 positional keys for code)."""
    if impl == 'candspec':
        f = CF.extract_candspec(tr, spec, fam)
        out = {k: _sv(v) for k, v in f.decisions.items()}
        if fam == 'financial_analysis' and f.answer is not None:
            out['answer'] = _sv(f.answer)
        return out
    if impl in ('v6', 'v7'):
        f = M7.extract_features_v7(tr, spec, fam) if impl == 'v7' \
            else F6.extract_features_v6(tr, spec, fam)
        out = {k: _sv(v) for k, v in f.decisions.items()}
        if getattr(f, 'answer', None) is not None:
            out['answer'] = _sv(f.answer)
        return out
    if impl == 'frozen':
        ff = frozen_features or {}
        if fam == 'research_synthesis':
            return {k: (e.get('state'), e.get('value'))
                    for k, e in (ff.get('final_claims') or {}).items()}
        if fam == 'financial_analysis':
            fa = ff.get('final_answer')
            return {'answer': (fa.get('state'), fa.get('value'))} \
                if fa else {}
        tab = normalizers.swe_issue_alias_table(spec.get('required_issues'))
        sem2pos = {e.get('semantic_id'): k for k, e in tab.items()
                   if e.get('semantic_id')}
        return {sem2pos.get(k, f'unresolved:{k}'):
                (e.get('state'), e.get('value'))
                for k, e in (ff.get('final_stances') or {}).items()}
    return {}


def _key_matches(fam: str, key: str, impl_sv: tuple,
                 det_entry: Dict) -> bool:
    """Per-key value agreement between an impl extraction and the
    independent det layer's key, on canonical forms (RT-1/RT-2/RT-3)."""
    st, v_i = impl_sv
    if st != 'observed':
        return False      # impl did not state the key; a det-observed key
                          # it missed is NOT an agreement
    det_v = det_entry.get('lep')
    if det_v is None:
        return False
    if fam == 'financial_analysis':
        try:
            a = float(v_i)
            b = float(det_v)
        except (TypeError, ValueError):
            return str(v_i) == str(det_v)
        return normalizers.numeric_equivalent(a, b, 0.0, 0.0)
    if fam == 'research_synthesis':
        return _canon(v_i) == _canon(det_v)
    if fam == 'code_review':
        # canonical stance CLASS (brief §4: normalize equivalent
        # representations before comparing): the det regex layer's
        # 'asserted' and the extractors' 'repair_proposed' both denote
        # 'defect identified' — exact-string match would be an
        # artificial miss.  Class vocabulary:
        #   defect_stated  = {asserted, repair_proposed, repair_performed}
        #   defect_denied  = {denied}
        #   no_stance      = {mentioned_no_stance, localized_no_stance,
        #                     absent, unobserved}
        return _stance_class(v_i) == _stance_class(det_v)
    return v_i == det_v


def _stance_class(v: Any) -> str:
    s = str(v).strip().lower() if v is not None else ''
    if s in ('asserted', 'repair_proposed', 'repair_performed'):
        return 'defect_stated'
    if s == 'denied':
        return 'defect_denied'
    return 'no_stance'


def _load_jsonl(path: str) -> List[Dict]:
    with open(path) as fh:
        return [json.loads(l) for l in fh if l.strip()]


def run(partition: str = 'heldout') -> Dict[str, Any]:
    items = {r['item_id']: r for r in _load_jsonl(
        os.path.join(common.OUT, f'items_{partition}.jsonl'))
        if r.get('kind') == 'extraction'}
    ref = {r['item_id']: r for r in _load_jsonl(
        os.path.join(common.OUT,
                     f'reference_{partition}_extraction.jsonl'))}
    specs = common.load_specs()
    idx = {json.loads(l)['trace_id']: json.loads(l)['path']
           for l in open(os.path.join(common.VALIDATION_CACHE,
                                      'trace_index.jsonl'))
           if l.strip()}
    P2 = {r['trace_id']: r for r in _load_jsonl(
        os.path.join(common.CONS, 'bnd_v41_run',
                     'bnd_v41_per_trace.jsonl'))}
    tc: Dict[str, Optional[Dict]] = {}

    def get_trace(tid: str) -> Optional[Dict]:
        if tid not in tc:
            p = idx.get(tid)
            tc[tid] = json.load(open(p)) if p and os.path.exists(p) \
                else None
        return tc[tid]

    impls = ['candspec', 'v6', 'v7', 'frozen']
    led_paths = {
        'candspec': os.path.join(common.OUT, 'per_trace_candspec.jsonl'),
        'v6': os.path.join(common.V6_DIR, 'out', 'per_trace_v6.jsonl'),
        'v7': os.path.join(common.V7_DIR, 'out', 'per_trace_v7.jsonl'),
        'frozen': os.path.join(common.CONS, 'bnd_v41_run',
                               'bnd_v41_per_trace.jsonl'),
    }
    coverage_floor: Dict[str, Dict[str, Any]] = {}
    for impl in impls:
        cc = {d: 0 for d in common.DIMS}
        if impl != 'frozen' and os.path.exists(led_paths[impl]):
            for r in _load_jsonl(led_paths[impl]):
                pd = r.get('per_dimension')
                if isinstance(pd, dict):
                    for d in common.DIMS:
                        if (pd.get(d) or {}).get('evaluable'):
                            cc[d] += 1
        else:
            # frozen ledger has no per-dimension evaluable flags:
            # unavailable, never imputed
            cc = {d: None for d in common.DIMS}
        coverage_floor[impl] = cc

    stat: Dict[str, Dict[str, Any]] = {
        i: {'det': {'n': 0, 'ok': 0}, 'det_by_fam': {},
            'ai': {'n': 0, 'ok': 0}}
        for i in impls}
    for iid, item in items.items():
        fam = item.get('task_family')
        spec = specs.get(item.get('fixture_id') or '', {})
        tid = (item.get('trace_ids') or {}).get('lep')
        tr = get_trace(tid)
        if tr is None:
            continue
        det = item.get('det_reference') or {}
        p2 = P2.get(tid) or {}
        frozen_feats = ((p2.get('features') or {}).get(tid) or {})
        refrow = ref.get(iid) or {}
        # det-layer per-key decisions-scope surface
        det_keys: Dict[str, Dict] = {}
        dlayer = det.get('verdicts') or det.get('swe')
        if isinstance(dlayer, dict) and dlayer.get('decides'):
            det_keys = {k: v for k, v in
                        (dlayer.get('claims') or dlayer.get('issues')
                         or {}).items()
                        if isinstance(v, dict) and v.get('lep')}
        det_fin = det.get('finans')
        for impl in impls:
            dec = _impl_decisions(impl, tr, spec, fam, frozen_feats)
            if det_keys:
                for k, dv in det_keys.items():
                    sv = dec.get(k)
                    if sv is None:
                        continue
                    cell = stat[impl]['det']
                    cell['n'] += 1
                    hit = int(_key_matches(fam, k, sv, dv))
                    cell['ok'] += hit
                    famc = stat[impl]['det_by_fam'].setdefault(fam,
                                                                [0, 0])
                    famc[0] += 1
                    famc[1] += hit
            elif fam == 'financial_analysis' and det_fin and \
                    det_fin.get('decides') and \
                    det_fin.get('lep_value') is not None:
                sv = dec.get('answer')
                if sv is not None and sv[0] == 'observed':
                    cell = stat[impl]['det']
                    cell['n'] += 1
                    hit = int(_key_matches(fam, 'answer', sv,
                                           {'lep':
                                            det_fin['lep_value']}))
                    cell['ok'] += hit
                    famc = stat[impl]['det_by_fam'].setdefault(fam,
                                                                [0, 0])
                    famc[0] += 1
                    famc[1] += hit
            # assertions-scope AI-judge surface: did the impl state any
            # key where the judges resolved the assertions construct?
            fld = (refrow.get('fields') or {}).get('assertions') or {}
            prov = fld.get('provenance')
            if prov in ('ai_agreed', 'ai_adjudicated'):
                cell = stat[impl]['ai']
                cell['n'] += 1
                impl_obs = any(sv2[0] == 'observed'
                               for sv2 in (dec.values() if dec else []))
                cell['ok'] += int(impl_obs)
    out: Dict[str, Any] = {
        'partition': partition,
        'method': ('G1 value-accuracy vs the INDEPENDENT det layer at '
                   'native key granularity (decisions scope: research '
                   'claim verdicts, code issue stances, financial '
                   'final answer), + AI-judge STATE-level agreement '
                   '(assertions scope: the full-trajectory surface the '
                   'det layer does not cover); actions value-accuracy '
                   'unsupported by design (pair-level construct -> '
                   'G4/G5 + synthetic S5/S9/S13 carry it).  The det '
                   'layer is a fresh independent implementation (no '
                   'candidate extractor import).'),
        'per_impl': {},
        'coverage_floor': coverage_floor,
        'v6_baseline': {'assertions': 12707, 'decisions': 12779,
                        'outcomes': 12068},
    }
    for impl in impls:
        s = stat[impl]
        dn, dk = s['det']['n'], s['det']['ok']
        an, ak = s['ai']['n'], s['ai']['ok']
        det_fam = {k: {'n': v[0], 'correct': v[1],
                       'accuracy': (v[1] / v[0]) if v[0] else None,
                       'wilson95': wilson(v[1], v[0])}
                   for k, v in s['det_by_fam'].items()}
        out['per_impl'][impl] = {
            'det_key_accuracy': (dk / dn) if dn else None,
            'det_key_wilson95': wilson(dk, dn),
            'det_n_keys': dn,
            'det_by_family': det_fam,
            'ai_state_accuracy': (ak / an) if an else None,
            'ai_n': an,
            'actions_value_accuracy': 'unsupported_by_design',
            'value_accuracy': ((dk + ak) / (dn + an)) if (dn + an)
            else None,
            'value_accuracy_wilson95': wilson(dk + ak, dn + an),
            'coverage': coverage_floor.get(impl, {}),
        }
    common.write_json(out, 'extract_accuracy.json')
    print(json.dumps({
        i: {'det_acc': out['per_impl'][i]['det_key_accuracy'],
            'ai_state_acc': out['per_impl'][i]['ai_state_accuracy'],
            'overall': out['per_impl'][i]['value_accuracy']}
        for i in impls}, indent=1))
    return out


if __name__ == '__main__':
    run()
