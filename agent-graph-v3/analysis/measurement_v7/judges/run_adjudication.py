#!/usr/bin/env python3.11
"""
Adjudication driver (requirement 9).

A SEPARATELY CONFIGURED THIRD MODEL (claude-opus-4-8 on the relay —
independent of both judge models and of each other's outputs) resolves the
disagreements found in the two-judge agreement analysis.  The adjudicator
sees: the original evidence (the blinded packet excerpt), the annotation
rubric (the packet's task block), and BOTH candidate label-sets ANONYMOUSLY
(candidate-1 / candidate-2, no model identity).  An evidence-based
explanation is required; genuinely ambiguous items are left unresolved,
never forced.

OUTPUTS (all under out/; no human file is touched):
  out/judge_adjudicated_ai.jsonl       adjudicated labels + verdicts
  out/judge_adjudicated_unresolved.jsonl
  out/judge_adjudication_report.json   counts + the required labelling
                                       (AI adjudication, NOT human GT)

Chunked foreground execution: the cache makes this resumable.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, os.path.join(REPO, 'analysis', 'measurement_v7'))
sys.path.insert(0, os.path.join(HERE))
sys.path.insert(0, os.path.join(
    REPO, 'analysis', 'measurement_v7', 'adjudication'))
from providers import JudgeConfig                    # noqa: E402
from runner import JudgeClient                      # noqa: E402
import adjudicate as ADJ                              # noqa: E402

OUT = os.path.join(REPO, 'analysis', 'measurement_v7', 'out')


def _adjudicator() -> JudgeConfig:
    jc = JudgeConfig(
        name='adjudicator', family='claude_opus', provider='anthropic',
        model='claude-opus-4-8',
        base_url=os.environ.get('ANTHROPIC_BASE_URL'),
        credential_var='ANTHROPIC_AUTH_TOKEN',
        temperature=0.0, max_tokens=8192,
        cache_dir=os.path.join(OUT, 'adjudicator_cache'))
    jc.extra['independence'] = ('third model, distinct from judge_a '
                                '(claude-opus-5-5) and judge_b '
                                '(claude-sonnet-5-5); same relay')
    return jc


def _packets(kind: str) -> Dict[str, Dict[str, Any]]:
    fn = ('extraction_packet_judge.jsonl' if kind == 'extraction'
          else 'pair_packet_judge.jsonl')
    key = 'item_token' if kind == 'extraction' else 'pair_token'
    p = os.path.join(OUT, 'judge_packets', fn)
    if not os.path.exists(p):
        return {}
    return {json.loads(l)[key]: json.loads(l)
            for l in open(p) if l.strip()}


def _disagreements() -> List[Dict[str, Any]]:
    rows = []
    ex = json.load(open(os.path.join(
        OUT, 'judge_agreement_extr_rows.jsonl')))
    for r in ex:
        bad = {fk: f for fk, f in r['fields'].items()
               if not f.get('state_match')}
        if bad:
            rows.append({'kind': 'extraction', 'item_token': r['item_token'],
                         'family': r.get('family'), 'fields': bad,
                         'fields_all': r['fields']})
    pr = json.load(open(os.path.join(
        OUT, 'judge_agreement_pair_rows.jsonl')))
    for r in pr:
        bad = {d: dd for d, dd in r['dimensions'].items()
               if not dd.get('all_members_match')}
        if bad:
            rows.append({'kind': 'pair', 'item_token': r['item_token'],
                         'family': None, 'fields': bad,
                         'fields_all': r['dimensions']})
    return rows


def _user_prompt(row: Dict[str, Any], packet: Dict[str, Any]) -> str:
    """Adjudicator input: evidence + rubric + two anonymous candidates."""
    ev_keys = ('task_prompt', 'execution_excerpt') if row['kind'] == \
        'extraction' else ('task_prompt', 'members', 'annotation_task')
    evidence = json.dumps({k: packet.get(k) for k in ev_keys
                           if k in packet}, default=str)[:28000]
    rubric = json.dumps(packet.get('annotation_task') or {},
                        default=str)[:3500]
    # the two candidates come from the agreement rows (a = judge_a's side,
    # b = judge_b's side), anonymised as candidate-1 / candidate-2
    if row['kind'] == 'extraction':
        a_labels = {fk: {'state': f['a_state'], 'value': f['a_value'],
                         'evidence': 'see judge record'}
                    for fk, f in row['fields_all'].items()}
        b_labels = {fk: {'state': f['b_state'], 'value': f['b_value'],
                         'evidence': 'see judge record'}
                    for fk, f in row['fields_all'].items()}
    else:
        a_labels = {d: dd.get('a') for d, dd in row['fields_all'].items()}
        b_labels = {d: dd.get('b') for d, dd in row['fields_all'].items()}
    return ('EXECUTION RECORD:\n' + evidence
            + '\n\nRUBRIC:\n' + rubric
            + '\n\nCANDIDATE-1:\n' + json.dumps(a_labels, default=str)[:4000]
            + '\n\nCANDIDATE-2:\n' + json.dumps(b_labels, default=str)[:4000]
            + ('\n\nDisputed fields: ' + json.dumps(
                sorted(row['fields'])) if row['kind'] == 'extraction'
                else ''))


def _v2_prompt(user: str, item_key: str) -> Dict[str, str]:
    """The strengthened prompt under its own version ('v7-adjudication-
    prompt-2'), so retry completions are separately cached and separately
    attributable from the v1 attempt."""
    import hashlib
    import adjudicate as _A
    blob = hashlib.sha256(
        (user + _A._ADJ_PROMPT).encode()).hexdigest()
    return {'system': _A._ADJ_PROMPT, 'user': user,
            'version': 'v7-adjudication-prompt-2', 'prompt_sha256': blob}


def run(max_calls: int = 40, offset: int = 0) -> Dict[str, Any]:
    os.makedirs(os.path.join(OUT, 'adjudicator_cache'), exist_ok=True)
    cfg = _adjudicator()
    client = JudgeClient(cfg)
    if not cfg.is_configured():
        raise SystemExit('adjudicator not configured; recorded as a '
                         'requirement, not run')
    rows = _disagreements()
    pk_ext = _packets('extraction')
    pk_pair = _packets('pair')
    n_ok = n_fail = n_unres = 0
    out_rows: List[Dict[str, Any]] = []
    unresolved: List[Dict[str, Any]] = []
    todo = rows[offset:offset + max_calls] if offset or max_calls else rows
    for row in todo:
        tok = row['item_token']
        packet = (pk_ext if row['kind'] == 'extraction' else pk_pair).get(
            tok, {})
        user = _user_prompt(row, packet)
        prompt = {'system': ADJ._ADJ_PROMPT, 'user': user,
                  'version': 'v7-adjudication-prompt-1',
                  'prompt_sha256': client._cache_key(
                      {'system': ADJ._ADJ_PROMPT, 'user': user,
                       'version': 'v7-adjudication-prompt-1',
                       'prompt_sha256': ''},
                      'adjudicator', tok)[:32]}
        import hashlib
        prompt['prompt_sha256'] = hashlib.sha256(
            (user + ADJ._ADJ_PROMPT).encode()).hexdigest()
        rec = client.annotate(prompt, 'adjudicator',
                              f"{row['kind']}:{tok}",
                              hashlib.sha256(tok.encode()).hexdigest())
        base = {'item_token': tok, 'kind': row['kind'],
                'family': row.get('family'),
                'adjudicator': 'claude-opus-4-8',
                'class': 'ai_adjudication',
                'note': ('AI adjudication, NOT human ground truth.  '
                         'Third model, distinct from both judges; same '
                         'relay as both judges.'),
                'candidates_anonymized': True}
        if rec['status'] != 'ok':
            n_fail += 1
            base['adjudicated'] = False
            base['reason'] = f'adjudicator {rec["status"]}: ' \
                f'{rec.get("error", "")}'
            out_rows.append(base)
            unresolved.append({'item_token': tok, 'kind': row['kind'],
                               'reason': 'adjudicator_failed'})
            continue
        try:
            parsed = json.loads(__import__('re').sub(
                r'^```(?:json)?\s*|\s*```$', '',
                rec.get('text', '').strip()))
        except json.JSONDecodeError:
            n_fail += 1
            base['adjudicated'] = False
            base['reason'] = 'adjudicator output not JSON; not coerced'
            base['raw'] = rec.get('text', '')[:800]
            out_rows.append(base)
            unresolved.append({'item_token': tok, 'kind': row['kind'],
                               'reason': 'adjudicator_not_json'})
            continue
        n_ok += 1
        uf = parsed.get('unresolved_fields') or []
        base.update({
            'adjudicated': not uf,
            'resolution': parsed.get('resolved_labels'),
            'explanations': parsed.get('explanations') or {},
            'unresolved_fields': uf,
            'confidence': parsed.get('confidence')})
        if uf:
            n_unres += 1
            unresolved.append({'item_token': tok, 'kind': row['kind'],
                               'reason': 'genuinely_ambiguous',
                               'fields': uf})
        out_rows.append(base)
    # merge with any prior adjudicated rows (resumable chunking).  The
    # dedupe key is kind-scoped: extraction item_tokens and pair_tokens
    # live in the same T-token space, so bare item_token is not unique.
    dst = os.path.join(OUT, 'judge_adjudicated_ai.jsonl')
    prior = []
    if os.path.exists(dst):
        prior = [json.loads(l) for l in open(dst) if l.strip()]
    seen = {f"{r.get('kind')}:{r.get('item_token')}" for r in out_rows}
    merged = ([r for r in prior
               if f"{r.get('kind')}:{r.get('item_token')}" not in seen]
              + out_rows)
    with open(dst, 'w') as f:
        for r in merged:
            f.write(json.dumps(r, sort_keys=True) + '\n')
    with open(os.path.join(OUT,
                           'judge_adjudicated_unresolved.jsonl'), 'w') as f:
        for r in unresolved:
            f.write(json.dumps(r, sort_keys=True) + '\n')
    rep = {'n_disagreements_total': len(rows),
           'n_this_chunk': len(todo), 'n_ok': n_ok, 'n_failed': n_fail,
           'n_unresolved_fields': n_unres,
           'n_adjudicated_rows_total': len(merged),
           'adjudicator': 'claude-opus-4-8',
           'caveat': 'AI adjudication, NOT human ground truth.'}
    json.dump(rep, open(os.path.join(OUT,
                                      'judge_adjudication_report.json'),
                         'w'), indent=2)
    print(json.dumps(rep, indent=1))
    return rep


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--max-calls', type=int, default=40)
    ap.add_argument('--offset', type=int, default=0)
    a = ap.parse_args()
    run(a.max_calls, a.offset)
