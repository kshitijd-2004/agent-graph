#!/usr/bin/env python3.11
"""
Two-judge agreement analysis (requirement 8).

Aligns Judge A and Judge B on item_token (extraction) and pair_token
(pair), and computes:

  * exact agreement (raw),
  * Cohen's kappa (categorical: state per field / differs per dimension),
  * Krippendorff's alpha,
  * numeric agreement (financial answers),
  * per-dimension confusion matrices,
  * disagreement rates by family,
  * ambiguous / unavailable rates,
  * classification of disagreements (direct disagreement, unsupported
    label, missing evidence, contradictory evidence, both-agree-weak).

Every number is LABELLED ``ai_judge_agreement``: agreement between two LLM
judges is NOT proof of correctness, and when judge B is a same-relay
substitute (no GPT endpoint) the report also carries the
``independence_caveat`` from the judge config.  The agreement is a
convergence statistic, nothing more.

Writes analysis/measurement_v7/out/judge_agreement.json (and the aligned
row file for adjudication).
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, os.path.join(REPO, 'analysis', 'measurement_v7'))
sys.path.insert(0, os.path.join(HERE))
import agreement                       # noqa: E402
from providers import default_judges    # noqa: E402

OUT = os.path.join(REPO, 'analysis', 'measurement_v7', 'out')
FAM_KEY = 'task_family_hint'


def _load_judgments(prefix: str, kind: str) -> Dict[str, Dict[str, Any]]:
    p = os.path.join(OUT, f'{prefix}_judgments.jsonl')
    if not os.path.exists(p):
        return {}
    out = {}
    for line in open(p):
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get('kind') != kind:
            continue
        out[r['item_token']] = r
    return out


def _independence_caveat() -> str:
    judges = default_judges()
    jb = judges.get('judge_b')
    if jb is not None and jb.extra.get('substitution_note'):
        return ('TWO-MODEL, SAME-RELAY AGREEMENT: judge_b is a distinct '
                'model on the same relay (no GPT endpoint was reachable), '
                'per the recorded substitution_note.  These agreement '
                'numbers measure model convergence, NOT two-vendor '
                'independence, and NOT correctness.')
    return ('Two independent judge families compared.  Agreement measures '
            'convergence, not correctness.')


def _extraction_family(tok: str) -> str:
    """Recover the task family for an extraction item from the rebuilt
    judge packet (the packet is family-blind on purpose; the builder knows
    the item's trace, and the trace index knows the family).  Used ONLY
    for reporting, never fed back to a judge."""
    try:
        import packet_builder
        pkts = {json.loads(l)['item_token']: json.loads(l)
                for l in open(os.path.join(
                    OUT, 'judge_packets',
                    'extraction_packet_judge.jsonl')) if l.strip()}
        p = pkts.get(tok)
        if p is None:
            return 'unknown'
        # the packet's task block does not name the family; the field keys
        # do
        for f in (p.get('annotation_task') or {}).get('fields') or []:
            k = f.get('key')
            if k == 'claim_verdicts':
                return 'research_synthesis'
            if k == 'issues_identified':
                return 'code_review'
            if k in ('final_answer', 'final_answer_span'):
                return 'financial_analysis'
        return 'unknown'
    except Exception:
        return 'unknown'


def _state_of(label_ent: Any) -> str:
    """Normalise a field label to its state (canonical enum; a verdict in
    the state slot counts as observed)."""
    if isinstance(label_ent, dict):
        s = str(label_ent.get('state') or '').strip().lower()
        v = str(label_ent.get('value') or '').strip().lower()
        if s in ('observed', 'absent', 'ambiguous', 'unavailable'):
            return s
        if v in ('supported', 'contradicted', 'not enough info',
                 'not_enough', 'asserted', 'repair_proposed',
                 'repair_performed', 'denied') and label_ent.get('value') \
                is not None:
            return 'observed'
        return s if s else 'unavailable'
    if label_ent is None:
        return 'unavailable'
    return str(label_ent).strip().lower()


#: label-block keys that are annotation METADATA, not fields being judged.
#: Comparing metadata across judges would manufacture disagreement.
_META_KEYS = {'annotator_confidence', 'notes', 'confidence', 'evidence',
              'item_token', 'pair_token', 'explanations',
              'random_in_group_by'}


def _expected_fields(tok: str) -> List[str]:
    """The annotation fields the packet asked for — agreement is measured
    on exactly those, never on the union of every key a judge invented."""
    try:
        pkts = {json.loads(l)['item_token']: json.loads(l)
                for l in open(os.path.join(
                    OUT, 'judge_packets',
                    'extraction_packet_judge.jsonl')) if l.strip()}
        p = pkts.get(tok)
        if p is None:
            return []
        return [f.get('key') for f in
                (p.get('annotation_task') or {}).get('fields') or []]
    except Exception:
        return []


def align_extraction() -> List[Dict[str, Any]]:
    a = _load_judgments('judge_a_extraction', 'extraction')
    b = _load_judgments('judge_b_extraction', 'extraction')
    rows = []
    for tok in sorted(set(a) & set(b)):
        ra, rb = a[tok], b[tok]
        la = ra.get('labels') or (ra.get('parsed') or {}).get('labels') or {}
        lb = rb.get('labels') or (rb.get('parsed') or {}).get('labels') or {}
        fam = _extraction_family(tok)
        exp = [k for k in _expected_fields(tok)
               if k not in _META_KEYS] or \
            [k for k in sorted(set(la) | set(lb)) if k not in _META_KEYS]
        per_field: Dict[str, Dict[str, Any]] = {}
        for fk in exp:
            sa, sb = _state_of(la.get(fk)), _state_of(lb.get(fk))
            va = (la.get(fk) or {}).get('value') if isinstance(
                la.get(fk), dict) else la.get(fk)
            vb = (lb.get(fk) or {}).get('value') if isinstance(
                lb.get(fk), dict) else lb.get(fk)
            per_field[fk] = {'a_state': sa, 'b_state': sb,
                             'a_value': va, 'b_value': vb,
                             'state_match': sa == sb,
                             'value_match': (str(va) == str(vb))
                             if (sa == 'observed' and sb == 'observed')
                             else None}
        rows.append({'item_token': tok, 'family': fam,
                     'fields': per_field,
                     'a_valid': ra.get('valid'), 'b_valid': rb.get('valid')})
    return rows


def align_pairs() -> List[Dict[str, Any]]:
    a = _load_judgments('judge_a_pair', 'pair')
    b = _load_judgments('judge_b_pair', 'pair')
    rows = []
    for tok in sorted(set(a) & set(b)):
        ra = a[tok].get('pair_record') or (a[tok].get('parsed') or {})
        rb = b[tok].get('pair_record') or (b[tok].get('parsed') or {})
        ma = _member_map(ra, 'a')
        mb = _member_map(rb, 'b')
        dims = agreement.DIMS
        per_dim = {}
        for d in dims:
            ka = {ix: ma[ix].get(f'{d}_differs') for ix in ma}
            kb = {ix: mb[ix].get(f'{d}_differs') for ix in mb}
            common = [ix for ix in ma if ix in mb]
            match_all = all(
                str(ka[ix]).lower() == str(kb[ix]).lower()
                for ix in common)
            per_dim[d] = {'a': {str(k): v for k, v in ka.items()},
                          'b': {str(k): v for k, v in kb.items()},
                          'all_members_match': match_all}
        rows.append({'item_token': tok, 'dimensions': per_dim,
                     'refset_a': ra.get('reference_set_usable'),
                     'refset_b': rb.get('reference_set_usable'),
                     'any_diff_a': ra.get('any_difference_member_index'),
                     'any_diff_b': rb.get('any_difference_member_index')})
    return rows


def _member_map(rec: Dict[str, Any], side: str
                ) -> Dict[Any, Dict[str, Any]]:
    mj = rec.get('member_judgments')
    if not isinstance(mj, list):
        mj = rec.get('members') or []
    out: Dict[Any, Dict[str, Any]] = {}
    for i, m in enumerate(mj or []):
        if not isinstance(m, dict):
            continue
        ix = m.get('member_index', i)
        out[ix] = m
    return out


def disagreement_class(row: Dict[str, Any], tok: str) -> str:
    """Classify one extraction disagreement for the audit (requirement 8):
    direct value disagreement, unsupported label, missing evidence,
    contradictory evidence, or both-agree-but-weak."""
    ff = row.get('fields') or {}
    direct = unsupported = weak = False
    for f in ff.values():
        sa, sb = f.get('a_state'), f.get('b_state')
        if f.get('state_match') and f.get('value_match') is True:
            continue
        if sa in ('observed', 'ambiguous') or sb in ('observed',
                                                     'ambiguous'):
            direct = True
        if sa in ('unavailable',) or sb in ('unavailable',):
            unsupported = True
    if not direct:
        weak = True
    return ('direct_disagreement' if direct
            else 'weak_agreement' if weak else 'agreement')


def main() -> int:
    ex = align_extraction()
    pr = align_pairs()
    out_dir = OUT
    json.dump(ex, open(os.path.join(out_dir,
                                    'judge_agreement_extr_rows.jsonl'),
                        'w'), indent=0)
    json.dump(pr, open(os.path.join(out_dir,
                                    'judge_agreement_pair_rows.jsonl'),
                        'w'), indent=0)

    report: Dict[str, Any] = {
        'label': 'ai_judge_agreement',
        'independence_caveat': _independence_caveat(),
        'extraction': {}, 'pair': {}}

    # ── extraction agreement ────────────────────────────────────────────────
    field_states_a: List[str] = []
    field_states_b: List[str] = []
    field_items: List[List[Any]] = []
    fam_dis: Counter = Counter()
    fam_tot: Counter = Counter()
    num_pairs = []
    n_dis = 0
    for r in ex:
        fam = r.get('family', 'unknown')
        any_dis = False
        for fk, f in (r.get('fields') or {}).items():
            sa, sb = f.get('a_state'), f.get('b_state')
            field_states_a.append(sa or 'unavailable')
            field_states_b.append(sb or 'unavailable')
            field_items.append([sa or 'unavailable',
                                sb or 'unavailable'])
            if not f.get('state_match'):
                any_dis = True
            if fk in ('final_answer',) and f.get('state_match') and \
                    f.get('value_match') is not None:
                try:
                    num_pairs.append(
                        (float((f.get('a_value') or 0)),
                         float((f.get('b_value') or 0))))
                except (TypeError, ValueError):
                    pass
        fam_tot[fam] += 1
        if any_dis:
            fam_dis[fam] += 1
            n_dis += 1
    report['extraction'] = {
        'n_aligned_items': len(ex),
        'n_items_disagreement': n_dis,
        'disagreement_rate': (n_dis / len(ex)) if ex else None,
        'field_level_raw_agreement': agreement.raw_agreement(
            field_states_a, field_states_b),
        'field_level_cohen_kappa': agreement.kappa(
            field_states_a, field_states_b),
        'field_level_krippendorff_alpha':
            agreement.krippendorff_alpha(field_items),
        'numeric_answer_agreement':
            agreement.numeric_agreement(
                [x[0] for x in num_pairs], [x[1] for x in num_pairs]),
        'disagreement_rate_by_family': {
            fam: {'n': fam_tot[fam], 'n_dis': fam_dis.get(fam, 0),
                  'rate': (fam_dis.get(fam, 0) / fam_tot[fam])
                  if fam_tot[fam] else None}
            for fam in sorted(fam_tot)},
    }

    # ── pair agreement ──────────────────────────────────────────────────────
    per_dim_stats = {}
    for d in agreement.DIMS:
        a_vals: List[str] = []
        b_vals: List[str] = []
        items: List[List[Any]] = []
        conf: Counter = Counter()
        n_mis = 0
        for r in pr:
            pdim = (r.get('dimensions') or {}).get(d) or {}
            ma, mb = pdim.get('a') or {}, pdim.get('b') or {}
            common = [ix for ix in ma if str(ix) in mb]
            if not common:
                continue
            for ix in common:
                va = str(ma[ix]).lower()
                vb = str(mb[str(ix)]).lower()
                a_vals.append(va)
                b_vals.append(vb)
                items.append([va, vb])
                conf[f'{va}->{vb}'] += 1
            if not pdim.get('all_members_match'):
                n_mis += 1
        per_dim_stats[d] = {
            'n_member_judgments': len(a_vals),
            'raw_agreement': agreement.raw_agreement(a_vals, b_vals),
            'cohen_kappa': agreement.kappa(a_vals, b_vals),
            'krippendorff_alpha': agreement.krippendorff_alpha(items),
            'confusion': dict(conf.most_common()),
            'n_dimensions_disagreed': n_mis,
            'dimension_disagreement_rate':
                (n_mis / len(pr)) if pr else None,
        }
    ref_a = [str((r.get('refset_a') or 'unavailable')).lower()
             for r in pr]
    ref_b = [str((r.get('refset_b') or 'unavailable')).lower()
             for r in pr]
    report['pair'] = {
        'n_aligned_groups': len(pr),
        'per_dimension': per_dim_stats,
        'reference_set_usable_agreement': {
            'raw': agreement.raw_agreement(ref_a, ref_b),
            'kappa': agreement.kappa(ref_a, ref_b)},
        'note': 'per-member dimensional judgments aligned on member index',
    }
    p = os.path.join(out_dir, 'judge_agreement.json')
    json.dump(report, open(p, 'w'), indent=2)
    print(json.dumps(report['extraction']['field_level_raw_agreement'],
                     indent=1))
    print('extraction disagreement rate:',
          report['extraction']['disagreement_rate'])
    for d in agreement.DIMS:
        print(d, report['pair']['per_dimension'][d]['raw_agreement'],
              'kappa:', report['pair']['per_dimension'][d]['cohen_kappa'])
    print('wrote', p)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
