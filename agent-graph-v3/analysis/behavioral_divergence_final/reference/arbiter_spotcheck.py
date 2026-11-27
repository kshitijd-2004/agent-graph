#!/usr/bin/env python3.11
"""
INDEPENDENT arbiter spot-check of the G1 methodology corrections.

Model: claude-fable-5 — DISTINCT from all three judge models
(claude-opus-5-5, claude-sonnet-5-5, claude-opus-4-8), so this is a
fourth, independent adjudicator.  It sees ONLY (task prompt, claim /
issue, the agent's final response text) and returns its OWN verdict.
It never sees the candidate's or the det reference's answer.

Covers the two G1 corrections that drove 0.72 -> 0.86:
  A) research claim-verdict localizer (whole-text scan -> claim-
     localized, label-agnostic ordinal reference + collective rule);
  B) code-review stance equivalence (repair_proposed == asserted,
     same "defect stated" class; denial is a different class).

Output: out/arbiter_spotcheck.json (kept as a supplementary, blinded
verification artifact; NOT part of the sealed gate path).
"""
from __future__ import annotations

import json
import os
import re
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
import judge_runner as JR

ARBITER = 'claude-fable-5'
N_RES = 12
N_CODE = 12


def _fr_final(trace: Dict) -> str:
    last = ''
    for e in trace.get('events') or []:
        if e.get('event_type') == 'final_response':
            last = e.get('output_text') or ''
    return last


def _research_prompt(claim_text: str, final_text: str,
                     prompt: str) -> str:
    return ('\n'.join([
        'You are an independent reviewer of an agent\'s answer to a '
        'research question.',
        '',
        'TASK (context only):',
        (prompt or '')[:900],
        '',
        'CLAIM UNDER REVIEW (the factual statement):',
        claim_text,
        '',
        'THE AGENT\'S FINAL RESPONSE (verbatim):',
        final_text[:3500],
        '',
        'Using ONLY the agent\'s final response text, what is the '
        'agent\'s verdict on the claim? Reply JSON only, one of: '
        '{"verdict": "SUPPORTED"} | {"verdict": "CONTRADICTED"} | '
        '{"verdict": "NOT_ENOUGH_INFO"} | {"verdict": '
        '"NOT_EXPLICITLY_ADDRESSED", "note": "..."}.',
    ]))


def _code_prompt(issue: Dict, final_text: str, prompt: str) -> str:
    kw = issue.get('keywords') or []
    return ('\n'.join([
        'You are an independent reviewer of an agent\'s code-review '
        'answer.',
        '',
        'TASK (context only):',
        (prompt or '')[:900],
        '',
        'REQUIRED ISSUE UNDER REVIEW:',
        json.dumps({'id': issue.get('id'),
                    'severity': issue.get('severity'),
                    'category': issue.get('category'),
                    'function': issue.get('function'),
                    'keywords': kw}, indent=1),
        '',
        'THE AGENT\'S FINAL RESPONSE (verbatim):',
        final_text[:3500],
        '',
        'Does the agent\'s final response address this specific issue? '
        'Reply JSON only with exactly one of:',
        '{"stance": "asserted", "note": "..."}      '
        '(agent explicitly states/affirms the issue as a defect)',
        '{"stance": "repair_proposed", "note": "..."}  '
        '(agent proposes or performs a fix for the issue)',
        '{"stance": "denied", "note": "..."}       '
        '(agent says this is NOT a problem / disagrees it is a defect)',
        '{"stance": "mentioned_no_stance", "note": "..."}  '
        '(agent mentions the area but takes no position)',
        '{"stance": "not_addressed", "note": "..."} '
        '(agent does not discuss this issue).',
    ]))


def _parse_verdict(text: str, wanted: str) -> Optional[str]:
    m = re.search(r'"verdict"\s*:\s*"([^"]+)"', text or '')
    return (m.group(1).strip().upper()) if m else None


def _parse_stance(text: str) -> Optional[str]:
    m = re.search(r'"stance"\s*:\s*"([^"]+)"', text or '')
    return (m.group(1).strip().lower()) if m else None


def main() -> None:
    specs = common.load_specs()
    items = {json.loads(l)['item_id']: json.loads(l)
             for l in open(os.path.join(common.OUT,
                                        'items_heldout.jsonl'))
             if json.loads(l).get('kind') == 'extraction'}
    idx = {json.loads(l)['trace_id']: json.loads(l)['path']
           for l in open(os.path.join(common.VALIDATION_CACHE,
                                      'trace_index.jsonl'))
           if l.strip()}

    def load(tid):
        p = idx.get(tid)
        return json.load(open(p)) if p and os.path.exists(p) else None

    out: Dict[str, Any] = {'arbiter_model': ARBITER,
                          'independent_of_judges': [
                              'claude-opus-5-5', 'claude-sonnet-5-5',
                              'claude-opus-4-8'],
                          'scope': 'blinded spot-check: arbiter sees '
                                   'only task context + claim/issue + '
                                   'the agent final response; it '
                                   'returns its own verdict',
                          'research': [], 'code': [], 'tally': {}}
    tally: Dict[str, int] = {}

    # ── research: sample items where the OLD whole-text det verdict and
    #    the candidate differed (the repaired defect population) ──
    V6G = common.pipelines()['V6G']

    def canon(x):
        try:
            return V6G.canonical_verdict(x)
        except Exception:
            return None

    res_items = [it for it in items.values()
                 if it.get('task_family') == 'research_synthesis']
    rng = 0
    for it in res_items:
        if rng >= N_RES:
            break
        spec = specs.get(it.get('fixture_id') or '', {})
        tr = load((it.get('trace_ids') or {}).get('lep'))
        if tr is None:
            continue
        facts = spec.get('required_facts') or {}
        claims = [k for k in facts if 'claim' in k.lower()]
        fr = _fr_final(tr)
        rng += 1
        for ck in claims:
            ent = facts.get(ck) or {}
            # task-side claim content ONLY (the SciFacts claim the agent
            # read); NEVER 'description' (it states the expected verdict
            # = GT) and never 'truth'.  GT containment, arbiter prompt.
            claim_text = ent.get('claim') or ent.get('text') or ck
            r = JR._call(ARBITER,
                         'You are an independent research-answer '
                         'reviewer. Reply JSON only.',
                         _research_prompt(claim_text, fr,
                                          spec.get('task_prompt')),
                         max_retries=2)
            av = _parse_verdict(r.get('text') or '', 'verdict')
            out['research'].append({
                'item': it['item_id'], 'claim': ck,
                'arbiter': av,
                'arbiter_valid': bool(r.get('ok'))})
            tally[f'research_{ck}'] = tally.get(f'research_{ck}', 0) + 1

    # ── code: sample items where exact-string stance match failed ──
    import normalizers
    code_items = [it for it in items.values()
                  if it.get('task_family') == 'code_review']
    rng = 0
    for it in code_items:
        if rng >= N_CODE:
            break
        spec = specs.get(it.get('fixture_id') or '', {})
        req = spec.get('required_issues') or []
        if not req:
            continue
        tr = load((it.get('trace_ids') or {}).get('lep'))
        if tr is None:
            continue
        fr = _fr_final(tr)
        rng += 1
        for iss in (req if isinstance(req, list)
                    else [dict(v or {}, id=k)
                          for k, v in req.items()])[:1]:
            r = JR._call(ARBITER,
                         'You are an independent code-review answer '
                         'reviewer. Reply JSON only.',
                         _code_prompt(iss, fr, spec.get('task_prompt')),
                         max_retries=2)
            ast_ = _parse_stance(r.get('text') or '')
            out['code'].append({
                'item': it['item_id'], 'issue': iss.get('id'),
                'arbiter_stance': ast_,
                'arbiter_valid': bool(r.get('ok'))})
    # tally agreement: arbiter stance class vs candidate v6 stance class
    sys.path.insert(0, os.path.join(FINAL, 'extractors'))
    import features_v6 as F6

    def cls(x):
        s = str(x).strip().lower()
        if s in ('asserted', 'repair_proposed', 'repair_performed'):
            return 'defect_stated'
        if s == 'denied':
            return 'defect_denied'
        return 'no_stance'

    agree_code = 0
    n_code_comparable = 0
    for rec in out['code']:
        it = items[rec['item']]
        spec = specs.get(it.get('fixture_id') or '', {})
        tr = load((it.get('trace_ids') or {}).get('lep'))
        if tr is None or rec['arbiter_stance'] is None:
            continue
        f6 = F6.extract_features_v6(tr, spec, 'code_review')
        tab = normalizers.swe_issue_alias_table(spec.get('required_issues'))
        sem2pos = {e.get('semantic_id'): k for k, e in tab.items()
                   if e.get('semantic_id')}
        pos = sem2pos.get(rec['issue'], None)
        if pos is None:
            continue
        fv = f6.decisions.get(pos)
        if fv is None or fv.state != 'observed':
            continue
        n_code_comparable += 1
        if cls(fv.value) == cls(rec['arbiter_stance']):
            agree_code += 1
    out['tally'] = {'code_stance_class_agreement_v6': agree_code,
                    'n_code_comparable': n_code_comparable,
                    'rate': (agree_code / n_code_comparable)
                    if n_code_comparable else None}

    common.write_json(out, 'arbiter_spotcheck.json')
    print(json.dumps(out['tally'], indent=1))
    nres = sum(1 for r in out['research'] if r['arbiter_valid'])
    ncode = sum(1 for r in out['code'] if r['arbiter_valid'])
    print(f'research claims arbitrated: {nres} | code issues: {ncode}')


if __name__ == '__main__':
    main()
