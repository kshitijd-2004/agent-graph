#!/usr/bin/env python3.11
"""
Build the complete AI-adjudicated REFERENCE label sets (requirements 9/11/12).

EXTRACTION reference (300 sampled units) — per unit, per ITEM (claim verdict
/ issue identification / final answer), the reference is exactly one of:

  * provenance 'ai_agreed'        — both judges valid and the item-level
                                    state+value agree;
  * provenance 'ai_adjudicated'   — a disagreement resolved by the third-model
                                    adjudicator (its resolution entry);
  * provenance 'ai_unresolved'    — genuinely ambiguous (the adjudicator
                                    refused to force a verdict) OR a
                                    judge/adjudicator output was unusable.
                                    State 'unavailable': EXCLUDED from
                                    precision/recall, never a negative.
  * provenance 'judge_invalid'    — both judges' item records unusable and
                                    no adjudication covered it.  Same
                                    exclusion as 'ai_unresolved'.

NO label is invented anywhere: every reference value traces to a judge
annotation (with its evidence span) or to the adjudicator's resolution.
Agreement between two LLM judges is NOT ground truth; the reference is
labelled 'AI-adjudicated' throughout and must never be reported as
human-validated.

PAIR (divergence) reference (150 matched groups) — per group, per dimension,
the reference is whether the TARGET member observably differs on that
dimension (the pipeline's own construct): 'positive' when both judges (or
the adjudicator, for disagreements) say 'yes'; 'negative' when both say
'no'; None (excluded) for ambiguous/unavailable/unresolved.  Groups whose
reference set the judges judged UNUSABLE are excluded entirely — matching
the validation framework's "unstable control makes the comparison unusable,
not negative" rule.  The pipeline side keeps its full multi-reference
semantics (mean over references, theta = max benign-to-benign), so the
reference is compared against the pipeline indicator on the target, not
against a substituted single reference.

PARTITIONS: every row carries its dev / heldout partition (fixture-level,
from out/partitions.json) so development and held-out evaluation stages run
against disjoint rows of these files.

Read-only with respect to all judge outputs and validation artifacts.
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
V7 = os.path.abspath(os.path.join(HERE, '..'))
REPO = os.path.abspath(os.path.join(V7, '..', '..'))
sys.path.insert(0, V7)
import base                                          # noqa: E402

OUT = os.path.join(V7, 'out')
VALID = os.path.join(REPO, 'analysis', 'measurement_validation')
SPECROOT = os.path.join(REPO, 'analysis', 'bnd_v41_cw_swe200_provisional',
                        'specroot')

DIMS = ('assertions', 'decisions', 'actions', 'outcomes')

# label-block keys that are annotation METADATA, not judged fields
_META_KEYS = {'annotator_confidence', 'notes', 'confidence', 'evidence',
              'item_token', 'pair_token', 'explanations',
              'random_in_group_by', 'field_key'}


def _load_jsonl(p: str) -> List[Dict[str, Any]]:
    return [json.loads(l) for l in open(p) if l.strip()]


def _load_json_arr_or_jsonl(p: str) -> List[Dict[str, Any]]:
    """run_agreement wrote .jsonl-named files as JSON arrays; accept both."""
    try:
        return json.load(open(p))
    except json.JSONDecodeError:
        return _load_jsonl(p)


def _state_of(ent: Any) -> str:
    """Canonical state of a label record (or None -> unavailable)."""
    if isinstance(ent, dict):
        s = str(ent.get('state') or '').strip().lower()
        if s in ('observed', 'absent', 'ambiguous', 'unavailable'):
            return s
        if ent.get('value') is not None and s in ('', 'none'):
            return 'observed'
        return s or 'unavailable'
    if ent is None:
        return 'unavailable'
    return str(ent).strip().lower() or 'unavailable'


def _item_keys_for(fam: str, spec: Dict[str, Any]) -> List[str]:
    """Reference items for a family: the claims / issues the labels are
    scored on.  Read from the SPEC (task-side ids only, no GT values)."""
    if fam == 'research_synthesis':
        facts = (spec or {}).get('required_facts') or {}
        return [k for k in facts if 'claim' in k.lower()] or sorted(facts)
    if fam == 'code_review':
        req = (spec or {}).get('required_issues') or []
        if isinstance(req, dict):
            return [str(k) for k in req]
        return [str(x.get('id') or x.get('key') or i)
                for i, x in enumerate(req)]
    if fam == 'financial_analysis':
        return ['final_answer']
    return []


def _canon_verdict(v: Any) -> Optional[str]:
    s = str(v or '').strip().lower().replace('_', ' ')
    if 'support' in s:
        return 'SUPPORTED'
    if 'contradict' in s or 'refut' in s or 'disprov' in s or \
            'falsif' in s:
        return 'CONTRADICTED'
    if any(k in s for k in ('not enough', 'nei', 'insufficient',
                            'inconclusive', 'undetermined', 'unclear')):
        return 'NOT_ENOUGH'
    return None


def _parse_num(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r'[-+]?\d[\d,]*(?:\.\d+)?', str(v))
    return float(m.group(0).replace(',', '')) if m else None


def _scale_words(s: str) -> float:
    m = re.search(r'(?i)\b(thousand|million|billion|trillion)s?\b', s)
    if not m:
        return 1.0
    return {'thousand': 1e3, 'million': 1e6, 'billion': 1e9,
            'trillion': 1e12}[m.group(1).lower()]


def _canon_fin_value(v: Any) -> Optional[float]:
    """Reference final answer -> float with scale words applied, matching
    how the v6/v7 extractors canonicalise ('13,532 million' -> 1.3532e10).
    Percent-marked values are kept as-is (18.84%, not 0.1884) — the
    extractors record percent answers as 18.84 too."""
    if v is None:
        return None
    n = _parse_num(v)
    if n is None:
        return None
    return n * _scale_words(str(v))


def _issue_id_from_ordinal(oid: str, spec_ids: List[str]) -> Optional[str]:
    """'issue_3' / 'ISSUE_3' -> the 3rd required issue's spec id; a literal
    spec id passes through."""
    m = re.match(r'(?i)^issue[_\- ]?(\d+)$', str(oid))
    if m:
        i = int(m.group(1)) - 1
        if 0 <= i < len(spec_ids):
            return spec_ids[i]
    return oid if oid in set(spec_ids) else None


def _normalize_extr_labels(labels: Dict[str, Any], fam: str,
                           item_keys: List[str]
                           ) -> Dict[str, Dict[str, Any]]:
    """Flatten any legitimate judge layout to {item_key: {state, value,
    canonical}}.  Information-preserving only: it rebinds existing
    records, never invents values.
      * field-key record whose value is a per-item dict (claim_verdicts ->
        {claim_1_verdict: SUPPORTED});
      * top-level per-item records named claim_N_verdict / <issue_id>;
      * ordinal issue_N keys, mapped positionally to the spec ids."""
    out: Dict[str, Dict[str, Any]] = {}

    def canon_of(val: Any) -> Any:
        if fam == 'research_synthesis':
            return _canon_verdict(val)
        if fam == 'financial_analysis':
            return _canon_fin_value(val)
        # code: map the stance surface to the issue-identification flags
        iv = val
        idf = rpf = None
        if isinstance(iv, dict):
            idf = bool(iv.get('identified'))
            rpf = bool(iv.get('repair_only') or
                       iv.get('proposed_or_performed') or
                       iv.get('repair_performed') or
                       iv.get('repair'))
        elif isinstance(iv, str):
            ls = iv.lower().strip()
            if 'identified' in ls and 'not' not in ls:
                idf = True
                rpf = bool(('repair' in ls or 'fix' in ls)
                           and 'no' not in ls.split('repair')[0])
            elif 'not_identified' in ls or 'denied' in ls:
                idf = False
            elif ls in ('no', 'none', 'n/a'):
                idf = False
            elif 'asserted' in ls:
                idf = True
            elif 'repair_proposed' in ls:
                idf = True
                rpf = True
            elif 'repair_performed' in ls:
                idf = True
                rpf = True
        elif isinstance(iv, bool):
            idf = iv
        return {'identified': idf, 'repair': rpf}

    def put(item: str, st: str, val: Any, precedence: int) -> None:
        """Record (state, value) for an item.  ``st`` is ALWAYS a parent
        availability state (observed/absent/ambiguous/unavailable) — never a
        leaf value like 'identified' (that was the original collision bug).
        Higher precedence wins: full record > leaf-of-expanded-dict > skip."""
        rec = {'state': st, 'value': val,
               'canonical': canon_of(val) if st in ('observed', 'absent')
               else None,
               '_prec': precedence}
        cur = out.get(item)
        if cur is None or rec['_prec'] > cur.get('_prec', -1):
            out[item] = rec

    for k, ent in (labels or {}).items():
        if k in _META_KEYS or not isinstance(ent, dict):
            continue
        st = _state_of(ent)
        val = ent.get('value')
        # case 1: a record directly named after the item itself
        if k in item_keys:
            put(k, st, val, 2)
        # case 2: a field key whose value is a per-item dict
        if isinstance(val, dict):
            for ik, iv in val.items():
                oo = _issue_id_from_ordinal(ik, item_keys) \
                    if fam == 'code_review' else \
                    (ik if ik in item_keys else None)
                if oo is None:
                    continue
                if isinstance(iv, dict) and 'state' in iv and \
                        'value' in iv:
                    # a full nested record: keep it intact
                    put(oo, _state_of(iv), iv.get('value'), 2)
                elif isinstance(iv, dict) and 'state' in iv:
                    put(oo, _state_of(iv), iv.get('value'), 1)
                else:
                    # a bare leaf: inherit the parent field's state
                    put(oo, st, iv, 1)
        # case 3 (code only): a top-level ordinal key NOT named after an
        # item itself (e.g. labels['issue_1'] = {state, value} when the
        # spec id is the semantic 'fast_delete_retains_primary_key')
        if fam == 'code_review' and k not in item_keys:
            oo = _issue_id_from_ordinal(k, item_keys)
            if oo is not None:
                put(oo, st, val, 1)

    for ik in item_keys:
        out.setdefault(ik, {'state': 'unavailable', 'value': None,
                            'canonical': None, '_prec': 0})
    return {k: {kk: vv for kk, vv in v.items() if kk != '_prec'}
            for k, v in out.items()}


# ── extraction reference ───────────────────────────────────────────────────

def build_extraction_reference() -> Dict[str, Any]:
    sample = _load_jsonl(os.path.join(VALID, 'sample_manifest.jsonl'))
    unit_by_token: Dict[str, str] = {}
    idmap = json.load(open(os.path.join(
        VALID, '.cache', 'blind_id_map.json')))
    for u, t in idmap['maps']['A'].items():
        unit_by_token[t] = u
    parts = json.load(open(os.path.join(OUT, 'partitions.json')))
    fx_part = {fx: r['partition'] for fx, r in parts['fixtures'].items()}
    unit_by_id = {u['unit_id']: u for u in sample}
    specs = base.load_specs()

    pk = os.path.join(OUT, 'judge_packets',
                      'extraction_packet_judge.jsonl')
    packets = {json.loads(l)['item_token']: json.loads(l)
               for l in open(pk) if l.strip()}
    ja = {r['item_token']: r for r in
          _load_jsonl(os.path.join(OUT,
                                   'judge_a_extraction_judgments.jsonl'))}
    jb = {r['item_token']: r for r in
          _load_jsonl(os.path.join(OUT,
                                   'judge_b_extraction_judgments.jsonl'))}
    adj = [r for r in _load_jsonl(
        os.path.join(OUT, 'judge_adjudicated_ai.jsonl'))
        if r.get('kind') == 'extraction']
    adj_by_tok = {r['item_token']: r for r in adj}

    ex_rows = _load_json_arr_or_jsonl(
        os.path.join(OUT, 'judge_agreement_extr_rows.jsonl'))
    agreed_by_tok = {r['item_token']: r for r in ex_rows}

    out_rows: List[Dict[str, Any]] = []
    prov_counter: Dict[str, int] = {'ai_agreed': 0, 'ai_adjudicated': 0,
                                     'ai_unresolved': 0,
                                     'judge_invalid': 0}
    for tok, pkt in packets.items():
        unit = unit_by_id.get(unit_by_token.get(tok))
        if unit is None:
            continue
        fam = unit['task_family']
        spec = specs.get(unit['fixture_id']) or {}
        items = _item_keys_for(fam, spec)
        if not items:
            continue
        def _extr_labels(rec: Dict[str, Any]) -> Dict[str, Any]:
            r = rec or {}
            la = r.get('labels')
            if isinstance(la, dict) and la:
                return la
            p = r.get('parsed')
            if isinstance(p, dict):
                pl = p.get('labels')
                if isinstance(pl, dict):
                    return pl
            return {}
        la = _extr_labels(ja.get(tok))
        lb = _extr_labels(jb.get(tok))
        A = _normalize_extr_labels(la, fam, items)
        B = _normalize_extr_labels(lb, fam, items)
        a_valid = bool((ja.get(tok) or {}).get('valid'))
        b_valid = bool((jb.get(tok) or {}).get('valid'))
        adjrow = adj_by_tok.get(tok)
        resolution: Dict[str, Any] = {}
        unresolved_f: set = set()
        if adjrow and adjrow.get('adjudicated'):
            resolution = adjrow.get('resolution') or {}
            # resolution keys may be field-key or item-key; normalise
            norm_res = _normalize_extr_labels(
                {k: v for k, v in resolution.items()}, fam, items)
            resolution = norm_res
            unresolved_f = set(adjrow.get('unresolved_fields') or [])
        unit_ref: Dict[str, Dict[str, Any]] = {}
        for ik in items:
            a, b = A.get(ik), B.get(ik)
            if a['state'] in ('observed', 'absent') and \
                    a['state'] == b['state'] and a_valid and b_valid and \
                    (a['canonical'] is None or
                     b['canonical'] is None or
                     a['canonical'] == b['canonical']):
                rec = dict(a)
                rec['provenance'] = 'ai_agreed'
                rec['agreement'] = 'value' if a['state'] == 'observed' \
                    else 'state'
            elif ik in resolution and resolution.get(ik, {}).get(
                    'state', 'unavailable') != 'unavailable':
                rec = dict(resolution.get(ik))
                rec['provenance'] = 'ai_adjudicated'
                rec['adjudicator_note'] = adjrow.get('note')
            elif ik in unresolved_f:
                rec = {'state': 'unavailable', 'value': None,
                       'canonical': None, 'provenance': 'ai_unresolved',
                       'note': 'adjudicator: genuinely ambiguous'}
            elif not a_valid and not b_valid:
                rec = {'state': 'unavailable', 'value': None,
                       'canonical': None, 'provenance': 'judge_invalid',
                       'note': 'both judge records unusable; no '
                               'adjudication covered this item'}
            else:
                # disagreement with no usable adjudication (JSON failure)
                rec = {'state': 'unavailable', 'value': None,
                       'canonical': None, 'provenance': 'ai_unresolved',
                       'note': 'disagreement left without a usable '
                               'adjudicated verdict'}
            if rec.get('provenance'):
                prov_counter[rec['provenance']] += 1
            unit_ref[ik] = rec
        out_rows.append({
            'unit_id': unit['unit_id'],
            'trace_id': unit['trace_id'],
            'fixture_id': unit['fixture_id'],
            'task_family': fam,
            'kind': unit.get('kind'),
            'component': unit.get('component'),
            'partition': fx_part.get(unit['fixture_id'], 'dev'),
            'item_token': tok,
            'design_weight': unit.get('design_weight'),
            'inclusion_probability': unit.get('inclusion_probability'),
            'population_estimable': unit.get('population_estimable'),
            'items': unit_ref,
            'n_items': len(unit_ref),
            'n_resolved': sum(1 for r in unit_ref.values()
                              if r['provenance'] in ('ai_agreed',
                                                     'ai_adjudicated')),
            'n_unresolved': sum(1 for r in unit_ref.values()
                                if r['provenance'] in ('ai_unresolved',
                                                       'judge_invalid')),
            'a_valid': a_valid, 'b_valid': b_valid,
        })
    return {'rows': out_rows, 'item_provenance': prov_counter}


# ── pair (divergence) reference ────────────────────────────────────────────

def build_pair_reference() -> Dict[str, Any]:
    pk = os.path.join(OUT, 'judge_packets', 'pair_packet_judge.jsonl')
    packets = {json.loads(l)['pair_token']: json.loads(l)
               for l in open(pk) if l.strip()}
    roles = json.load(open(os.path.join(
        VALID, '.cache', 'blind_pair_roles.json')))
    role_of = {}
    for grp in roles['roles'].values():
        for tok, rec in grp.items():
            role_of[tok] = rec
    pairs = {p['pair_id']: p for p in
             _load_jsonl(os.path.join(VALID, 'pair_manifest.jsonl'))}
    sample = {u['unit_id']: u for u in
              _load_jsonl(os.path.join(VALID, 'sample_manifest.jsonl'))}
    parts = json.load(open(os.path.join(OUT, 'partitions.json')))
    fx_part = {fx: r['partition'] for fx, r in parts['fixtures'].items()}

    ja = {r['item_token']: r for r in
          _load_jsonl(os.path.join(OUT, 'judge_a_pair_judgments.jsonl'))}
    jb = {r['item_token']: r for r in
          _load_jsonl(os.path.join(OUT, 'judge_b_pair_judgments.jsonl'))}
    adj = [r for r in _load_jsonl(
        os.path.join(OUT, 'judge_adjudicated_ai.jsonl'))
        if r.get('kind') == 'pair']
    adj_by_tok = {r['item_token']: r for r in adj}

    def _member_diffs(rec: Dict[str, Any]) -> Dict[int, Dict[str, str]]:
        p = rec.get('pair_record') or {}
        if not isinstance(p, dict):
            p = {}
        mj = p.get('member_judgments')
        if not isinstance(mj, list):
            mj = p.get('members') or []
        out: Dict[int, Dict[str, str]] = {}
        for i, m in enumerate(mj or []):
            if not isinstance(m, dict):
                continue
            ix = m.get('member_index', i)
            out[int(ix)] = {d: str(m.get(f'{d}_differs') or
                                   m.get(d) or '').lower()
                            for d in DIMS}
        return out

    out_rows: List[Dict[str, Any]] = []
    for tok, pkt in packets.items():
        members = pkt.get('members') or []
        pair_id = role_of.get(members[0].get('item_token', ''),
                               {}).get('pair_id')
        pair = pairs.get(pair_id) or {}
        fixture = pair.get('fixture_id') or (pair.get('task_family') or '')
        # target member index
        tidx = None
        for m in members:
            if role_of.get(m.get('item_token'), {}).get('role') == 'target':
                tidx = m.get('member_index')
                break
        if tidx is None:
            continue
        da = _member_diffs(ja.get(tok) or {})
        db = _member_diffs(jb.get(tok) or {})
        fa = da.get(tidx, {})
        fb = db.get(tidx, {})
        adjrow = adj_by_tok.get(tok) or {}
        resolution = adjrow.get('resolution') or {}
        unresolved_f = set(adjrow.get('unresolved_fields') or [])

        def _res_key(d: str) -> List[str]:
            return [d, f'{d}.{tidx}', f'{d}_{tidx}',
                    f'{d}/{tidx}', f'member_{tidx}_{d}']

        dims: Dict[str, Dict[str, Any]] = {}
        for d in DIMS:
            va, vb = fa.get(d), fb.get(d)
            if va in ('yes', 'no') and va == vb:
                ref = 'positive' if va == 'yes' else 'negative'
                prov = 'ai_agreed'
            else:
                got = None
                for kk in _res_key(d):
                    rv = resolution.get(kk)
                    if isinstance(rv, dict):
                        rv = rv.get('state') or rv.get('value')
                    if rv in ('yes', 'positive', 'true', True, 'observed'):
                        got = 'positive'
                    elif rv in ('no', 'negative', 'false', False,
                                'absent'):
                        got = 'negative'
                    break
                if got is not None and adjrow.get('adjudicated'):
                    ref, prov = got, 'ai_adjudicated'
                elif d in unresolved_f or va in ('ambiguous', 'unavailable') \
                        or vb in ('ambiguous', 'unavailable'):
                    ref, prov = None, 'ai_unresolved'
                else:
                    ref, prov = None, 'ai_unresolved'
            dims[d] = {'ref': ref, 'provenance': prov,
                       'judge_a': va, 'judge_b': vb}
        def _pair_payload(rec: Dict[str, Any]) -> Dict[str, Any]:
            for k in ('pair_record', 'parsed'):
                p = (rec or {}).get(k)
                if isinstance(p, dict):
                    return p
            return {}
        refset_a = str(_pair_payload(ja.get(tok)).get(
            'reference_set_usable') or '').lower()
        refset_b = str(_pair_payload(jb.get(tok)).get(
            'reference_set_usable') or '').lower()
        if 'no' in (refset_a.lower(), refset_b.lower()):
            usable = False
        else:
            usable = None
        pos = [d for d in DIMS if dims[d]['ref'] == 'positive']
        neg = [d for d in DIMS if dims[d]['ref'] == 'negative']
        union_ref = 'positive' if pos else (
            'negative' if (neg and not any(
                dims[d]['ref'] is None for d in DIMS)) else None)
        out_rows.append({
            'pair_id': pair_id, 'pair_token': tok,
            'lep_trace_id': pair.get('lep_trace_id') or
            pair.get('trace_id'),
            'task_family': pair.get('task_family') or fixture,
            'fixture_id': fixture,
            'partition': fx_part.get(fixture, 'dev'),
            'target_member_index': tidx,
            'dims': dims,
            'union_ref': union_ref,
            'reference_set_usable': usable,
            'excluded': bool(usable is False),
            'n_dims_resolved': sum(1 for d in DIMS
                                    if dims[d]['ref'] is not None),
        })
    return {'rows': out_rows}


def main() -> int:
    ex = build_extraction_reference()
    pr = build_pair_reference()
    dst1 = os.path.join(OUT, 'ai_reference_extraction.jsonl')
    with open(dst1, 'w') as f:
        for r in ex['rows']:
            f.write(json.dumps(r, sort_keys=True) + '\n')
    dst2 = os.path.join(OUT, 'ai_reference_pairs.jsonl')
    with open(dst2, 'w') as f:
        for r in pr['rows']:
            f.write(json.dumps(r, sort_keys=True) + '\n')
    rep = {
        'extraction': {
            'n_units': len(ex['rows']),
            'item_provenance': ex['item_provenance'],
            'partition_split': {p: sum(1 for r in ex['rows']
                                       if r['partition'] == p)
                                for p in ('dev', 'heldout')},
        },
        'pair': {
            'n_groups': len(pr['rows']),
            'excluded_unusable_refsets': sum(
                1 for r in pr['rows'] if r['excluded']),
            'union_ref': {u: sum(1 for r in pr['rows']
                                  if r['union_ref'] == u)
                          for u in ('positive', 'negative', None)},
            'partition_split': {p: sum(1 for r in pr['rows']
                                       if r['partition'] == p)
                                for p in ('dev', 'heldout')},
        },
        'provenance_caveat': ('AI-adjudicated reference (two judges + '
                               'third-model adjudication), NOT human ground '
                               'truth.  Judge B was a distinct same-relay '
                               'model (no GPT endpoint reachable); see the '
                               'judge config substitution_note.'),
    }
    json.dump(rep, open(os.path.join(OUT, 'ai_reference_report.json'),
                        'w'), indent=2)
    print(json.dumps(rep, indent=2))
    print(f'wrote {dst1} and {dst2}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
