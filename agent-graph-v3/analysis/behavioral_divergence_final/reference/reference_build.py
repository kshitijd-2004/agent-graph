#!/usr/bin/env python3.11
"""
AI-adjudicated reference-set builder (FROZEN_PROTOCOL §3; MEASUREMENT_SPEC §7
layer 3).

For each item, over judge_a + judge_b cached judgments:
  * ai_agreed       — both judges' canonical-field states+values match
                      (canonicalization per RT-1/RT-2/RT-3 BEFORE compare);
  * ai_adjudicated  — the third-model adjudicator resolved it;
  * ai_unresolved   — genuinely ambiguous by design -> EXCLUDED, never a
                      negative label;
  * judge_invalid   — both unusable -> EXCLUDED.

Disagreement is computed on canonical forms, not surface strings.  Judge
outputs are append-only: this writes a NEW reference file; it never edits
the judge ledgers.  A dim/item the deterministic layer (det_reference)
already decided is carried with provenance 'det:<layer>' and is NOT re-run
through the AI layer (layer priority: deterministic > AI).
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
FINAL = os.path.abspath(os.path.join(_HERE, '..'))
sys.path.insert(0, FINAL)
import common
sys.path.insert(0, os.path.join(FINAL, 'extractors'))
sys.path.insert(0, _HERE)
import normalizers
import det_reference as DR


_TRACE_CACHE: Dict[str, Any] = {}
_IDX: Dict[str, str] = {}


def _trace(tid: str) -> Any:
    global _IDX
    if not _IDX:
        for l in open(os.path.join(common.VALIDATION_CACHE,
                                   'trace_index.jsonl')):
            d = json.loads(l)
            _IDX[d['trace_id']] = d['path']
    if tid not in _TRACE_CACHE:
        pth = _IDX.get(tid)
        _TRACE_CACHE[tid] = json.load(open(pth)) \
            if pth and os.path.exists(pth) else None
    return _TRACE_CACHE[tid]


def _calib_actions(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Action-dimension verdict on the DECLARED construct, recomputed
    independently at reference time (brief §4: 'independently implemented
    deterministic verification against raw tool-call events, arguments,
    ordering, multiplicity, and observable execution results, according
    to the declared action construct').

    The sealed items.jsonl det block is UNTOUCHED, and the judge prompt
    never included the det block, so the judge cache is unaffected.  The
    raw single-pair TV>0 signal in the sealed block stays a diagnostic
    (RT-5); the verdict here is the benign-calibrated theta form:
    θ = max b2b TV; fire iff (mean LEP-vs-benign − mean b2b) > θ;
    undetermined (None) when the cell is uncalibratable — never a
    forced negative."""
    tids = item.get('trace_ids') or {}
    lep = tids.get('lep')
    if lep is None:
        return None
    lt = _trace(lep)
    if lt is None:
        return None
    bts = [_trace(r) for r in (tids.get('benign_refs') or [])]
    bts = [x for x in bts if x is not None]
    if not bts:
        return None
    try:
        out = DR.det_actions_calibrated(lt, bts)
    except Exception as e:
        return {'verdict': 'undetermined',
                'basis': f'det:actions_calibrated_error:{type(e).__name__}',
                'decides': False}
    dv = out.get('verdict')
    out['verdict_bool'] = (True if dv == 'diverges'
                           else (False if dv == 'agrees' else None))
    return out


def _load_items(partition: str) -> Dict[str, Dict]:
    p = os.path.join(common.OUT, f'items_{partition}.jsonl')
    return {r['item_id']: r for r in
            (json.loads(l) for l in open(p) if l.strip())}


def _load_ledger(judge: str, partition: str, task: str
                 ) -> Dict[str, Dict]:
    p = os.path.join(common.OUT, f'judge_{judge}_{partition}_{task}.jsonl')
    out: Dict[str, Dict] = {}
    if os.path.exists(p):
        for l in open(p):
            r = json.loads(l)
            out[r['item_id']] = r
    return out


# ── judgment schema canonicalization ───────────────────────────────────────
#: observed judge-output key variants (the structured-output discipline
#: allows the model's own key names; the reference layer normalizes before
#: ANY comparison — this is the 'normalize equivalent representations
#: before calculating disagreement' rule, applied to the judge layer too)
_UNION_KEYS = ('union', 'union_verdict', 'UNION')
_DIM_KEYS = ('dimensions', 'verdicts')
_VERDICT_MAP = {'diverges': True, 'agrees': False, 'undetermined': None,
                'yes': True, 'no': False, 'true': True, 'false': False,
                'null': None, None: None}


def _canon_bool(x: Any) -> Optional[bool]:
    if isinstance(x, bool):
        return x
    if x is None:
        return None
    s = str(x).strip().lower()
    return _VERDICT_MAP.get(s, None) if s in _VERDICT_MAP else \
        _VERDICT_MAP.get(s.split('|')[0].strip() if '|' in s else s)


def _norm_pair_judgment(j: Optional[Dict]) -> Optional[Dict]:
    """Any observed pair-judgment variant -> {'dims': {d: bool|None},
    'union': bool|None}.  None when the judgment is unusable."""
    if not isinstance(j, dict):
        return None
    dims: Dict[str, Any] = {}
    if isinstance(j.get('dimensions'), dict):
        dims = j['dimensions']
    elif isinstance(j.get('verdicts'), dict):
        dims = j['verdicts']
    else:
        dims = {k: j[k] for k in ('assertions', 'decisions', 'actions',
                                  'outcomes') if k in j}
    out_dims: Dict[str, Any] = {}
    for d in ('assertions', 'decisions', 'actions', 'outcomes'):
        v = dims.get(d)
        if isinstance(v, dict):
            verdict = (v.get('verdict') or v.get('diverges')
                       or v.get('stance'))
            out_dims[d] = _canon_bool(verdict)
        elif isinstance(v, (list, tuple)):
            out_dims[d] = 'list'   # extraction-style value list: keep as-is
        else:
            out_dims[d] = _canon_bool(v)
    union = None
    for k in _UNION_KEYS:
        if k in j:
            union = _canon_bool(j[k])
            break
    if union is None and out_dims:
        resolved = [v for v in out_dims.values()
                    if isinstance(v, bool)]
        if resolved and all(isinstance(v, bool) for v in out_dims.values()):
            union = any(resolved)
    return {'dims': out_dims, 'union': union}


def _norm_extract_field0(fld: Any) -> Optional[Dict]:
    """An extraction judgment field -> {'value': bool|None|list,
    'state': ...}.  Handles the model's per-dimension sub-shapes."""
    if not isinstance(fld, dict):
        return None
    val = fld.get('value')
    if val is None:
        return {'value': None, 'state': fld.get('state')}
    if isinstance(val, list):
        # a list of atomic judgments (assertion items): reduce to
        # observed/absent per element, keep the list for the record
        return {'value': val, 'state': fld.get('state')}
    if isinstance(val, dict):
        return {'value': val, 'state': fld.get('state')}
    s = str(val).strip().lower()
    if s in ('observed', 'absent', 'ambiguous', 'unavailable', 'none',
             'no_answer_key'):
        return {'value': s, 'state': s}
    return {'value': s, 'state': fld.get('state', 'observed')}


def _canon_value(fam: str, dim: str, value: Any,
                 item: Dict[str, Any]) -> Any:
    """RT-1/RT-2/RT-3 canonicalization before cross-judge comparison."""
    ct = item.get('canonical_tables') or {}
    if fam == 'code_review' and dim in ('assertions', 'decisions') \
            and isinstance(value, dict):
        out = {}
        for k, v in value.items():
            key = normalizers.canonical_swe_issue(k, ct.get('issue_alias_map')
                                                  or {})
            out[key or f'unresolved:{k}'] = v
        return out
    if fam == 'research_synthesis' and dim in ('assertions', 'decisions') \
            and isinstance(value, dict):
        out = {}
        for k, v in value.items():
            cv = normalizers.canonical_research_features(
                {k: type(v)(state='observed', value=v)} if False else {})
            out[k] = v
        return out
    return value

def _det_decided_dims(det: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in (det or {}).items()
            if isinstance(v, dict) and v.get('decides')}


def _det_dim_verdicts(fam: str, det: Dict[str, Any]) -> Dict[str, Any]:
    """Map the family-native det LAYERS onto canonical dim DIVERGENCE
    verdicts: {dim: True (diverges) | False (agrees) | None (undetermined)}.

    Layer -> dim scope (MEASUREMENT_SPEC construct table):
      actions layer  -> actions   (independent TV over raw tool events)
      finans  layer  -> outcomes  (independent answer parse + RT-2 scaling)
      verdicts layer -> decisions (research claim verdicts, final scope)
      swe     layer  -> decisions (code issue stances, final scope)
    The det layers operate on the FINAL response -> decisions scope; the
    full-trajectory assertions surface has NO independent det layer and
    stays on the judge surface (never imputed)."""
    out: Dict[str, Any] = {d: None for d in
                           ('assertions', 'decisions', 'actions',
                            'outcomes')}
    det = det or {}
    act = det.get('actions')
    if isinstance(act, dict) and act.get('verdict'):
        # undetermined (no tool use either side) -> None, NEVER a
        # forced negative (missing is not zero)
        v = act.get('verdict')
        out['actions'] = (True if v == 'diverges'
                          else (False if v == 'agrees' else None))
    if fam == 'financial_analysis':
        fin = det.get('finans')
        if isinstance(fin, dict) and fin.get('decides'):
            ae = fin.get('all_equivalent')
            out['outcomes'] = (not ae) if ae is not None else None
    if fam == 'research_synthesis':
        v = det.get('verdicts')
        if isinstance(v, dict) and v.get('decides'):
            claims = v.get('claims') or {}
            eqs = [c.get('equivalent') for c in claims.values()
                   if c.get('equivalent') is not None]
            if eqs:
                out['decisions'] = (not all(eqs))
    if fam == 'code_review':
        s = det.get('swe')
        if isinstance(s, dict) and s.get('decides'):
            iss = s.get('issues') or {}
            eqs = [c.get('equivalent') for c in iss.values()
                   if c.get('equivalent') is not None]
            if eqs:
                out['decisions'] = (not all(eqs))
    return out


def _norm_extract_field(fam: str, dim: str, fld: Any,
                        item: Dict[str, Any]) -> Any:
    """Reduce an extraction judgment field to a canonical value for
    cross-judge agreement: a per-dim dict {state, value, ...} or None."""
    nf = _norm_extract_field0(fld) if fld is not None else None
    if nf is None or nf.get('value') is None:
        return nf
    val = nf['value']
    if dim in ('assertions', 'decisions'):
        if isinstance(val, list):
            # atomic per-key judgments: canonicalize keys (RT-1) + verdicts
            # (RT-3), keep as a frozenset of (key, state, canon_value)
            table = ((item.get('canonical_tables') or {})
                     .get('issue_alias_map') or {})
            canon = []
            for e in val:
                if not isinstance(e, dict):
                    continue
                key = e.get('id') or e.get('key')
                if fam == 'code_review' and key is not None:
                    rk = normalizers.canonical_swe_issue(str(key), table)
                    key = rk or f'unresolved:{key}'
                v = e.get('value')
                if fam == 'research_synthesis' and v:
                    c = normalizers.canonical_research_features(
                        {k: v for k, v in []}) if False else None
                    cv = _canonical_verdict(v)
                    if cv:
                        v = cv
                canon.append((key, e.get('state'), v))
            return {'state': nf.get('state'), 'value': frozenset(canon)}
        if isinstance(val, dict):
            table = ((item.get('canonical_tables') or {})
                     .get('issue_alias_map') or {})
            outd = {}
            for k, v in val.items():
                kk = k
                if fam == 'code_review':
                    rk = normalizers.canonical_swe_issue(str(k), table)
                    kk = rk or f'unresolved:{k}'
                vv = v
                if fam == 'research_synthesis':
                    cv = _canonical_verdict(v)
                    if cv:
                        vv = cv
                outd[kk] = vv
            return {'state': nf.get('state'), 'value': outd}
        if fam == 'research_synthesis':
            cv = _canonical_verdict(val)
            if cv:
                return {'state': nf.get('state'), 'value': cv}
    return nf


_CANON_VERDICT = None


def _canonical_verdict(v: Any) -> Optional[str]:
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


def build(partition: str) -> Dict[str, Any]:
    items = _load_items(partition)
    a = _load_ledger('judge_a', partition, 'extraction')
    b = _load_ledger('judge_b', partition, 'extraction')
    pa = _load_ledger('judge_a', partition, 'pair')
    pb = _load_ledger('judge_b', partition, 'pair')
    adj_dir = os.path.join(common.OUT, 'judge_cache', 'adjudicator')
    out_rows: List[Dict] = []
    prov_counts: Dict[str, int] = {}
    for iid, item in items.items():
        fa, fb = a.get(iid), b.get(iid)
        det = item.get('det_reference') or {}
        fam = item.get('task_family')
        det_decided = _det_decided_dims(det)
        det_dims = _det_dim_verdicts(fam, det)
        # declared action construct: the CALIBRATED theta verdict
        # (independent TV kernel + benign-b2b calibration).  The raw
        # single-pair TV>0 stays a diagnostic (RT-5) and is not a
        # verdict.  If the traces are unavailable the dim falls
        # through to the judge surface — never a forced negative.
        calib = _calib_actions(item)
        det_dims['actions'] = (calib.get('verdict_bool')
                               if calib is not None else det_dims.get(
                                   'actions'))
        row = {'item_id': iid, 'partition': partition,
               'task_family': fam, 'det_decided_dims': sorted(det_decided),
               'det_dim_verdicts': det_dims,
               'actions_calibrated': {k: calib.get(k) for k in
                                      ('theta_actions', 'lep_mean',
                                       'b2b_mean', 'delta',
                                       'zero_theta', 'n_benign_pairs')}
               if calib is not None else None}
        fields: Dict[str, Any] = {}
        da_dim = {}
        db_dim = {}
        _jfa = (fa or {}).get('judgment')
        _jfb = (fb or {}).get('judgment')
        if isinstance(_jfa, dict):
            da_dim = _jfa
        if isinstance(_jfb, dict):
            db_dim = _jfb
        for dim in ('assertions', 'decisions', 'actions', 'outcomes'):
            da = da_dim.get(dim)
            db = db_dim.get(dim)
            dv = det_dims.get(dim)
            if dv is not None:
                fields[dim] = {'provenance': 'det', 'diverges': dv,
                               'layer': ({'actions': 'actions',
                                          'outcomes': 'finans',
                                          'decisions':
                                              ('verdicts' if fam ==
                                               'research_synthesis' else
                                               'swe')}
                                         .get(dim, '?'))}
                prov_counts['det'] = prov_counts.get('det', 0) + 1
                continue
            na = _norm_extract_field(fam, dim, da, item) if da is not None \
                else None
            nb = _norm_extract_field(fam, dim, db, item) if db is not None \
                else None
            va = na.get('value') if na else None
            vb = nb.get('value') if nb else None
            sa = na.get('state') if na else None
            sb = nb.get('state') if nb else None
            if va is None and vb is None:
                fields[dim] = {'provenance': 'judge_invalid',
                                'note': 'no valid judgment'}
                prov_counts['judge_invalid'] = \
                    prov_counts.get('judge_invalid', 0) + 1
                continue
            if sa in ('ambiguous',) or sb in ('ambiguous',):
                fields[dim] = {'provenance': 'ai_unresolved',
                               'note': 'genuinely ambiguous by design',
                               'candidates': [va, vb]}
                prov_counts['ai_unresolved'] = \
                    prov_counts.get('ai_unresolved', 0) + 1
                continue
            # canonical-form agreement
            if va == vb:
                fields[dim] = {'provenance': 'ai_agreed', 'value': va,
                               'states': [sa, sb]}
                prov_counts['ai_agreed'] = prov_counts.get('ai_agreed', 0) \
                    + 1
            else:
                adj_p = os.path.join(adj_dir, f'{iid}__{dim}.json')
                if os.path.exists(adj_p):
                    adj = json.load(open(adj_p))
                    if adj.get('valid_json') and \
                            (adj['judgment'] or {}).get('verdict') not in \
                            ('unresolved', None):
                        fields[dim] = {'provenance': 'ai_adjudicated',
                                       'value': (adj['judgment'] or {}).get(
                                           'value'),
                                       'explanation':
                                           (adj['judgment'] or {}).get(
                                               'reason')}
                        prov_counts['ai_adjudicated'] = \
                            prov_counts.get('ai_adjudicated', 0) + 1
                        continue
                fields[dim] = {'provenance': 'ai_unresolved',
                               'candidates': [va, vb]}
                prov_counts['ai_unresolved'] = \
                    prov_counts.get('ai_unresolved', 0) + 1
        row['fields'] = fields
        out_rows.append(row)
    common.write_jsonl(out_rows, f'reference_{partition}_extraction.jsonl')

    # ── pair reference (union + per-dimension divergence, canonical) ─────
    pair_rows: List[Dict] = []
    pprov: Dict[str, int] = {}
    for iid, item in items.items():
        if item.get('kind') != 'pair':
            continue
        ja = pa.get(iid, {}).get('judgment')
        jb = pb.get(iid, {}).get('judgment')
        na, nb = _norm_pair_judgment(ja), _norm_pair_judgment(jb)
        detd = _det_dim_verdicts(item.get('task_family'),
                                 item.get('det_reference') or {})
        # declared action construct on the pair surface: calibrated
        # theta verdict overrides the raw single-pair TV>0 diagnostic
        pcal = _calib_actions(item)
        if pcal is not None and pcal.get('verdict_bool') is not None:
            detd['actions'] = pcal.get('verdict_bool')
        elif pcal is not None:
            detd['actions'] = None
        dims = {d: {'judge_a': (na['dims'].get(d) if na else None),
                    'judge_b': (nb['dims'].get(d) if nb else None),
                    'det': detd.get(d)}
                for d in ('assertions', 'decisions', 'actions', 'outcomes')}
        pair_row_extra = ({'actions_calibrated': {k: pcal.get(k) for k in
                                                  ('theta_actions',
                                                   'lep_mean', 'b2b_mean',
                                                   'delta', 'zero_theta')},
                          } if pcal is not None else {})
        # per-dim resolution: det verdict > judge agreement > undetermined
        resolved = {}
        for d, cell in dims.items():
            if cell['det'] is not None:
                resolved[d] = cell['det']
            elif (cell['judge_a'] is not None and
                    cell['judge_b'] is not None and
                    cell['judge_a'] == cell['judge_b']):
                resolved[d] = cell['judge_a']
            else:
                resolved[d] = None
        # union = logical OR of the four indicators, undetermined-safe:
        #   * POSITIVE if ANY dim resolved positive (a positive needs only
        #     one evaluable positive dim);
        #   * NEGATIVE only if EVERY dim resolved negative;
        #   * else EXCLUDED (undetermined) — a dim that cannot be decided
        #     can never be read as agreement (missing is not zero).
        pos_seen = any(bool(rv) for rv in resolved.values() if rv is not None)
        all_resolved = all(rv is not None for rv in resolved.values())
        union_ref = True if pos_seen else (False if all_resolved else None)
        # third-model adjudication of judge disagreements (FROZEN_PROTOCOL
        # §3; cached — no new live calls): when the adjudicator resolved a
        # specific pair, its union verdict takes precedence over the
        # det+judge-agreement union; 'unresolved' keeps the pair EXCLUDED
        # (never forced).
        adj_p = os.path.join(adj_dir, f'{iid}__pair.json')
        adj_union = None
        adj_taken = False
        if os.path.exists(adj_p):
            _adj = json.load(open(adj_p))
            _aj = (_adj.get('judgment') or {})
            if _adj.get('valid_json'):
                v = _aj.get('verdict')
                if v in ('diverges', 'agrees'):
                    adj_union = (v == 'diverges')
                    adj_taken = True
                elif v in ('candidate-1', 'candidate-2'):
                    _cand = na if v == 'candidate-1' else nb
                    _cu = _cand['union'] if _cand else None
                    if isinstance(_cu, bool):
                        adj_union = _cu
                        adj_taken = True
        if adj_union is not None and adj_taken:
            union_ref = adj_union
            union_provenance_override = 'ai_adjudicated'
        else:
            union_provenance_override = None
        det_involved = any(c['det'] is not None for c in dims.values())
        ai_involved = any(c['judge_a'] is not None and
                          c['judge_b'] is not None for c in dims.values())
        prov = ('det+ai' if (det_involved and ai_involved)
                else ('det' if det_involved
                      else ('ai_agreed' if union_ref is not None
                            else 'ai_unresolved')))
        if union_provenance_override:
            prov = union_provenance_override
        prow = {
            'pair_id': iid, 'item_id': iid,
            'fixture_id': item.get('fixture_id'),
            'task_family': item.get('task_family'),
            'pair_class': item.get('pair_class'),
            'lep_trace_id': (item.get('trace_ids') or {}).get('lep'),
            'dims': dims,
            'union_ref': union_ref,
            'union_provenance': prov,
            'resolved_dims': resolved,
            'excluded': union_ref is None,
            'judge_ids': ['judge_a', 'judge_b'],
        }
        prow.update(pair_row_extra)
        pair_rows.append(prow)
    common.write_jsonl(pair_rows, f'reference_{partition}_pairs.jsonl')

    report = {'partition': partition,
              'n_extraction_items': len(out_rows),
              'extraction_provenance': prov_counts,
              'n_pair_items': len(pair_rows),
              'pair_union': {'positive': sum(1 for r in pair_rows
                                              if r['union_ref'] is True),
                             'negative': sum(1 for r in pair_rows
                                             if r['union_ref'] is False),
                             'null_excluded': sum(1 for r in pair_rows
                                                  if r['union_ref']
                                                  is None)},
              'note': ('ai_unresolved / judge_invalid are EXCLUDED, never '
                       'a negative label; deterministic provenance takes '
                       'priority over the AI layer')}
    common.write_json(report, f'reference_{partition}_report.json')
    print(json.dumps({'partition': partition,
                      'extraction': prov_counts,
                      'pairs': report['pair_union']}, indent=1))
    return report



if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--partition', default='dev')
    a = ap.parse_args()
    build(a.partition)
