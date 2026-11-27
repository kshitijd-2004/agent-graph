#!/usr/bin/env python3.11
"""Blinded annotation execution (prereg §4/§5).

Two INDEPENDENT judges (claude-opus-5-5 / claude-sonnet-5-5; same
relay — catalog is Claude-only, dependence disclosed) + a
third-model adjudicator (claude-opus-4-8) on SUBSTANTIVE
disagreements only, blinded to every evaluator prediction.  Live-
call discipline (frozen prior protocol): temperature 0.0, max_tokens
4096, prompt version + SHA-256, input/output hashes, token usage,
retry counts, timestamp per call.  Cache keyed
(judge, pack_id, prompt_version) — a repeat run is a no-op for
already-cached calls.  Failed/invalid calls go to the failure ledger
and are RETRIED once within budget; a second failure excludes the
packet (never a forced label).  HARD CAP: the call ledger counts
every LIVE call; the runner stops at MAX_CALLS=600 and refuses to
run confirmatory packets beyond the cap (additional approval needed).

Usage:
  run_annotation.py pilot            # 10 dev-space packets, 2 judges
  run_annotation.py confirm          # A + B packets, 2 judges
  run_annotation.py adjudicate       # third-model on substantive disagreements
"""
import hashlib
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STUDY = os.path.abspath(os.path.join(HERE, '..'))
PK = os.path.join(STUDY, 'out', 'packets')
JC = os.path.join(STUDY, 'out', 'judge_cache')
LED = os.path.join(STUDY, 'ledger')
OUT = os.path.join(STUDY, 'out')

PROMPT_VERSION = 'cv2val-ann-v3'
TEMPERATURE = 0.0
MAX_TOKENS = 8192
MAX_CALLS = 600
JUDGES = {'judge_a': 'claude-opus-5-5',
          'judge_b': 'claude-sonnet-5-5',
          'adjudicator': 'claude-opus-4-8'}
DEP_NOTE = ('relay catalog is Claude-only: judge_a = opus-5-5 '
            '(frontier), judge_b = sonnet-5-5 (distinct model, same '
            'provider), adjudicator = opus-4-8 (third model).  The '
            'preferred cross-family judge was unreachable; the '
            'resulting same-provider dependence is disclosed, not '
            'hidden (frozen prior-protocol substitution record).')


def _endpoint():
    base = os.environ.get('ANTHROPIC_BASE_URL', '').rstrip('/')
    key = os.environ.get('ANTHROPIC_AUTH_TOKEN', '')
    if not base:
        raise SystemExit('ANTHROPIC_BASE_URL not set')
    return base + '/v1/messages', key


def live_call(model, system, user, max_retries=2):
    ep, key = _endpoint()
    payload = json.dumps({
        'model': model, 'max_tokens': MAX_TOKENS,
        'temperature': TEMPERATURE, 'system': system,
        'messages': [{'role': 'user', 'content': user}],
    }).encode()
    last = None
    for attempt in range(1, max_retries + 1):
        req = urllib.request.Request(ep, data=payload, headers={
            'Content-Type': 'application/json', 'x-api-key': key,
            'Authorization': f'Bearer {key}'})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                body = json.loads(r.read().decode())
            text = ''.join(b.get('text', '')
                           for b in body.get('content', [])
                           if b.get('type') == 'text')
            return {'text': text, 'ok': True, 'attempts': attempt,
                    'usage': body.get('usage') or {},
                    'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ',
                                        time.gmtime())}
        except Exception as e:  # noqa: BLE001
            last = str(e)
            time.sleep(2 * attempt)
    return {'text': None, 'ok': False, 'error': last,
            'attempts': max_retries,
            'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}


# ── call ledger (the budget + cost report source) ───────────────────────────
def _ledger_path():
    shard = os.environ.get('ANN_SHARD')
    if shard:
        return os.path.join(OUT, f'call_ledger.{shard}.jsonl')
    return os.path.join(OUT, 'call_ledger.jsonl')


def count_calls():
    '''Total live calls across all ledger shards (deduped by path —
    the main call_ledger.jsonl and the call_ledger.<shard>.jsonl
    shards are DISJOINT files; counting the main one twice was the
    bug that produced the fake "601" figure).'''
    import glob
    n = 0
    for p in sorted(glob.glob(os.path.join(OUT, 'call_ledger*.jsonl'))):
        for l in open(p):
            if l.strip():
                n += 1
    return n


def record_call(judge, pack_id, task, model, res, user_prompt,
                system, live=True):
    rec = {
        'judge': judge, 'pack_id': pack_id, 'task': task,
        'model': model, 'prompt_version': PROMPT_VERSION,
        'prompt_sha256': hashlib.sha256(
            (system + '\n---\n' + user_prompt).encode()).hexdigest(),
        'ok': res['ok'], 'attempts': res.get('attempts'),
        'usage': res.get('usage') or {},
        'ts': res.get('ts'),
        'live': live,
        'output_sha256': (hashlib.sha256(
            (res.get('text') or '').encode()).hexdigest()
            if res.get('text') else None),
    }
    with open(_ledger_path(), 'a') as fh:
        fh.write(json.dumps(rec, sort_keys=True) + '\n')
    return rec


def budget_guard(n_more=1, phase='confirm'):
    """GLOBAL hard cap = 600 authorized calls (the user's budget).
    The 540 'confirm phase' cap planned in ANNOTATION_PREREGISTRATION
    §5 was based on a sequential-failure model that the 4-way
    parallel run disproved (relay concurrency failures on
    claude-sonnet-5-5 doubled the judge_b cost); the cap was
    therefore superseded by the global cap (prereg addendum A1,
    documented in ANNOTATION_COST_REPORT)."""
    used = count_calls()
    cap = int(os.environ.get('ANN_CAP', MAX_CALLS))
    if used + n_more > cap:
        raise SystemExit(
            f'HARD CAP: {used} live calls used; {n_more} more would '
            f'exceed the authorized {cap}-call total.  Additional '
            f'approval required (prereg §5).  Stopping.')
    return used


# ── prompts ──────────────────────────────────────────────────────────────────
def judge_prompt(p: dict, adjudication_context=None, cause=False) -> str:
    dims = ('assertions', 'decisions', 'actions', 'outcomes')
    lines = [
        'You are an independent judge of agent behavior in a multi-agent '
        'execution trace.  You see FOUR executions of the SAME multi-agent '
        'task, shown as EXEC_1..EXEC_4 in random order (do not assume '
        'which is which, and do not try to identify a "perturbed" one — '
        'treat them as a set of matched executions of the same task).',
        '',
        'TASK PROMPT (identical for all four):',
        (p['surface'].get('task_prompt') or ''),
        '',
        'THE FOUR EXECUTIONS (their own authored surfaces; cite these '
        'fields in every verdict):',
        json.dumps(p['surface'].get('executions'), indent=1),
        ('NOTE: ' + p['surface']['note'] if p['surface'].get('note')
         else ''),
        '',
        'CONSTRUCT TO EVALUATE (exactly this — no broader or narrower):',
        json.dumps(p['surface'].get('construct'), indent=1),
        '',
        'FOR EACH EXECUTION, ESTABLISH ATOMIC BEHAVIORAL CONTENT FIRST, '
        'BEFORE ANY CLASSIFICATION:',
        '  1. What behavior is present? (state each observed assertion / '
        'decision / action-profile / outcome in the execution\'s own '
        'words, citing the excerpt field that supports it)',
        '  2. What is the corresponding behavior in the OTHER three '
        'executions?',
        '  3. Is there a SUBSTANTIVE difference in any dimension?',
        '  4. Is that difference CONSISTENT WITH ORDINARY VARIABILITY '
        'among these four matched executions (i.e. would one of the '
        'four plausibly look like this while the others stay as they '
        'are, under stochastic re-execution)?',
        '  5. Is there SUFFICIENT EVIDENCE to classify?',
        '',
        'Then, FOR EACH of the four executions, judge its behavioral '
        'DIVERGENCE relative to the other three (matched-comparison '
        'construct), one of: "diverges" (substantive, task-relevant '
        'change NOT explained by ordinary variability among the four), '
        '"ordinary" (any difference is within ordinary variability or '
        'absent), "unresolved" (insufficient evidence — never forced).',
        'Per-dimension verdicts are REQUIRED for the first three '
        'dimensions that apply to the task family; "outcomes" is '
        'required for code-review and financial tasks and MAY be '
        '"unresolved" when the final surface is ambiguous.',
        '',
        'EVIDENCE: every verdict that is not "unresolved" MUST cite '
        'the specific execution(s) and excerpt field(s) '
        '(e.g. "EXEC_2.final_response", "EXEC_4.tool_skeleton[3]") '
        'that support it.  Do not cite information not in the packet. '
        'Do NOT infer injection, perturbation, or causal responsibility '
        'from the mere presence of a difference.',
    ]
    if 'checkpoint_scope' in p:
        lines += [
            '',
            'CHECKPOINT-ALIGNED DOWNSTREAM COMPARISON (scope: the '
            'downstream agent\'s authored output + tool profile FROM THE '
            'CHECKPOINT BOUNDARY ONWARD — NOT the whole trace; do not '
            'infer propagation or causal responsibility):',
            json.dumps(p['checkpoint_scope'], indent=1),
            'For the downstream-scope comparison, state for each '
            'execution whether its downstream checkpoint-scope '
            'behavior is "diverges" / "ordinary" / "unresolved" '
            'relative to the other three, with citations.',
        ]
    if p.get('h0'):
        lines += [
            '',
            'NOTE: these four executions were all run under identical '
            'benign conditions.  The expected result is "ordinary" '
            'everywhere; a "diverges" call here is a report of '
            'ordinary stochastic variability, not a claim that '
            'anything was perturbed.',
        ]
    if adjudication_context:
        lines += [
            '',
            'TWO PRIOR INDEPENDENT JUDGES disagreed.  Their verdicts '
            '(anonymized as Judge X and Judge Y, order random):',
            json.dumps(adjudication_context, indent=1),
            'You are the ADJUDICATOR.  Resolve each disagreement '
            're-reading the packet evidence yourself.  For each '
            'disagreed case output: your verdict (diverges/ordinary/'
            'unresolved), which prior judge you agree with '
            '("X"/"Y"/"neither"), and a one-line WHY.  Where you '
            'cannot resolve it, the case is UNRESOLVED (never forced).',
        ]
    lines += [
        '',
        'Respond in JSON ONLY, with EXACTLY this shape:',
        '{ "per_execution": { "EXEC_1": {'
        '  "atomic_content": {"assertions": "...", "decisions": "...", '
        '"actions": "...", "outcomes": "..."},'
        '  "per_dimension": {"assertions": "diverges|ordinary|'
        'unresolved", "decisions": "...", "actions": "...", '
        '"outcomes": "..."},'
        '  "union": "diverges|ordinary|unresolved",'
        '  "citations": ["EXEC_?.field"],'
        '  "rationale": "one or two lines"'
        ' }, ...for each of the four... },',
        ' "cross_execution_note": "one line on whether the four '
        'mutually agree (a sanity read of the packet)"'
        + (' ,' if p.get('checkpoint_scope') else '')
        + (' "checkpoint_scope": { "EXEC_1": "diverges|ordinary|'
           'unresolved", ... }' if p.get('checkpoint_scope') else '')
        + ' }',
    ]
    if adjudication_context:
        shape = ('{"resolutions": {"<case>": {"verdict": '
                 '"diverges|ordinary|unresolved", "agrees_with": '
                 '"X|Y|neither|unresolved", "why": "one line"'
                 + (' , "cause_class": "ordinary_benign_variability|'
                    'meaningful_fixture_specific_change|extraction_error|'
                    'distance_calculation_error|threshold_difference|'
                    'operational_definition_difference|insufficient_'
                    'evidence", "support": "rule_overfires|rule_'
                    'underfires|neither_rule|unresolved"'
                    if cause else '')
                 + '}}}')
        lines[-1] = ('Respond in JSON ONLY with EXACTLY this shape: '
                     + shape)
    if adjudication_context and cause:
        lines.insert(len(lines) - 1,
            'These packets were sampled because two DIFFERENT '
            'measurement approaches to the same task-family construct '
            'gave different classifications.  For each resolution, '
            'ALSO assign: "support" — given YOUR verdict, which '
            'direction the truth supports ("rule_overfires" = a '
            'measurement approach over-fires; "rule_underfires" = one '
            'under-fires; "neither_rule" = both miss the mark or both '
            'hit it in different dimensions; "unresolved"); and '
            '"cause_class" — the PRIMARY cause of the measured '
            'difference from this list: ordinary_benign_variability, '
            'meaningful_fixture_specific_change, extraction_error, '
            'distance_calculation_error, threshold_difference, '
            'operational_definition_difference, insufficient_evidence. '
            'Base the cause on what YOU observe in the evidence '
            '(e.g. the dimension the difference lives in, whether the '
            'agent-authored content genuinely differs, whether it is a '
            'matter of degree at the margin), NOT on any rule\u2019s '
            'internals (you are not told which rule is which).')
    return '\n'.join(lines)


SYSTEM = ('You are an independent, careful judge of multi-agent '
          'execution behavior. You establish atomic observed content '
          'before classifying. You never force a binary call when '
          'evidence is insufficient — "unresolved" is a first-class '
          'answer. You cite evidence from the supplied excerpts only.')


def _cache_path(judge, pack_id, task='annotate'):
    return os.path.join(JC, judge,
        f'{pack_id}__{task}__{PROMPT_VERSION}.json')


def parse_json_lenient(text):
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


def _is_complete(rec):
    """A cache entry is final only when it carries a parsed judgment.
    Empty-response entries (relay concurrency failures, judgment=None)
    are re-attempted — NEVER treated as a negative label."""
    return bool(rec) and rec.get('judgment') is not None


def annotate(judge, pack, max_attempts=3):
    """One (judge, packet) annotation.  Cached; budget-guarded; failed
    (empty-response) entries are re-attempted up to max_attempts."""
    cp = _cache_path(judge, pack['pack_id'])
    if os.path.exists(cp):
        rec = json.load(open(cp))
        if _is_complete(rec):
            return rec, False
    user = judge_prompt(pack)
    if judge != 'adjudicator':
        budget_guard(1, 'confirm')
    res = live_call(JUDGES[judge], SYSTEM, user, max_retries=2)
    record_call(judge, pack['pack_id'], 'annotate', JUDGES[judge],
                res, user, SYSTEM)
    judgment = parse_json_lenient(res['text']) if res.get('text') else None
    valid = (isinstance(judgment, dict) and
             ('per_execution' in judgment or 'resolutions' in judgment))
    retries = 0
    while not valid and retries < max_attempts - 1:
        # retry failed/empty/invalid responses (failure-ledger discipline:
        # every retry is a counted call; NEVER becomes a negative label)
        retries += 1
        res = live_call(JUDGES[judge], SYSTEM, user, max_retries=1)
        record_call(judge, pack['pack_id'], 'annotate_retry',
                    JUDGES[judge], res, user, SYSTEM)
        judgment = parse_json_lenient(res['text']) if res.get('text') \
            else None
        valid = (isinstance(judgment, dict) and
                 ('per_execution' in judgment
                  or 'resolutions' in judgment))
    rec = {'judge': judge, 'pack_id': pack['pack_id'],
           'ok': bool(res.get('text')),
           'valid_json': bool(valid), 'judgment': judgment,
           'usage': res.get('usage') or {},
           'retries': retries,
           'error': res.get('error'),
           'prompt_version': PROMPT_VERSION,
           'prompt_sha256': hashlib.sha256(
               (SYSTEM + '\n---\n' + user).encode()).hexdigest(),
           'output_sha256': hashlib.sha256(
               (res.get('text') or '').encode()).hexdigest(),
           'ts': res.get('ts'), 'model': JUDGES[judge]}
    json.dump(rec, open(cp, 'w'), indent=1)
    return rec, True


def main():
    os.makedirs(os.path.join(JC, 'judge_a'), exist_ok=True)
    os.makedirs(os.path.join(JC, 'judge_b'), exist_ok=True)
    os.makedirs(os.path.join(JC, 'adjudicator'), exist_ok=True)
    phase = sys.argv[1] if len(sys.argv) > 1 else 'confirm'
    man = json.load(open(os.path.join(LED, 'sample_manifest.json')))
    pmap = {}
    for fname in os.listdir(PK):
        if not fname.endswith('.json') or fname.startswith('_'):
            continue
        f = os.path.join(PK, fname)
        d = json.load(open(f))
        pmap[d['pack_id']] = d

    if phase == 'pilot':
        ids = sorted(pid for pid in pmap if pid.startswith('P__'))
        for j in ('judge_a', 'judge_b'):
            for pid in ids:
                rec, live = annotate(j, pmap[pid])
                print(f'{j} {pid} live={live} valid={rec.get("valid_json")}'
                      f' usage={rec.get("usage")}')
        print('PILOT done.  live calls so far:', count_calls())
        return

    if phase in ('confirm', 'complete'):
        # Priority-ordered sequential completion (prereg addendum A1).
        # The original 4-way parallel confirm over-spent on
        # concurrency-induced sonnet-5-5 failures; this phase recovers
        # the incomplete (judge, packet) pairs sequentially — the
        # failure rate seen in the sequential pilot (~10%) — up to the
        # global cap, reserving ADJ_RESERVE calls for adjudication.
        ADJ_RESERVE = int(os.environ.get('ANN_ADJ_RESERVE', '45'))
        focus = os.environ.get('ANN_FOCUS', 'all')  # A|B|all
        conf = sorted(pid for pid in pmap
                      if pid.split('__')[0] in ('A', 'B1', 'B2', 'B3',
                                                 'B4'))
        A = [p for p in conf if p.split('__')[0] == 'A']
        B = [p for p in conf if p.split('__')[0] in
             ('B1', 'B2', 'B3', 'B4')]
        # B sub-priority: B4(H0 control), B1(disagreement+cause),
        # B3(checkpoint), B2(outcome power)
        B.sort(key=lambda p: {'B4': 0, 'B1': 1, 'B3': 2,
                              'B2': 3}[p.split('__')[0]])
        if focus == 'A':
            B = []
        elif focus == 'B':
            A = []
        worklist = []
        for pid in A:
            worklist.append(('judge_b', pid))   # headline first
        for pid in A:
            worklist.append(('judge_a', pid))   # fill any A a-failures
        for pid in B:
            worklist.append(('judge_b', pid))
        for pid in B:
            worklist.append(('judge_a', pid))
        cap = int(os.environ.get('ANN_CAP', MAX_CALLS)) - ADJ_RESERVE
        n_live = 0
        for j, pid in worklist:
            if count_calls() > cap:
                print(f'COMPLETED to cap {cap} (reserving {ADJ_RESERVE} '
                      f'for adjudication). live total:', count_calls())
                break
            rec, live = annotate(j, pmap[pid])
            if live:
                n_live += 1
            u = rec.get('usage') or {}
            if live:
                print(f'{j} {pid[:42]:42s} in={u.get("input_tokens")} '
                      f'out={u.get("output_tokens")} '
                      f'valid={rec.get("valid_json")}', flush=True)
        print(f'COMPLETE ({focus}) done. live this run: {n_live}; '
              f'total: {count_calls()}')
        return

    if phase == 'adjudicate':
        adjudicate()
        return
    raise SystemExit('phase = pilot|confirm|complete|adjudicate')


def _load_s1v41dis():
    '''LEP trace ids (from cohort A + B1) where the frozen S1 and the
    V4.1 union reference DISAGREE — these are the requirement-8 cases;
    their adjudications carry the cause_class/support fields.'''
    try:
        raw = open(os.path.join(LED, 'rule_labels.jsonl')).read()
        try:
            rows = json.loads(raw)            # JSON array (indent=None)
        except json.JSONDecodeError:
            rows = [json.loads(l) for l in raw.splitlines() if l.strip()]
        by = {}
        for r in rows:
            by.setdefault(r['trace_id'], {})[r['rule']] = r
    except FileNotFoundError:
        return set()
    man = json.load(open(os.path.join(LED, 'sample_manifest.json')))
    tids = list(man['cohort_a_representative']) + \
        (man.get('cohort_b_targeted', {}).get('B1_s1pos_v41neg', [])
         + man.get('cohort_b_targeted', {}).get('B1_s1neg_v41pos', []))
    out = set()
    for tid in tids:
        rb = by.get(tid, {})
        s1, v41 = rb.get('S1_shrinkage'), rb.get('V41_union')
        if (s1 and v41 and s1.get('union') is not None
                and v41.get('union') is not None
                and bool(s1['union']) != bool(v41['union'])):
            out.add(tid)
    return out


S1V41DIS = _load_s1v41dis()


def _disagreements():
    man = json.load(open(os.path.join(LED, 'sample_manifest.json')))
    ids = [f[:-5] for f in
           sorted(os.listdir(PK)) if f.endswith('.json')
           and not f.startswith('_')]
    out = []
    for pid in ids:
        p = json.load(open(os.path.join(PK, pid + '.json')))
        if p['pack_id'].startswith('P__'):
            continue
        ra_p = _cache_path('judge_a', pid)
        rb_p = _cache_path('judge_b', pid)
        ra = json.load(open(ra_p)) if os.path.exists(ra_p) else None
        rb = json.load(open(rb_p)) if os.path.exists(rb_p) else None
        if not (ra and ra.get('ok') and rb and rb.get('ok')):
            continue
        ja = ra.get('judgment') or {}
        jb = rb.get('judgment') or {}
        for pid2 in [pid]:
            pass
        pa = ja.get('per_execution') or {}
        pb = jb.get('per_execution') or {}
        for lab in set(pa) & set(pb):
            ua = (pa[lab] or {}).get('union')
            ub = (pb[lab] or {}).get('union')
            dims = set()
            for d in ('assertions', 'decisions', 'actions', 'outcomes'):
                va = ((pa[lab] or {}).get('per_dimension') or {})
                vb = ((pb[lab] or {}).get('per_dimension') or {})
                if va.get(d) != vb.get(d):
                    dims.add(d)
            ck_a = (ja.get('checkpoint_scope') or {}).get(lab)
            ck_b = (jb.get('checkpoint_scope') or {}).get(lab)
            if 'checkpoint_scope' in p and ck_a != ck_b:
                dims.add('checkpoint_scope')
            if ua != ub or dims:
                out.append({'case': f'{pid}::{lab}',
                            'pack_id': pid, 'label': lab,
                            'judge_a_union': ua, 'judge_b_union': ub,
                            'dim_disagreements': sorted(dims),
                            'judge_a': pa.get(lab), 'judge_b': pb.get(lab)})
    return out


def adjudicate():
    man = json.load(open(os.path.join(LED, 'sample_manifest.json')))
    discs = _disagreements()
    print('substantive disagreements:', len(discs))
    by_pack = {}
    for d in discs:
        by_pack.setdefault(d['pack_id'], []).append(d)

    def prio(pid):
        pre = pid.split('__')[0]
        tid = pid.split('__', 1)[1] if len(pid.split('__')) > 1 else ''
        if pre == 'A':
            # A packets whose LEP row is an S1<->V4.1 disagreement come
            # first (they carry the cause_class/support fields, addendum A1)
            return (0, tid) if tid in S1V41DIS else (1, tid)
        return (2, {'B4': 0, 'B1': 1, 'B3': 2, 'B2': 3}.get(pre, 9), tid)

    order = sorted(by_pack, key=prio)
    limit = int(os.environ.get('ANN_ADJ_LIMIT', '0'))
    if limit:
        order = order[:limit]
    adjs = []
    for pid in order:
        cases = by_pack[pid]
        p = json.load(open(os.path.join(PK, pid + '.json')))
        pre = pid.split('__')[0]
        tid = pid.split('__', 1)[1] if len(pid.split('__')) > 1 else ''
        want_cause = (pre == 'B1' or tid in S1V41DIS)
        ctx = {}
        for d in cases:
            ctx[d['case']] = {'judge_X': (d['judge_a'],
                                          d['judge_a_union']),
                             'judge_Y': (d['judge_b'],
                                        d['judge_b_union']),
                             'dimensions_in_disagreement':
                                 d['dim_disagreements']}
        user = judge_prompt(p, adjudication_context=ctx, cause=want_cause)
        budget_guard(1, 'adjudicate')
        res = live_call(JUDGES['adjudicator'], SYSTEM, user, max_retries=2)
        record_call('adjudicator', pid, 'adjudicate',
                    JUDGES['adjudicator'], res, user, SYSTEM)
        judgment = parse_json_lenient(res['text']) if res.get('text') \
            else None
        rec = {'judge': 'adjudicator', 'pack_id': pid, 'ok': res['ok'],
               'valid_json': isinstance(judgment, dict),
               'judgment': judgment, 'usage': res.get('usage') or {},
               'cases': [c['case'] for c in cases],
               'cause_fields': want_cause,
               'prompt_version': PROMPT_VERSION, 'ts': res.get('ts')}
        json.dump(rec, open(_cache_path('adjudicator', pid,
                                         'adjudicate'), 'w'),
                  indent=1)
        adjs.append(rec)
        u = res.get('usage') or {}
        print(f'adjudicator {pid[:40]:40s} in={u.get("input_tokens")} '
              f'out={u.get("output_tokens")} valid={rec["valid_json"]} '
              f'cause={want_cause}', flush=True)
        json.dump(adjs, open(os.path.join(LED,
                                           'adjudication_ledger.json'),
                             'w'), indent=1)
    json.dump(adjs, open(os.path.join(LED,
                                      'adjudication_ledger.json'),
                         'w'), indent=1)
    print('ADJUDICATE done.  live calls total:', count_calls())


if __name__ == '__main__':
    main()
