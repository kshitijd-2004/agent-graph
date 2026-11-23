#!/usr/bin/env python3.11
"""
Human-audit package builder (requirement 10).

A COMPACT package of ~50-75 items for a human reviewer, mixing:

  * a probability-sampled component of AI agreements (so agreement ACCURACY
    is estimable, not just difficult-case coverage),
  * AI disagreements,
  * zero-threshold positives,
  * frozen/v6/v7 disagreements,
  * difficult numerical answers,
  * ambiguous scientific verdicts,
  * SWE-bench issue-identification cases.

It is a PACKAGE — the audit has NOT been performed.  No human labels are
implied or fabricated; the output records that it is pending human
completion and provides a simple annotation format.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
V7 = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, V7)
import base  # noqa: E402

OUT = os.path.join(V7, 'out')


def _load(path, key='trace_id'):
    if not os.path.exists(path):
        return {}
    return {r[key]: r for r in
            (json.loads(l) for l in open(path) if l.strip())}


def build(n_target: int = 60) -> dict:
    frozen = _load(os.path.join(base.CONS, 'bnd_v41_run',
                                'bnd_v41_per_trace.jsonl'))
    v6 = _load(os.path.join(base.V6_DIR, 'out', 'per_trace_v6.jsonl'))
    v7 = _load(os.path.join(OUT, 'per_trace_v7.jsonl'))
    common = sorted(set(frozen) & set(v6) & set(v7))

    # bucket each common row by interesting properties
    buckets = {
        'ai_disagreement': [],          # v6 and v7 differ on union
        'zero_threshold_positive': [],  # v7 positive resting only on theta=0
        'frozen_v6_v7_disagreement': [],
        'difficult_numeric': [],
        'ambiguous_verdict': [],
        'swe_issue_id': [],
    }
    for t in common:
        f, s, r = frozen[t], v6[t], v7[t]
        mf = bool(f.get('m1_whole_trace_positive') or f.get('m1'))
        m6 = bool(s.get('m1_union'))
        m7 = bool(r.get('m1_union'))
        u7 = r.get('union') or {}
        trig = u7.get('triggered_dimensions') or []
        zt = u7.get('zero_threshold_triggered') or []
        rec = {'trace_id': t, 'family': r.get('task_family'),
               'm1_frozen': mf, 'm1_v6': m6, 'm1_v7': m7,
               'v7_triggered': trig, 'v7_zero_only':
               bool(trig) and set(zt) == set(trig)}
        if m6 != m7:
            buckets['ai_disagreement'].append(rec)
        if rec['v7_zero_only']:
            buckets['zero_threshold_positive'].append(rec)
        if not (mf == m6 == m7):
            buckets['frozen_v6_v7_disagreement'].append(rec)
        if r.get('task_family') == 'financial_analysis' and \
                any(d == 'outcomes' for d in trig):
            buckets['difficult_numeric'].append(rec)
        if r.get('task_family') == 'research_synthesis' and \
                (r.get('per_dimension', {}).get('assertions', {})
                 .get('zero_threshold') or 'ambiguous' in
                 json.dumps(r.get('per_dimension', {}))):
            buckets['ambiguous_verdict'].append(rec)
        if r.get('task_family') == 'code_review' and \
                'assertions' in trig:
            buckets['swe_issue_id'].append(rec)

    # probability-sampled agreement component: rows where all three agree,
    # drawn with the fixture-cluster seed so the agreement-accuracy estimate
    # has a known sampling basis
    rng = random.Random(20261129)
    agreed = [t for t in common
              if bool(frozen[t].get('m1_whole_trace_positive') or
                      frozen[t].get('m1')) ==
              bool(v6[t].get('m1_union')) ==
              bool(v7[t].get('m1_union'))]
    rng.shuffle(agreed)
    n_agreed = max(15, n_target // 4)
    # reserve slots for the AI-judge component (judge disagreements + a
    # probability-sampled slice of judge agreements), so it is not crowded
    # out by the implementation target-case buckets
    n_ai = max(8, n_target // 6)
    n_buckets = max(0, n_target - n_agreed - n_ai)
    sel = []
    sel += agreed[:n_agreed]
    for b in ('ai_disagreement', 'zero_threshold_positive',
              'frozen_v6_v7_disagreement', 'difficult_numeric',
              'ambiguous_verdict', 'swe_issue_id'):
        remaining = max(0, n_target - n_ai - len(sel))
        if len(sel) >= n_target - n_ai:
            break
        sel += [r['trace_id'] for r in buckets[b]][:max(0, remaining)]
        sel = sel[:n_target - n_ai]
    # AI-judge components: disagreements between judge A and judge B, and
    # a probability-sampled slice of judge AGREEMENTS (so the accuracy of
    # the agreement itself is estimable, not only difficult cases).
    ai = _ai_judge_component(OUT, n_target, rng, sel, n_ai)
    if ai['items']:
        sel += [r['trace_id'] for r in ai['items']]
    sel = sel[:n_target]
    # dedupe keeping order
    seen, items = set(), []
    for t in sel:
        if t in seen:
            continue
        seen.add(t)
        rec = next((r for r in sum(buckets.values(), []) if r['trace_id'] == t),
                   {'trace_id': t, 'family': v7[t].get('task_family'),
                    'm1_frozen': bool(frozen[t].get(
                        'm1_whole_trace_positive') or
                        frozen[t].get('m1')),
                    'm1_v6': bool(v6[t].get('m1_union')),
                    'm1_v7': bool(v7[t].get('m1_union')),
                    'v7_triggered': (v7[t].get('union') or {}).get(
                        'triggered_dimensions'),
                    'v7_zero_only': False})
        # attach the AI-judge component if this trace is one of its items
        for ai_item in ai['items']:
            if ai_item['trace_id'] == t:
                rec['ai_component'] = ai_item.get('ai_component')
                rec['ai_disagreement'] = ai_item.get('ai_disagreement')
                rec['pair_token'] = ai_item.get('pair_token')
                rec['disputed_dimensions'] = \
                    ai_item.get('disputed_dimensions')
                break
        rec['source_component'] = 'probability_sampled_agreement' if \
            t in agreed[:n_agreed] else 'targeted_case'
        rec['human_label'] = None   # PENDING, not fabricated
        items.append(rec)
    out = {
        'n_items': len(items),
        'n_probability_sampled_agreement': n_agreed,
        'note': ('This is a human-audit PACKAGE, not a completed audit.  '
                 'No human labels have been collected; each '
                 '`human_label` is null by design.  A reviewer fills the '
                 '`human_label` (divergent? which dimension? why?) and the '
                 'file is then a genuine human reference, reported '
                 'separately from AI-adjudicated reference performance.'),
        'annotation_format': {
            'divergent': 'yes|no|ambiguous|unavailable',
            'triggering_dimensions': ['assertions', 'decisions', 'actions',
                                      'outcomes'],
            'rationale': 'free text, cite the evidence span',
        },
        'items': items,
    }
    p = os.path.join(OUT, 'human_audit_package.json')
    json.dump(out, open(p, 'w'), indent=2)
    print(f'wrote {p} with {len(items)} items '
          f'({n_agreed} probability-sampled agreement, '
          f'{len(items) - n_agreed} targeted cases); '
          f'human audit NOT performed')
    return out


def _ai_judge_component(OUT, n_target, rng, current_sel, n_ai):
    """AI-judge cases for the audit: disagreements between judge A and
    judge B (high-value hard cases) plus a probability-sampled slice of
    judge AGREEMENTS (so agreement ACCURACY is estimable, not only
    difficult-case coverage).  Trace ids resolve through the blinded
    pair-role cache (token -> trace_id), which is the validation
    framework's own map — read-only."""
    items = []
    # group pair_token -> target (LEP) trace_id, via the pair packet's
    # member tokens and the blinded role cache (target role holds the LEP).
    roles = json.load(open(os.path.join(
        base.VALIDATION_CACHE, 'blind_pair_roles.json')))
    member2rec = {t: r for grp in roles.get('roles', {}).values()
                  for t, r in grp.items()}
    group2target: Dict[str, Any] = {}
    pkr = os.path.join(OUT, 'judge_packets', 'pair_packet_judge.jsonl')
    if os.path.exists(pkr):
        for l in open(pkr):
            if not l.strip():
                continue
            d = json.loads(l)
            gtok = d.get('pair_token')
            target = None
            for m in (d.get('members') or []):
                rec = member2rec.get(m.get('item_token'))
                if rec and rec.get('role') == 'target':
                    target = rec.get('trace_id')
                    break
            if target:
                group2target[gtok] = target
    agr = os.path.join(OUT, 'judge_agreement_pair_rows.jsonl')
    dis, agree = set(), set()
    dis_dims: Dict[str, Any] = {}
    if os.path.exists(agr):
        rows = json.load(open(agr))     # a JSON array, not JSONL
        for r in rows:
            dd = r.get('dimensions') or {}
            bad = [d for d in dd
                   if not (dd.get(d) or {}).get('all_members_match', True)]
            if bad:
                dis.add(r['item_token'])
                dis_dims[r['item_token']] = bad
            else:
                agree.add(r['item_token'])
    n_dis = max(4, n_ai // 2)
    picked = set()
    for tok in sorted(dis):
        tid = group2target.get(tok)
        if tid and tid not in current_sel and tid not in picked:
            items.append({'trace_id': tid, 'pair_token': tok,
                         'ai_component': 'ai_disagreement',
                         'ai_disagreement': True,
                         'disputed_dimensions': dis_dims.get(tok)})
            picked.add(tid)
        if len(items) >= n_dis:
            break
    n_aga = max(4, n_ai - n_dis)
    rng2 = random.Random(20261129)
    agree_sorted = sorted(agree)
    for tok in rng2.sample(agree_sorted, min(n_aga, len(agree_sorted))):
        tid = group2target.get(tok)
        if tid and tid not in current_sel and tid not in picked:
            items.append({'trace_id': tid, 'pair_token': tok,
                         'ai_component': 'ai_agreement_sampled',
                         'ai_disagreement': False})
            picked.add(tid)
    return {'items': items}


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=60)
    a = ap.parse_args()
    build(a.n)
