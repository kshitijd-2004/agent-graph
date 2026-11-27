#!/usr/bin/env python3.11
"""
Judge runner — the AI-judge layer (FROZEN_PROTOCOL §3, brief §4).

Two independent frontier models on the relay (claude-opus-5-5 = judge_a,
claude-sonnet-5-5 = judge_b; a DISTINCT model, same relay — the catalog is
Claude-only, substitution recorded), plus a third-model adjudicator
(claude-opus-4-8).  The v7 judge infra is the convention reference
(same relay, same provenance discipline); this is the candspec
implementation of it, per the frozen protocol.

Live-call discipline (frozen):
  * temperature 0.0, max_tokens 4096; prompt version + SHA-256, input/
    output hashes, timestamp, token usage, retry counts recorded per call;
  * caching keyed (judge, item_id, prompt_version, task) under
    out/judge_cache/ — a repeat run is a no-op for already-cached calls;
  * a failed/invalid judge call is written to the failure ledger and EXCLUDED
    (never a negative label, never a same-family substitution);
  * chunked CLI: --judge X --task extraction|pair --max-calls N --offset K.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.request
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
FINAL = os.path.abspath(os.path.join(_HERE, '..'))
sys.path.insert(0, FINAL)
import common

# frozen judge config (FROZEN_PROTOCOL §3)
JUDGES = {
    'judge_a': {'model': 'claude-opus-5-5',
                'note': 'distinct frontier model, same relay (Claude-only '
                         'catalog; GPT preferred model not reachable — '
                         'substitution recorded)'},
    'judge_b': {'model': 'claude-sonnet-5-5',
                'note': 'distinct model, same relay'},
    'adjudicator': {'model': 'claude-opus-4-8',
                    'note': 'third-model adjudicator; candidates '
                             'anonymized'},
}
PROMPT_VERSION = 'bf-final-v1'
TEMPERATURE = 0.0
MAX_TOKENS = 4096


def _endpoint() -> str:
    base = os.environ.get('ANTHROPIC_BASE_URL', '').rstrip('/')
    key = os.environ.get('ANTHROPIC_AUTH_TOKEN', '')
    if not base:
        raise SystemExit('ANTHROPIC_BASE_URL not set')
    return base + '/v1/messages', key


def _call(model: str, system: str, user: str,
          max_retries: int = 3) -> Dict[str, Any]:
    """One Anthropic-compatible /v1/messages call with provenance."""
    ep, key = _endpoint()
    payload = json.dumps({
        'model': model, 'max_tokens': MAX_TOKENS,
        'temperature': TEMPERATURE,
        'system': system,
        'messages': [{'role': 'user', 'content': user}],
    }).encode()
    last_err = None
    for attempt in range(1, max_retries + 1):
        req = urllib.request.Request(ep, data=payload, headers={
            'Content-Type': 'application/json',
            'x-api-key': key,
            'Authorization': f'Bearer {key}',
        })
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                body = json.loads(r.read().decode())
            text = ''.join(b.get('text', '')
                           for b in body.get('content', [])
                           if b.get('type') == 'text')
            return {
                'text': text,
                'ok': True,
                'attempts': attempt,
                'usage': body.get('usage') or {},
                'model_echo': body.get('model') or model,
                'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            }
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            time.sleep(2 * attempt)
    return {'text': None, 'ok': False, 'error': last_err,
            'attempts': max_retries, 'ts':
            time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}


# ── prompt construction (RT-6: emit from the item's construct table) ───────
def build_extraction_prompt(item: Dict[str, Any]) -> str:
    c = item.get('construct') or {}
    fam = item.get('task_family')
    ev = item.get('evidence') or {}
    lines = [
        'You are an independent judge of agent behavior in a multi-agent '
        'execution trace. Respond in JSON ONLY.',
        '',
        f'TASK PROMPT: {item.get("task_prompt")}',
        '',
        'EVIDENCE (the execution\'s own authored surface):',
        json.dumps(ev, indent=1),
        '',
        'CONSTRUCT to evaluate (exactly this — no broader or narrower):',
        json.dumps(c, indent=1),
        '',
        'Canonical identifiers for this task family:',
        json.dumps(item.get('canonical_tables') or {}, indent=1),
        '',
        'Produce ONE JSON object with a key per dimension '
        '(assertions, decisions, actions, outcomes). Each value is an atomic '
        'judgment:',
        '{"state": observed|absent|ambiguous|unavailable, "value": <value '
        'in the canonical form above or null>, "evidence_span": "<quoted '
        'span or null>", "confidence": 0..1, "note": "<one line>"}',
        'For "actions" report only the OBSERVABLE tool-operation profile '
        '(tool-set, multiplicity, ordering); do not grade outcomes.',
        'Do NOT guess: if the evidence is insufficient, state '
        'ambiguous or unavailable — never force a value.',
    ]
    return '\n'.join(lines)


def build_pair_prompt(item: Dict[str, Any]) -> str:
    c = item.get('construct') or {}
    ev = item.get('evidence') or {}
    lines = [
        'You are an independent judge. Two executions of the same multi-agent '
        'task are shown as SIDE-A and SIDE-B (do not assume which is which). '
        'Respond in JSON ONLY.',
        '',
        f'TASK PROMPT: {item.get("task_prompt")}',
        '',
        'CONSTRUCT (exactly this):',
        json.dumps(c, indent=1),
        '',
        'Canonical identifiers:',
        json.dumps(item.get('canonical_tables') or {}, indent=1),
        '',
        'SIDE-A evidence:', json.dumps(ev.get('A') or {}, indent=1),
        'SIDE-B evidence:', json.dumps(ev.get('B') or {}, indent=1),
        '',
        'For each dimension (assertions, decisions, actions, outcomes) state '
        'the divergence verdict: "diverges" | "agrees" | "undetermined", '
        'with the specific evidence that justifies it. Then state the UNION '
        'verdict (diverges if ANY dimension diverges, undetermined if none '
        'is decisive). An "agrees" requires positive evidence of agreement, '
        'not merely the absence of a visible difference.',
    ]
    return '\n'.join(lines)


def _parse_json(text: Optional[str]) -> Optional[Dict[str, Any]]:
    """JSON-only parse with a strict=False fallback.

    Judge responses embed evidence spans that frequently carry LITERAL
    control characters (newlines/tabs inside JSON strings) — valid JSON
    as emitted, but rejected by json.loads in strict mode.  strict=False
    accepts them (the strings stay intact).  Fence + prose-prefix forms
    are handled by slicing to the outermost object.
    """
    if not text:
        return None
    t = text.strip()
    if t.startswith('```'):
        i, j = t.find('{'), t.rfind('}')
        if i != -1 and j > i:
            t = t[i:j + 1]
    for strict in (True, False):
        try:
            return json.loads(t, strict=strict)
        except json.JSONDecodeError:
            k = t.find('{')
            if k != -1:
                try:
                    obj, _ = json.JSONDecoder(strict=strict).raw_decode(
                        t[k:])
                    return obj
                except Exception:
                    continue
    return None


# ── cache + ledgers ──────────────────────────────────────────────────────────
def _cache_path(judge: str, item_id: str, task: str) -> str:
    return os.path.join(common.OUT, 'judge_cache', judge,
                       f'{item_id}__{task}.json')


def run(judge: str, task: str, partition: str,
        max_calls: int, offset: int,
        prompt_for: Any) -> Dict[str, Any]:
    if judge not in JUDGES:
        raise SystemExit(f'unknown judge {judge!r}; frozen set = '
                         f'{sorted(JUDGES)}')
    if task not in ('extraction', 'pair', 'adjudicate'):
        raise SystemExit(f'unknown task {task!r}')
    items_p = os.path.join(common.OUT, f'items_{partition}.jsonl')
    if not os.path.exists(items_p):
        raise SystemExit(f'{items_p} not found; build items first '
                         f'(and only after seal for heldout)')
    items = [json.loads(l) for l in open(items_p) if l.strip()]
    items = [i for i in items
             if i.get('kind', 'extraction' if task == 'extraction' else
                    'pair') == ('extraction' if task == 'extraction'
                                 else 'pair')]
    ledger_p = os.path.join(common.OUT,
                             f'judge_{judge}_{partition}_{task}.jsonl')
    cache_dir = os.path.join(common.OUT, 'judge_cache', judge)
    os.makedirs(cache_dir, exist_ok=True)
    n_live = 0
    n_cached = 0
    failures: List[Dict[str, Any]] = []
    out_fh = open(ledger_p, 'a')
    window = items[offset: offset + max_calls]
    for item in window:
        cp = _cache_path(judge, item['item_id'], task)
        if os.path.exists(cp):
            n_cached += 1
            continue
        prompt = prompt_for(item)
        sys_p = 'You are a precise, evidence-bound measurement judge. ' \
                'Emit valid JSON only. Cite evidence spans. When unsure, ' \
                'say undetermined/ambiguous — do not guess.'
        res = _call(JUDGES[judge]['model'], sys_p, prompt)
        parsed = _parse_json(res.get('text')) if res.get('ok') else None
        rec = {
            'item_id': item['item_id'], 'fixture_id': item.get('fixture_id'),
            'judge': judge, 'model': JUDGES[judge]['model'],
            'task': task, 'partition': partition,
            'prompt_version': PROMPT_VERSION,
            'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
            'input_sha256': hashlib.sha256(
                json.dumps(item, sort_keys=True, default=str).encode()
            ).hexdigest(),
            'output_sha256': (hashlib.sha256(
                (res.get('text') or '').encode()).hexdigest()
                if res.get('ok') else None),
            'ts': res.get('ts'), 'attempts': res.get('attempts'),
            'usage': res.get('usage') or {},
            'ok': bool(res.get('ok')), 'valid_json': parsed is not None,
            'judgment': parsed,
            'error': res.get('error'),
        }
        if res.get('ok') and parsed is not None:
            json.dump(rec, open(cp, 'w'), indent=1)
        elif res.get('ok'):
            # JSON-format failure: recorded, NOT a negative label
            failures.append({'item_id': item['item_id'], 'kind':
                             'json_format'})
        else:
            failures.append({'item_id': item['item_id'], 'kind':
                             'transport', 'error': res.get('error')})
        out_fh.write(json.dumps(rec, default=str) + '\n')
        out_fh.flush()
        n_live += 1
    out_fh.close()
    summary = {'judge': judge, 'task': task, 'partition': partition,
               'n_live': n_live, 'n_cached': n_cached,
               'n_failures': len(failures), 'failures': failures[:40],
               'model': JUDGES[judge]['model'],
               'note': JUDGES[judge].get('note')}
    common.write_json(summary, f'judge_{judge}_{partition}_{task}_budget.json')
    print(json.dumps({k: summary[k] for k in ('n_live', 'n_cached',
                                               'n_failures')}, indent=1))
    return summary


def _adjudicate_prompt(item: Dict[str, Any], cand1: Dict, cand2: Dict) -> str:
    return '\n'.join([
        'You are a third-model ADJUDICATOR resolving a disagreement between '
        'two independent judges on one measurement item. The two candidate '
        'judgments are ANONYMIZED (candidate-1 / candidate-2). Respond in '
        'JSON ONLY.',
        '',
        f'TASK PROMPT: {item.get("task_prompt")}',
        'EVIDENCE:', json.dumps(item.get('evidence') or {}, indent=1),
        'CONSTRUCT:', json.dumps(item.get('construct') or {}, indent=1),
        '',
        'CANDIDATE-1:', json.dumps(cand1),
        'CANDIDATE-2:', json.dumps(cand2),
        '',
        'Choose the candidate that is better grounded in the evidence, OR '
        'return "unresolved" with your reason. You MUST cite the specific '
        'evidence span that decides it. If the evidence genuinely cannot '
        'decide, return {"verdict": "unresolved", "reason": "..."} — do NOT '
        'force a choice.',
    ])


def adjudicate(partition: str, max_calls: int,
               offset: int) -> Dict[str, Any]:
    """Third-model adjudication of judge_a vs judge_b disagreements."""
    def _load_ledger(j: str) -> Dict[str, Dict]:
        p = os.path.join(common.OUT,
                         f'judge_{j}_{partition}_pair.jsonl')
        out: Dict[str, Dict] = {}
        if os.path.exists(p):
            for l in open(p):
                r = json.loads(l)
                out[r['item_id']] = r
        return out
    a, b = _load_ledger('judge_a'), _load_ledger('judge_b')
    common_ids = [i for i in a if i in b and a[i].get('judgment')
                  and b[i].get('judgment')]
    items = {json.loads(l)['item_id']: json.loads(l)
             for l in open(os.path.join(common.OUT,
                                        f'items_{partition}.jsonl'))}
    # disagreements = union verdicts differ
    import reference_build as RB
    disc = []
    for i in common_ids:
        ja, jb = a[i]['judgment'], b[i]['judgment']
        na = RB._norm_pair_judgment(ja)
        nb = RB._norm_pair_judgment(jb)
        # disagreement on the NORMALIZED union verdict (the canonical form,
        # per the protocol's 'normalize before comparing' rule)
        ua = na['union'] if na else None
        ub = nb['union'] if nb else None
        if ua is not None and ub is not None and ua != ub:
            disc.append(i)
    out_p = os.path.join(common.OUT,
                         f'judge_adjudicated_{partition}.jsonl')
    os.makedirs(os.path.dirname(out_p), exist_ok=True)
    done = 0
    for i in disc[offset: offset + max_calls]:
        cp = os.path.join(common.OUT, 'judge_cache', 'adjudicator',
                          f'{i}__pair.json')
        if os.path.exists(cp):
            done += 1
            continue
        prompt = _adjudicate_prompt(items.get(i, {}),
                                    a[i]['judgment'], b[i]['judgment'])
        res = _call(JUDGES['adjudicator']['model'],
                    'You are a precise measurement adjudicator. '
                    'Resolve using EVIDENCE only. JSON output only.',
                    prompt)
        parsed = _parse_json(res.get('text'))
        rec = {'item_id': i, 'judge': 'adjudicator',
               'model': JUDGES['adjudicator']['model'],
               'prompt_sha256':
                   hashlib.sha256(prompt.encode()).hexdigest(),
               'ok': res.get('ok'), 'valid_json': parsed is not None,
               'judgment': parsed, 'ts': res.get('ts')}
        json.dump(rec, open(cp, 'w'), indent=1)
        with open(out_p, 'a') as fh:
            fh.write(json.dumps(rec, default=str) + '\n')
        done += 1
    return {'n_disagreements': len(disc), 'n_adjudicated_this_run': done,
            'model': JUDGES['adjudicator']['model']}


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--judge', default='judge_a')
    ap.add_argument('--task', default='extraction',
                    choices=['extraction', 'pair', 'adjudicate'])
    ap.add_argument('--partition', default='dev')
    ap.add_argument('--max-calls', type=int, default=20)
    ap.add_argument('--offset', type=int, default=0)
    a = ap.parse_args()
    if a.task == 'adjudicate':
        print(json.dumps(adjudicate(a.partition, a.max_calls, a.offset),
                         indent=1))
    else:
        runner = (build_extraction_prompt if a.task == 'extraction'
                   else build_pair_prompt)
        run(a.judge, a.task, a.partition, a.max_calls, a.offset, runner)
