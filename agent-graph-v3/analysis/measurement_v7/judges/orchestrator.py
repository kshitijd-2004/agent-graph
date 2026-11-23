#!/usr/bin/env python3.11
"""
Judge orchestration: build blinded jobs from the existing annotation packets,
run a configured judge with a hard budget guard, validate outputs against the
annotation schemas, and write provenance-complete annotation records.

NON-DESTRUCTIVE: reads the blinded packets under
``analysis/measurement_validation/annotation_templates/`` and writes only
under ``analysis/measurement_v7/out/``.

BUDGET GUARD: a maximum number of live calls and a maximum USD cost are
enforced per run (``--max-calls``, ``--max-cost-usd``).  Successful responses
are cached, so re-running after a budget stop resumes without re-issuing
completed calls.  A budget stop is a recorded state, not an error.

BLINDING VERIFICATION: before any call, the orchestrator re-checks that no
packet item exposes a LEP label, a benign/perturbed identity, a pipeline
prediction, or an expected answer (the same forbidden-token scan the
validation framework uses, extended).  A blinding failure aborts the run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
VALID_TEMPLATES = os.path.join(REPO, 'analysis', 'measurement_validation',
                               'annotation_templates')
OUT = os.path.join(REPO, 'analysis', 'measurement_v7', 'out')

sys.path.insert(0, HERE)
from providers import default_judges, availability_report     # noqa: E402
from prompts import (extraction_prompt, pair_prompt, input_sha,  # noqa: E402
                     PROMPT_VERSION)
from schemas import (parse_json_completion, validate_extraction,  # noqa: E402
                     validate_pair, extraction_expected_fields,
                     JudgeOutputError)
from runner import JudgeClient                                  # noqa: E402

# The pre-call blinding scan reuses the PACKET BUILDER's single source of
# truth (the framework's own test list + structural identifiers).  A second,
# hand-written regex here would drift and false-positive on agent-authored
# content (e.g. the variable ``ipv6_re`` in SWE-bench code) — so there is
# exactly one blinding standard, shared by build and pre-call checks.
import packet_builder as _PB                                  # noqa: E402

_FORBIDDEN_TEST = _PB._FORBIDDEN_TEST
_FORBIDDEN_IDS = _PB._FORBIDDEN_IDS


def _scan_forbidden(text: str) -> Dict[str, int]:
    low = text.lower()
    hits: Dict[str, int] = {}
    for s in _FORBIDDEN_TEST:
        if s.lower() in low:
            hits[s] = hits.get(s, 0) + 1
    for s in _FORBIDDEN_IDS:
        if s in text:
            hits[s] = hits.get(s, 0) + 1
    return hits


def _load_jsonl(path: str) -> List[Dict[str, Any]]:
    return [json.loads(l) for l in open(path) if l.strip()]


def verify_blinding(packets: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Abort-safe blinding check over every packet item the judge would see.

    The rebuilt packets already carry NO tool_result / user_input /
    system_init content and are scrubbed at build time, so this pre-call
    scan is a defence-in-depth layer: if a packet shipped with a forbidden
    token, the run aborts before any API call is made.
    """
    hits: Dict[str, int] = {}
    for p in packets:
        for tok, c in _scan_forbidden(json.dumps(p, ensure_ascii=False)).items():
            hits[tok] = hits.get(tok, 0) + c
    return {'n_items': len(packets), 'forbidden_hits': hits,
            'blinding_ok': not hits}


def build_extraction_jobs(judge_name: str,
                          judge_cfg, packets: List[Dict[str, Any]]
                          ) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for p in packets:
        prompt = extraction_prompt(p, judge_name)
        out.append({'prompt': prompt,
                    'item_token': p.get('item_token'),
                    'input_hash': input_sha(p, judge_name),
                    'kind': 'extraction',
                    'expected_fields': extraction_expected_fields(
                        p.get('annotation_task') or {})})
    return out


def build_pair_jobs(judge_name: str, judge_cfg, packets: List[Dict[str, Any]]
                    ) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for p in packets:
        prompt = pair_prompt(p, judge_name)
        out.append({'prompt': prompt,
                    'item_token': p.get('pair_token'),
                    'input_hash': input_sha(p, judge_name),
                    'kind': 'pair',
                    'n_members': len(p.get('members') or [])})
    return out


def _validate(rec: Dict[str, Any], job: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a judge completion against the annotation schema.  A failure
    is a judge failure (recorded), never a coerced label."""
    v: Dict[str, Any] = {'item_token': job['item_token'],
                         'kind': job['kind']}
    if rec['status'] != 'ok':
        v.update({'valid': False, 'reason': f'judge {rec["status"]}'})
        return v
    try:
        parsed = parse_json_completion(rec.get('text', ''))
    except JudgeOutputError as e:
        v.update({'valid': False, 'reason': f'schema: {e}'})
        return v
    v['parsed'] = parsed
    if job['kind'] == 'extraction':
        labels = parsed.get('labels') or {}
        v['labels'] = labels
        r = validate_extraction(labels, job['expected_fields'])
        v.update({'valid': r['ok'], 'problems': r['problems']})
    else:
        r = validate_pair(parsed, job['n_members'])
        v.update({'valid': r['ok'], 'problems': r['problems'],
                  'pair_record': parsed})
    return v


def run(judge_name: str, tasks: str = 'extraction', limit: Optional[int] =
        None, max_calls: int = 40, max_cost_usd: float = 2.0,
        out_prefix: Optional[str] = None, smoke: bool = False,
        offset: int = 0, corrective: bool = True
        ) -> Dict[str, Any]:
    os.makedirs(OUT, exist_ok=True)
    judges = default_judges()
    if judge_name not in judges:
        raise SystemExit(f'unknown judge {judge_name}; '
                         f'available: {list(judges)}')
    cfg = judges[judge_name]
    avail = availability_report()
    cfg_path = os.path.join(OUT, 'judge_config_report.json')
    with open(cfg_path, 'w') as f:
        json.dump({'availability': avail, 'judge_config':
                   {k: v.to_json() for k, v in judges.items()}},
                  f, indent=2, sort_keys=True)

    # load the packets for the requested task.  The judge packets are the
    # REBUILT excerpts under out/judge_packets/ (template tokens + task
    # blocks, complete authored evidence base — see packet_builder.py).
    # Fall back to the template only if the rebuilt packets are missing.
    jpk = os.path.join(OUT, 'judge_packets')
    ext_src = os.path.join(jpk, 'extraction_packet_judge.jsonl')
    pair_src = os.path.join(jpk, 'pair_packet_judge.jsonl')
    if 'extraction' in tasks:
        ext = _load_jsonl(ext_src if os.path.exists(ext_src)
                          else os.path.join(
                              VALID_TEMPLATES,
                              'extraction_packet_annotator_A.jsonl'))
    else:
        ext = []
    if 'pair' in tasks:
        pr = _load_jsonl(pair_src if os.path.exists(pair_src)
                         else os.path.join(
                             VALID_TEMPLATES,
                             'pair_packet_annotator_A.jsonl'))
    else:
        pr = []
    all_pkts = ext + pr
    bl = verify_blinding(all_pkts)
    bl_path = os.path.join(OUT, 'judge_blinding_check.json')
    json.dump(bl, open(bl_path, 'w'), indent=2, sort_keys=True)
    if not bl['blinding_ok']:
        raise SystemExit(
            f'BLINDING FAILED: forbidden tokens {bl["forbidden_hits"]} '
            f'reached the packet.  Aborting — no call is made.  '
            f'{bl_path}')

    jobs: List[Dict[str, Any]] = []
    if ext:
        jobs += build_extraction_jobs(judge_name, cfg, ext)
    if pr:
        jobs += build_pair_jobs(judge_name, cfg, pr)
    if limit:
        jobs = jobs[:limit]

    # split into already-cached vs needing a live call
    client = JudgeClient(cfg)
    needs_call: List[Dict[str, Any]] = []
    for j in jobs:
        key = client._cache_key(j['prompt'], judge_name,
                                 j['item_token'] or '')
        if client._load_cache(key) is None:
            needs_call.append(j)

    # budget guard over the calls that would actually be issued
    budget = {'jobs_total': len(jobs), 'needs_live_call': len(needs_call),
              'cached': len(jobs) - len(needs_call),
              'max_calls': max_calls, 'max_cost_usd': max_cost_usd,
              'runnable': cfg.is_configured(), 'smoke': smoke}
    budget_path = os.path.join(OUT, f'{out_prefix or judge_name}_budget.json')
    if not cfg.is_configured():
        budget['note'] = ('judge is UNCONFIGURED (no credential/endpoint). '
                          'No live call will be issued; this is a recorded '
                          'configuration requirement, not a silent '
                          'substitution.')
        json.dump(budget, open(budget_path, 'w'), indent=2, sort_keys=True)
        # still validate whatever is cached (synthetic or prior)
        records = _validate_all(client, judge_name, jobs)
        _write_out(records, judge_name, out_prefix)
        _write_report(records, judge_name, out_prefix, budget, avail)
        return budget

    if smoke:
        needs_call = needs_call[:3]     # smoke: at most 3 live calls
    elif offset:
        # chunked foreground execution: skip already-cached items, then take
        # the next ``max_calls`` uncached items starting at ``offset`` within
        # the uncached list.  The judgments file is always written over the
        # FULL job set (cache-backed), so chunks resume cleanly.
        needs_call = needs_call[offset:offset + max_calls]
    if len(needs_call) > max_calls and not smoke:
        needs_call = needs_call[:max_calls]
        budget['note'] = f'budget cap: {max_calls} live calls this run'
    json.dump(budget, open(budget_path, 'w'), indent=2, sort_keys=True)

    # run the live calls
    live = [j for j in needs_call]
    total_cost = 0.0
    for j in live:
        rec = client.annotate(j['prompt'], judge_name, j['item_token'] or '',
                              j['input_hash'])
        total_cost += float(rec.get('usage', {}).get('cost_usd') or 0)
        if total_cost > max_cost_usd:
            budget['note'] = f'stopped at cost cap ${max_cost_usd}'
            break
    records = _validate_all(client, judge_name, jobs)
    # ONE corrective retry (prompt version '-corrective'): a completion that
    # is not JSON, or that states a value without a usable evidence span,
    # is re-issued once with an explicit correction instruction.  The v2
    # prompt has its own cache key (prompt_version + hash), so the bad v1
    # completion stays in the cache for audit and the corrective call is a
    # distinct, provenance-recorded job.  A second failure is a recorded
    # judge failure — never a coerced label.
    by_tok = {r['item_token']: r for r in records}
    for j in jobs:
        r = by_tok.get(j['item_token'] or '')
        failed = (r and not r.get('valid')
                  and (r.get('reason', '').startswith('schema')
                       or r.get('problems')))
        if not failed:
            continue
        p2 = _corrective_prompt(j['prompt'], r, j['kind'])
        client.annotate(p2, judge_name, j['item_token'] or '', j['input_hash'])
    records = _validate_all_v2(client, judge_name, jobs)
    _write_out(records, judge_name, out_prefix)
    _write_report(records, judge_name, out_prefix, budget, avail)
    return budget


def _corrective_prompt(prompt: Dict[str, str],
                       failed: Dict[str, Any],
                       kind: str) -> Dict[str, str]:
    """A second-chance prompt: same content, plus an explicit correction.

    Recorded under a DIFFERENT prompt version ('-corrective') so the two
    attempts are separately cached, separately hashed, and separately
    attributable.  The correction tells the model exactly what failed;
    it does not relax the schema.
    """
    import hashlib as _hl
    probs = (failed.get('problems') or []) or [
        failed.get('reason') or 'the completion failed validation']
    user = (prompt['user']
            + '\n\nCORRECTION REQUIRED: your previous response was '
            + 'rejected. Problems: ' + '; '.join(probs)
            + ' Re-answer. Output ONLY the strict JSON object described '
            + 'in the system prompt — no surrounding prose. Every '
            + '"observed" or "ambiguous" label MUST carry an evidence span '
            + '(a verbatim quote of <=200 characters from the record, with '
            + 'its event index). If the record genuinely does not let you '
            + 'decide, use the state "unavailable" or "ambiguous" with a '
            + 'note — do not guess.')
    blob = json.dumps({'version': prompt['version'] + '-corrective',
                       'user': user,
                       'item_token': prompt.get('prompt_sha256', '')[:16]},
                      sort_keys=True)
    return {'system': prompt['system'], 'user': user,
            'version': prompt['version'] + '-corrective',
            'prompt_sha256': _hl.sha256(blob.encode()).hexdigest()}


def _validate_all(client: JudgeClient, judge_name: str,
                  jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Validate the first-attempt (v1 prompt) completions."""
    out = []
    for j in jobs:
        key = client._cache_key(j['prompt'], judge_name,
                                j['item_token'] or '')
        rec = client._load_cache(key)
        if rec is None:
            rec = {'status': 'not_run', 'item_token': j['item_token']}
        v = _validate(rec, j)
        v['judge'] = judge_name
        v['model'] = client.cfg.model
        v['prompt_version'] = j['prompt'].get('version', PROMPT_VERSION)
        v['attempt'] = 'v1'
        out.append(v)
    return out


def _validate_all_v2(client: JudgeClient, judge_name: str,
                     jobs: List[Dict[str, Any]]
                     ) -> List[Dict[str, Any]]:
    """Validate with corrective-prompt (v2) completions taking precedence.

    For each job: if a v2 cache entry exists, it is the operative attempt
    and is validated; a valid v1 entry is the fallback.  Every record
    carries which attempt produced it, so the final judgment file is fully
    attributable.
    """
    out = []
    for j in jobs:
        key1 = client._cache_key(j['prompt'], judge_name,
                                 j['item_token'] or '')
        rec1 = client._load_cache(key1)
        v1 = _validate(rec1 or {'status': 'not_run',
                                'item_token': j['item_token']}, j)
        v1['judge'] = judge_name
        v1['model'] = client.cfg.model
        v1['prompt_version'] = j['prompt'].get('version', PROMPT_VERSION)
        v1['attempt'] = 'v1'
        if not v1.get('valid'):
            p2 = _corrective_prompt(j['prompt'], v1, j['kind'])
            key2 = client._cache_key(p2, judge_name,
                                     j['item_token'] or '')
            rec2 = client._load_cache(key2)
            if rec2 is not None:
                v2 = _validate(rec2, j)
                v2['judge'] = judge_name
                v2['model'] = client.cfg.model
                v2['prompt_version'] = p2['version']
                v2['attempt'] = 'v2_corrective'
                v2['v1_problems'] = (v1.get('problems')
                                     or [v1.get('reason')])
                if v2.get('valid'):
                    out.append(v2)
                    continue
                v2['v1'] = v1
                out.append(v2)
                continue
        out.append(v1)
    return out


def _write_out(records: List[Dict[str, Any]], judge_name: str,
               out_prefix: Optional[str]) -> None:
    base = out_prefix or judge_name
    p = os.path.join(OUT, f'{base}_judgments.jsonl')
    with open(p, 'w') as f:
        for r in records:
            f.write(json.dumps(r, sort_keys=True) + '\n')


def _write_report(records, judge_name, out_prefix, budget, avail):
    base = out_prefix or judge_name
    n = len(records)
    ok = sum(1 for r in records if r.get('valid'))
    fail = sum(1 for r in records if r.get('reason', '').startswith(
        'judge') and r['reason'] != 'judge ok')
    rep = {
        'judge': judge_name,
        'model': records[0].get('model') if records else None,
        'n_items': n,
        'n_valid': ok,
        'n_invalid_or_failed': n - ok,
        'configured': budget.get('runnable'),
        'budget': budget,
        'note': ('These are AI-judge annotations, NOT human labels.  A '
                 'single-family run (Judge B unconfigured) cannot support a '
                 'two-family independent-agreement claim; it is a mechanism '
                 'validation only.'),
    }
    json.dump(rep, open(os.path.join(OUT, f'{base}_report.json'), 'w'),
              indent=2, sort_keys=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--judge', default='judge_a', choices=['judge_a',
                                                           'judge_b'])
    ap.add_argument('--tasks', default='extraction,pair')
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--max-calls', type=int, default=40)
    ap.add_argument('--max-cost-usd', type=float, default=2.0)
    ap.add_argument('--smoke', action='store_true',
                    help='at most 3 live calls; mechanism validation only')
    ap.add_argument('--prefix')
    ap.add_argument('--offset', type=int, default=0,
                    help='chunked foreground execution: resume point in the '
                         'uncached list (cache makes this idempotent)')
    a = ap.parse_args()
    tasks = [t for t in a.tasks.split(',') if t]
    run(a.judge, ','.join(tasks), a.limit, a.max_calls, a.max_cost_usd,
        a.prefix, smoke=a.smoke, offset=a.offset)


if __name__ == '__main__':
    main()
