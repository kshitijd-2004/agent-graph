#!/usr/bin/env python3.11
"""
Reference-based evaluation driver (requirements 11/12/13).

SCORES the frozen / v6 / v7 extractors and their divergence indicators
against the AI-adjudicated reference sets built by build_ai_reference.py:

  1. EXTRACTION performance — run frozen, v6 and v7 extractors on the 300
     sample units (under the verified strip_gt_values feed), score per item
     against the reference items.  Metrics per family: coverage,
     observed-call precision/recall/F1, state agreement, value agreement
     (canonical verdicts / scale-normalised numerics / issue flags), error
     taxonomy, Wilson intervals; the representative (A) component is
     additionally scored with design weights + fixture-cluster bootstrap.
  2. DIVERGENCE performance — per-dimension and union indicators of
     frozen / v6 / v7 (from the frame ledger and the v7 pipeline) scored
     against the pair reference; unavailable references excluded, never
     counted as negatives.
  3. ZERO-THRESHOLD sensitivity — for each implementation, how many union
     positives depend EXCLUSIVELY on dimensions with theta_d == 0.
  4. M4-matched propagation — the five-way classification on the sampled
     LEP units (M4-matched primary; M4 historical retained; persistent
     benign failures NEVER read as perturbation-caused degradation).

PARTITIONS: dev and heldout are evaluated as disjoint stages (requirement
8/16).  Dev runs first; held-out results are the final, untuned
comparison.  No threshold is tuned against held-out data.

All outputs written under analysis/measurement_v7/out/.  The reference is
AI-adjudicated, not human: every report carries that label.
"""
from __future__ import annotations

import json
import math
import os
import random
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
V7 = os.path.abspath(os.path.join(HERE, '..'))
REPO = os.path.abspath(os.path.join(V7, '..', '..'))
sys.path.insert(0, V7)
import base                                          # noqa: E402
import build_ai_reference as BR                      # noqa: E402

OUT = os.path.join(V7, 'out')
VALID = os.path.join(REPO, 'analysis', 'measurement_validation')
BOOT_SEED = 20261129

# ── intervals ───────────────────────────────────────────────────────────────


def wilson(k: int, n: int, z: float = 1.959963984540054) -> Optional[Dict]:
    if n <= 0:
        return None
    p = k / n
    den = 1.0 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / den
    return {'p': p, 'lo': min(max(0.0, c - h), p),
            'hi': max(min(1.0, c + h), p), 'n': n, 'k': k,
            'method': 'wilson'}


def cluster_bootstrap(rows: List[Dict[str, Any]],
                      cluster_key: str = 'fixture_id',
                      n_rep: int = 2000
                      ) -> Dict[str, Any]:
    """Percentile fixture-cluster bootstrap of the DESIGN-WEIGHTED value
    accuracy, MT-driven (fixed seed) so the interval is byte-reproducible.

    Each row carries ``_den`` (judgeable), ``_num`` (correct), and
    ``design_weight``; the statistic is the weighted numerator / weighted
    denominator.  Replicates resample FIXTURES (the cluster), which is the
    correct interval for fixture-clustered samples; a replicate with no
    judgeable items contributes no draw."""
    def stat(sample: List[Dict[str, Any]]) -> Optional[float]:
        num = den = 0.0
        for r in sample:
            if not r.get('_den'):
                continue
            w = float(r.get('design_weight') or 1.0)
            den += w
            if r.get('_num'):
                num += w
        return (num / den) if den else None

    groups: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[r.get(cluster_key)].append(r)
    keys = sorted(groups, key=str)
    point = stat(rows)
    if not keys:
        return {'estimate': None,
                'method': 'design_weighted + fixture_cluster_bootstrap'}
    rnd = random.Random(BOOT_SEED)
    draws = []
    for _ in range(n_rep):
        pick = [keys[int(rnd.random() * len(keys))] for _ in keys]
        sample = [r for k in pick for r in groups[k]]
        v = stat(sample)
        if v is not None:
            draws.append(v)
    out: Dict[str, Any] = {'estimate': point,
                           'method': 'design_weighted + '
                                     'fixture_cluster_bootstrap'}
    if draws:
        draws.sort()
        out['lo'] = draws[int(0.025 * len(draws))]
        out['hi'] = draws[min(len(draws) - 1, int(0.975 * len(draws)))]
        out['n_valid_replicates'] = len(draws)
    return out


# ── extractor runs on the sample ────────────────────────────────────────────


def _load_traces(need: set) -> Dict[str, Dict[str, Any]]:
    idx = {json.loads(l)['trace_id']: json.loads(l)['path']
           for l in open(os.path.join(base.VALIDATION_CACHE,
                                      'trace_index.jsonl'))}
    out: Dict[str, Dict[str, Any]] = {}
    for tid in need:
        p = idx.get(tid)
        if p and os.path.exists(p):
            out[tid] = json.load(open(p))
    return out


def run_extractors_on_sample(ref_rows: List[Dict[str, Any]]
                             ) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Run frozen / v6 / v7 extractors on every sample unit's trace under
    the verified containment feed.  Returns
    {impl: {unit_id: {'states': {item: state}, 'values': {item: value},
    'canon': {item: canonical}}}}."""
    P = base.import_pipelines()
    FROZEN, V6F, V6CORE = P['FROZEN'], P['V6F'], P['V6CORE']
    # the v7 extractors dir FIRST, as run_pipeline does (the validation
    # scripts dir ships a flat extractors.py that would otherwise shadow it)
    sys.path.insert(0, os.path.join(V7, 'extractors'))
    import measure as V7M                             # noqa: E402
    specs = base.load_specs()
    traces = _load_traces({r['trace_id'] for r in ref_rows})
    out: Dict[str, Dict[str, Dict[str, Any]]] = \
        {'frozen': {}, 'v6': {}, 'v7': {}}
    for r in ref_rows:
        tr = traces.get(r['trace_id'])
        if tr is None:
            continue
        fam = r['task_family']
        full_spec = specs.get(r['fixture_id']) or {}
        clean = base.strip_gt_values(full_spec)
        items = r['items']
        # FROZEN
        fz = FROZEN.extract_features_v4(tr, clean)
        fstates: Dict[str, Any] = {}
        fvals: Dict[str, Any] = {}
        _frozen_items(fstates, fvals, fam, r, fz)
        fdec_s: Dict[str, Any] = {}
        fdec_v: Dict[str, Any] = {}
        _frozen_decisions(fdec_s, fdec_v, fam, r, fz)
        out['frozen'][r['unit_id']] = {'states': fstates, 'values': fvals,
                                       'decision_states': fdec_s,
                                       'decision_values': fdec_v}
        # V6
        f6 = V6F.extract_features_v6(tr, clean, fam)
        v6s: Dict[str, Any] = {}
        v6v: Dict[str, Any] = {}
        _v6_items(v6s, v6v, fam, r, f6, full_spec)
        v6d_s: Dict[str, Any] = {}
        v6d_v: Dict[str, Any] = {}
        _v6_decisions(v6d_s, v6d_v, fam, r, f6)
        out['v6'][r['unit_id']] = {'states': v6s, 'values': v6v,
                                   'decision_states': v6d_s,
                                   'decision_values': v6d_v}
        # V7 — the shared entrypoint dispatches to the family extractors
        f7 = V7M.extract_features_v7(tr, clean, fam)
        v7s: Dict[str, Any] = {}
        v7v: Dict[str, Any] = {}
        _v7_items(v7s, v7v, fam, r, f7, full_spec)
        v7d_s: Dict[str, Any] = {}
        v7d_v: Dict[str, Any] = {}
        _v7_decisions(v7d_s, v7d_v, fam, r, f7)
        out['v7'][r['unit_id']] = {'states': v7s, 'values': v7v,
                                    'decision_states': v7d_s,
                                    'decision_values': v7d_v}
    return out


def _feat(fv: Any) -> Tuple[str, Any]:
    st = getattr(fv, 'state', None)
    if st is None:
        return 'unavailable', None
    return st, getattr(fv, 'value', None)


def _frozen_items(states: Dict, vals: Dict, fam: str,
                  r: Dict, fz) -> None:
    """Frozen extractor item-level states.  Frozen verdicts/stances live in
    the feature map keyed by the same item ids; the final answer is a
    dedicated field.  Frozen financial facts are in the fact map too."""
    amap = {}
    try:
        amap = dict(fz.assertion_map(r['task_family']) or {})
    except Exception:
        pass
    all_feats = {}
    for k, fv in amap.items():
        st, v = _feat(fv)
        all_feats[k] = (st, v)
    if fam == 'financial_analysis':
        # frozen final answer (v4 extractor shape)
        st, v = _feat(getattr(fz, 'final_answer', None))
        states['final_answer'] = st
        vals['final_answer'] = v
    elif fam == 'research_synthesis':
        for ik in r['items']:
            st, v = all_feats.get(ik, ('unavailable', None))
            states[ik] = st
            vals[ik] = v
    else:
        for ik in r['items']:
            st, v = all_feats.get(ik, ('unavailable', None))
            states[ik] = st
            vals[ik] = v


def _v6_items(states: Dict, vals: Dict, fam: str, r: Dict,
              f6, spec: Dict) -> None:
    amap = dict(f6.assertions or {})
    if fam == 'financial_analysis':
        st, v = _feat(f6.answer) if f6.answer else ('unavailable', None)
        states['final_answer'] = st
        vals['final_answer'] = v
    else:
        for ik in r['items']:
            fv = amap.get(ik)
            st, v = _feat(fv) if fv is not None else ('unavailable', None)
            states[ik] = st
            vals[ik] = v


def _v7_items(states: Dict, vals: Dict, fam: str, r: Dict,
              f7, spec: Dict) -> None:
    amap = dict(getattr(f7, 'assertions', None) or {})
    if fam == 'financial_analysis':
        st, v = _feat(f7.answer) if getattr(f7, 'answer', None) else \
            ('unavailable', None)
        states['final_answer'] = st
        vals['final_answer'] = v
    else:
        for ik in r['items']:
            fv = amap.get(ik)
            st, v = _feat(fv) if fv is not None else ('unavailable', None)
            states[ik] = st
            vals[ik] = v


def _frozen_decisions(states: Dict, vals: Dict, fam: str, r: Dict,
                      fz) -> None:
    """Frozen DECISION-dimension scope (final-response-only semantics).
    Frozen keeps final-scope verdicts/stances/answers in ``decision_map`` —
    that is the decisions dimension, distinct from the whole-trace
    assertions."""
    dmap = {}
    try:
        dmap = dict(fz.decision_map(fam) or {})
    except Exception:
        dmap = {}
    for ik in r['items']:
        st, v = _feat(dmap.get(ik)) if ik in dmap else ('unavailable', None)
        states[ik] = st
        vals[ik] = v
    if fam == 'financial_analysis':
        st, v = _feat(getattr(fz, 'final_answer', None))
        states.setdefault('final_answer', st)
        vals.setdefault('final_answer', v)


def _v6_decisions(states: Dict, vals: Dict, fam: str, r: Dict,
                  f6) -> None:
    dmap = dict(getattr(f6, 'decisions', None) or {})
    for ik in r['items']:
        fv = dmap.get(ik)
        st, v = _feat(fv) if fv is not None else ('unavailable', None)
        states[ik] = st
        vals[ik] = v
    if fam == 'financial_analysis':
        st, v = _feat(getattr(f6, 'answer', None)) \
            if getattr(f6, 'answer', None) else ('unavailable', None)
        states.setdefault('final_answer', st)
        vals.setdefault('final_answer', v)


def _v7_decisions(states: Dict, vals: Dict, fam: str, r: Dict,
                  f7) -> None:
    dmap = dict(getattr(f7, 'decisions', None) or {})
    for ik in r['items']:
        fv = dmap.get(ik)
        st, v = _feat(fv) if fv is not None else ('unavailable', None)
        states[ik] = st
        vals[ik] = v
    if fam == 'financial_analysis':
        st, v = _feat(getattr(f7, 'answer', None)) \
            if getattr(f7, 'answer', None) else ('unavailable', None)
        states.setdefault('final_answer', st)
        vals.setdefault('final_answer', v)


# ── extraction scoring ──────────────────────────────────────────────────────


def _value_matches(fam: str, ref_canon: Any, pred_val: Any,
                   pred_state: str, ref_state: str) -> Optional[bool]:
    """True/False when both sides carry a value; None when not comparable."""
    if pred_state not in ('observed', 'absent'):
        return None
    if fam == 'research_synthesis':
        import build_ai_reference as _b
        pc = _b._canon_verdict(pred_val)
        if pc is None and pred_state == 'observed':
            # the extractor emitted a verdict surface the normaliser does not
            # map (e.g. NOT_ENOUGH spelled out); compare by raw equality as
            # a fallback
            return str(pred_val).strip().upper().replace('_', ' ') == \
                str(ref_canon).strip().upper().replace('_', ' ')
        return (pc == ref_canon) if (pc is not None or
                                     ref_canon is not None) else None
    if fam == 'financial_analysis':
        pc = _canon_fin(pred_val)
        if pc is None:
            return None
        if ref_canon is None:
            return pred_state == 'absent'
        return abs(pc - ref_canon) <= max(0.02 * max(1.0, abs(ref_canon)),
                                          1e-9)
    # code: reference is {'identified','repair'}
    if ref_canon is None:
        return False
    pc = pred_val
    idf, rpf = None, None
    if isinstance(pc, dict):
        idf = pc.get('identified')
        rpf = bool(pc.get('repair') or pc.get('repair_proposed') or
                   pc.get('repair_performed'))
    elif isinstance(pc, str):
        ls = pc.lower()
        if 'asserted' in ls or 'identified' in ls:
            idf = True
        elif 'denied' in ls:
            idf = False
        if 'repair_proposed' in ls or 'repair_performed' in ls or \
                'proposed' in ls:
            rpf = True
    got = {'identified': bool(idf) if idf is not None else False,
           'repair': bool(rpf)}
    return got['identified'] == bool(ref_canon.get('identified')) and \
        (ref_canon.get('repair') in (None, False) or
         got['repair'] == bool(ref_canon.get('repair')))


def _canon_fin(v: Any) -> Optional[float]:
    import build_ai_reference as _b
    return _b._canon_fin_value(v)


def score_extraction(ref_rows: List[Dict[str, Any]],
                     preds: Dict[str, Dict[str, Any]]
                     ) -> Dict[str, Any]:
    """Per-family, per-implementation extraction metrics vs the reference.
    Unavailable/unresolved references are EXCLUDED (never negatives); the
    excluded count is reported, which is the coverage/ambiguity accounting
    the brief requires."""
    out: Dict[str, Any] = {}
    for impl, by_unit in preds.items():
        per_fam: Dict[str, Dict[str, Any]] = {}
        for fam in ('research_synthesis', 'code_review',
                    'financial_analysis'):
            rows = [r for r in ref_rows if r['task_family'] == fam]
            # assertion (whole-trace) scope — the primary extraction metric
            entry = _scope_metrics(rows, by_unit, 'states', 'values')
            # decision (final-response-only) scope — the decisions dimension
            entry['decision_scope'] = _scope_metrics(rows, by_unit,
                                                     'decision_states',
                                                     'decision_values')
            per_fam[fam] = entry
        out[impl] = per_fam
    return out


def _scope_metrics(rows: List[Dict[str, Any]],
                  by_unit: Dict[str, Any], state_key: str,
                  value_key: str) -> Dict[str, Any]:
    """Score one extractor SCOPE (assertions or decision) against the
    reference.  Unavailable/unresolved references are excluded, never
    negatives; the design-weighted component-A accuracy carries a
    fixture-cluster bootstrap interval."""
    fam = rows[0]['task_family'] if rows else ''
    n_items = n_judgeable = 0
    n_ref_obs = n_ref_abs = 0
    correct_state = correct_val = 0
    pred_cov = 0
    err: Counter = Counter()
    agree_state_all = agree_state_judged = 0
    a_rows: List[Dict[str, Any]] = []
    for r in rows:
        ref_items = r['items']
        pred = by_unit.get(r['unit_id']) or {}
        ps = pred.get(state_key) or {}
        pv = pred.get(value_key) or {}
        for ik, ref in ref_items.items():
            n_items += 1
            rstate = ref.get('state')
            pstate = ps.get(ik, 'unavailable')
            if rstate in ('observed', 'absent'):
                n_judgeable += 1
                rcanon = ref.get('canonical')
                if pstate in ('observed', 'absent'):
                    agree_state_judged += 1
                    if pstate == rstate:
                        correct_state += 1
                    if rstate == 'observed':
                        v_ok = _value_matches(fam, rcanon, pv.get(ik),
                                              pstate, rstate)
                        if v_ok is True:
                            correct_val += 1
                        else:
                            err['value_mismatch' if v_ok is False
                                else 'value_uncomparable'] += 1
                    else:
                        if pstate == 'absent':
                            correct_val += 1
                else:
                    err['pred_unavailable_vs_ref_' + rstate] += 1
            elif rstate == 'ambiguous':
                err['ref_ambiguous'] += 1
            else:
                err['ref_unresolved_excluded'] += 1
            if pstate in ('observed', 'absent'):
                pred_cov += 1
            else:
                err['pred_unavailable'] += 1
            if pstate == rstate:
                agree_state_all += 1
            n_ref_obs += (rstate == 'observed')
            n_ref_abs += (rstate == 'absent')
            if r.get('component') == 'A_representative':
                row = dict(r)
                row['_den'] = rstate in ('observed', 'absent') and \
                    pstate in ('observed', 'absent')
                row['_num'] = (rstate == 'observed' and
                               _value_matches(fam, ref.get('canonical'),
                                              pv.get(ik), pstate, rstate)
                               is True) or \
                    (rstate == 'absent' and pstate == 'absent')
                a_rows.append(row)
    tp = correct_val
    n_den = n_ref_obs + n_ref_abs
    prec = tp / n_den if n_den else None
    rec = (tp / n_ref_obs) if n_ref_obs else None
    f1 = (2 * prec * rec / (prec + rec)) if (prec is not None and
                                             rec is not None and
                                             (prec + rec)) else None
    entry: Dict[str, Any] = {
        'n_units': len(rows),
        'n_items': n_items,
        'n_judgeable_items': n_judgeable,
        'n_ref_observed': n_ref_obs, 'n_ref_absent': n_ref_abs,
        'n_excluded_unresolved': n_items - n_judgeable,
        'state_coverage_pred': pred_cov / n_items if n_items else None,
        'state_agreement_overall':
            agree_state_all / n_items if n_items else None,
        'state_agreement_judged':
            correct_state / n_judgeable if n_judgeable else None,
        'precision_value': prec,
        'recall_value_over_observed': rec,
        'f1_value': f1,
        'value_ci': wilson(tp, n_den),
        'error_taxonomy': dict(err),
        'note': 'unavailable/unresolved references excluded from '
                'precision/recall, never counted as negatives; '
                'AI-adjudicated reference, not human ground truth',
    }
    if a_rows:
        # design-weighted component-A value accuracy, point estimate
        # + fixture-cluster bootstrap interval
        entry['component_A_weighted_value_accuracy'] = \
            cluster_bootstrap(a_rows)
    return entry


# ── divergence scoring ──────────────────────────────────────────────────────


def score_divergence(partition: Optional[str]) -> Dict[str, Any]:
    """Per-dimension + union indicator performance of frozen / v6 / v7
    against the pair reference, partition-scoped."""
    ref_rows = [r for r in _load_jsonl(
        os.path.join(OUT, 'ai_reference_pairs.jsonl'))
        if r['partition'] == partition or partition is None]
    frame = {json.loads(l)['trace_id']: json.loads(l)
             for l in open(os.path.join(base.VALIDATION_CACHE,
                                        'frame_units.jsonl'))}
    v7full = {json.loads(l)['trace_id']: json.loads(l)
              for l in open(os.path.join(OUT, 'per_trace_v7.jsonl'))}
    out: Dict[str, Any] = {}
    for impl in ('frozen', 'v6', 'v7'):
        per_dim: Dict[str, Dict[str, Any]] = {}
        for d in ('assertions', 'decisions', 'actions', 'outcomes'):
            tp = fp = fn = tn = n_un = 0
            for r in ref_rows:
                if r['excluded']:
                    continue
                ref = r['dims'].get(d, {}).get('ref')
                pred = _pipeline_dim(impl, r['lep_trace_id'], frame,
                                     v7full, d)
                if ref is None or pred is None:
                    n_un += 1
                    continue
                if ref == 'positive' and pred:
                    tp += 1
                elif ref == 'positive' and not pred:
                    fn += 1
                elif ref == 'negative' and pred:
                    fp += 1
                else:
                    tn += 1
            den = tp + fp + fn + tn
            per_dim[d] = {
                'n_evaluable': den, 'n_excluded_or_unavailable': n_un,
                'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn,
                'precision': tp / max(1, tp + fp) if (tp + fp) else None,
                'recall': tp / max(1, tp + fn) if (tp + fn) else None,
                'f1': (_f1(tp / max(1, tp + fp) if tp + fp else None,
                           tp / max(1, tp + fn) if tp + fn else None)),
                'fpr': fp / max(1, fp + tn) if (fp + tn) else None,
                'fnr': fn / max(1, fn + tp) if (fn + tp) else None,
            }
        # union
        tp = fp = fn = tn = n_un = 0
        zero_only = 0
        pos_total = 0
        for r in ref_rows:
            if r['excluded']:
                continue
            ref = r.get('union_ref')
            pred, zo = _pipeline_union(impl, r['lep_trace_id'], frame,
                                       v7full)
            if ref is None or pred is None:
                n_un += 1
                continue
            if ref == 'positive':
                pos_total += 1
                if zo:
                    zero_only += 1
            if ref == 'positive' and pred:
                tp += 1
            elif ref == 'positive' and not pred:
                fn += 1
            elif ref == 'negative' and pred:
                fp += 1
            else:
                tn += 1
        den = tp + fp + fn + tn
        out[impl] = {
            'partition': partition,
            'per_dimension': per_dim,
            'union': {
                'n_evaluable': den, 'n_excluded_or_unavailable': n_un,
                'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn,
                'precision': tp / max(1, tp + fp) if (tp + fp) else None,
                'recall': tp / max(1, tp + fn) if (tp + fn) else None,
                'f1': _f1(tp / max(1, tp + fp) if tp + fp else None,
                          tp / max(1, tp + fn) if tp + fn else None),
                'n_ref_positive': pos_total,
                'positives_relying_exclusively_on_zero_threshold': zero_only,
                'zero_only_fraction_of_positives':
                    (zero_only / pos_total) if pos_total else None,
                'note': 'positives flagged where EVERY triggering '
                        'dimension has theta_d == 0; theta_d=0 is NOT '
                        'changed, only quantified',
            },
        }
    return out


def _f1(p: Optional[float], r: Optional[float]) -> Optional[float]:
    if p is None or r is None or not (p + r):
        return None
    return 2 * p * r / (p + r)


def _pipeline_dim(impl: str, tid: str,
                  frame: Dict[str, Any], v7full: Dict[str, Any],
                  d: str) -> Optional[bool]:
    if impl == 'v7':
        r = v7full.get(tid)
        if not r:
            return None
        return bool((r.get('per_dimension') or {}).get(d, {}).get(
            'divergent'))
    fr = frame.get(tid)
    if not fr:
        return None
    if impl == 'frozen':
        return bool((fr.get('frozen_per_dimension') or {}).get(d, {})
                    .get('positive'))
    return bool((fr.get('v6_per_dimension') or {}).get(d, {}).get(
        'divergent'))


def _pipeline_union(impl: str, tid: str,
                    frame: Dict[str, Any], v7full: Dict[str, Any]
                    ) -> Tuple[Optional[bool], bool]:
    if impl == 'v7':
        r = v7full.get(tid)
        if not r:
            return None, False
        u = r.get('union') or {}
        trig = u.get('triggered_dimensions') or []
        zt = u.get('zero_threshold_triggered') or []
        zo = bool(trig) and set(zt) == set(trig)
        return bool(u.get('divergent')), zo
    fr = frame.get(tid)
    if not fr:
        return None, False
    if impl == 'frozen':
        pos = bool(fr.get('m1_frozen'))
        pd = fr.get('frozen_per_dimension') or {}
        trig = [d for d in base.DIMS if (pd.get(d) or {}).get('positive')]
        zo = bool(trig) and all(
            (pd.get(d) or {}).get('theta') == 0.0 for d in trig)
        return pos, zo
    pos = bool(fr.get('m1_v6'))
    u = fr.get('v6_union') or {}
    trig = u.get('triggered_dimensions') or []
    zt = u.get('zero_threshold_triggered') or []
    zo = bool(trig) and set(zt) == set(trig)
    return pos, zo


# ── M4-matched on the sampled LEP units ────────────────────────────────────


def m4_matched_report(partition: Optional[str] = None) -> Dict[str, Any]:
    """M4 taxonomy on the sampled LEP units (from the v7 pipeline),
    partition-scoped.  M4-matched = M3-supported AND LEP grade failure AND
    >=1 compatible benign reference graded correct.  Causality caveat
    carried through."""
    rows = [json.loads(l) for l in
            open(os.path.join(OUT, 'per_trace_v7.jsonl')) if l.strip()]
    parts = json.load(open(os.path.join(OUT, 'partitions.json')))
    fx_part = {fx: r['partition'] for fx, r in parts['fixtures'].items()}
    if partition:
        rows = [r for r in rows
                if fx_part.get(r['trace_id'].split('.')[0]) == partition]
    M4 = base.import_pipelines()['M4']
    sel = [{'class': r.get('m4_class'), 'lep_grade': r.get('lep_grade'),
            'm3_reach_class': r.get('m3_reach_class')} for r in rows]
    rep = M4.summarise(sel)
    rep['partition'] = partition
    rep['caveat'] = ('M4-matched is perturbation-ASSOCIATED degradation, '
                     'not causation: benign references are separate '
                     'executions, not counterfactuals.  Persistent benign '
                     'failures are NOT perturbation-caused degradation and '
                     'are reported separately.')
    return rep


def _load_jsonl(p: str) -> List[Dict[str, Any]]:
    return [json.loads(l) for l in open(p) if l.strip()]


# ── driver ─────────────────────────────────────────────────────────────────


def run_all() -> None:
    ref_rows = BR.build_extraction_reference()['rows']
    # run extractors once (300 units), score per partition
    preds = run_extractors_on_sample(ref_rows)
    for part in ('dev', 'heldout'):
        prows = [r for r in ref_rows if r['partition'] == part]
        s = score_extraction(prows, preds)
        dst = os.path.join(OUT,
                           f'extraction_vs_reference_{part}.json')
        json.dump({'partition': part,
                   'reference': 'ai_adjudicated (NOT human ground truth)',
                   'implementations': s},
                  open(dst, 'w'), indent=2)
        print(f'wrote {dst}')
    for part in ('dev', 'heldout'):
        d = score_divergence(part)
        dst = os.path.join(OUT, f'divergence_vs_reference_{part}.json')
        json.dump({'partition': part,
                   'reference': 'ai_adjudicated pair reference '
                                '(NOT human ground truth)',
                   'implementations': d},
                  open(dst, 'w'), indent=2)
        print(f'wrote {dst}')
    for part in ('dev', 'heldout', None):
        dst = os.path.join(OUT,
                           f'm4_matched_{part or "full"}.json')
        json.dump(m4_matched_report(part), open(dst, 'w'), indent=2)
        print(f'wrote {dst}')


if __name__ == '__main__':
    run_all()
