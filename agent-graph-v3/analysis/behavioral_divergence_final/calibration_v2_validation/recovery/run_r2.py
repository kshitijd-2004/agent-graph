#!/usr/bin/env python3.11
"""Round-2 annotation runner (2 judges + adjudicator), sequential.

Imports round-1 scripts/run_annotation.py UNMODIFIED (byte-frozen
prompt code — judge_prompt / SYSTEM / live_call / annotate
mechanics), monkeypatching ONLY: prompt version (cv2val-ann-v4, the
v3 prompt body reused byte-identically), cache dir
(recovery/out/judge_cache), ledger (recovery/out/call_ledger*.jsonl),
packet dir (recovery/out/packets), and the global cap (720).
Execution is SEQUENTIAL (addendum A1 lesson: no parallel shards).

Phases:
  pilot      5 representative + 5 diagnostic packets, both judges
              (mechanics check; these 10 packets are EXCLUDED from
              C1-C3 gate denominators at scoring time)
  confirm    the remaining two-judge sweep, judge_a then judge_b
              per packet, up to the global cap
  adjudicate  unresolved + S1<->V4.1-disagreement packets (priority),
              cause_class fields on the diagnostic packets

Retries/empty responses are re-attempted (annotate() in round-1 code
re-attempts incomplete caches; every attempt is a counted ledger
call).  No call is written as a label.
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STUDY = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(STUDY, 'scripts'))
import run_annotation as RA  # round-1 frozen module (unmodified)

# ── monkeypatch to r2 isolation ────────────────────────────────────
RA.PROMPT_VERSION = 'cv2val-ann-v4'
RA.JC = os.path.join(HERE, 'out', 'judge_cache')
RA.OUT = os.path.join(HERE, 'out')
RA.PK = os.path.join(HERE, 'out', 'packets')
RA.MAX_CALLS = 720


def r2_budget_guard(n_more=1, phase='confirm'):
    """GLOBAL 720 cap for ALL r2 phases.  (Round-1's budget_guard
    hard-codes a 540 confirm-phase cap; that belongs to round 1 and
    must NOT apply here — the approved r2 cap is 720 total.)"""
    used = RA.count_calls()
    if used + n_more > RA.MAX_CALLS:
        raise SystemExit(
            f'HARD CAP: {used} r2 live calls used; {n_more} more '
            f'would exceed the authorized {RA.MAX_CALLS}-call round-2 '
            f'total.  Additional approval required (prereg §5 '
            f'analog).  Stopping.')
    return used
for j in ('judge_a', 'judge_b', 'adjudicator'):
    os.makedirs(os.path.join(RA.JC, j), exist_ok=True)
os.makedirs(RA.PK, exist_ok=True)


RA.budget_guard = r2_budget_guard   # annotate()/adjudication use the
                                     # global 720 cap, never round-1's
                                     # 540 confirm-phase cap


def _packets():
    pmap = {}
    for f in sorted(os.listdir(RA.PK)):
        if f.endswith('.json') and not f.startswith('_'):
            d = json.load(open(os.path.join(RA.PK, f)))
            pmap[d['pack_id']] = d
    return pmap


def _rep_diag():
    pmap = _packets()
    reps = sorted(pid for pid in pmap
                  if pid.startswith('R__')
                  and pmap[pid]['cohort'] == 'representative')
    diags = sorted(pid for pid in pmap
                   if pid.startswith('R2D__'))
    h0 = sorted(pid for pid in pmap if pid.startswith('R2H0__'))
    return pmap, reps, diags, h0


def phase_pilot():
    pmap, reps, diags, h0 = _rep_diag()
    pil = reps[:5] + diags[:5]
    for j in ('judge_a', 'judge_b'):
        for pid in pil:
            rec, live = RA.annotate(j, pmap[pid])
            u = rec.get('usage') or {}
            print(f'PILOT {j} {pid[:40]:40s} live={live} '
                  f'in={u.get("input_tokens")} out={u.get("output_tokens")}'
                  f' valid={rec.get("valid_json")}', flush=True)
    json.dump(pil, open(os.path.join(HERE, 'out', 'pilot_packs.json'),
                        'w'), indent=1)
    print('PILOT done. live calls total:', RA.count_calls())


def phase_confirm():
    pmap, reps, diags, h0 = _rep_diag()
    pilot = set(json.load(open(os.path.join(HERE, 'out',
                                            'pilot_packs.json')))
                if os.path.exists(os.path.join(HERE, 'out',
                                                'pilot_packs.json'))
                else [])
    ids = [p for p in reps + diags + h0 if p not in pilot]
    for j in ('judge_a', 'judge_b'):
        for pid in ids:
            rec, live = RA.annotate(j, pmap[pid])
            u = rec.get('usage') or {}
            if live:
                print(f'{j} {pid[:40]:40s} live={live} '
                      f'in={u.get("input_tokens")} '
                      f'out={u.get("output_tokens")} '
                      f'valid={rec.get("valid_json")}', flush=True)
    print('CONFIRM done. live calls total:', RA.count_calls())


def phase_adjudicate():
    pmap, reps, diags, h0 = _rep_diag()
    pilot = set(json.load(open(os.path.join(HERE, 'out',
                                            'pilot_packs.json')))
                if os.path.exists(os.path.join(HERE, 'out',
                                                'pilot_packs.json'))
                else [])
    # UNION-disagreement detection (the frozen adjudicator resolves
    # the union label only; dimension-only disagreements are left
    # per-dimension-EXCLUDED, never forced — that is the honest
    # treatment and avoids spending calls on cases that do not move
    # the gate label).  Diagnostic (S1<->V4.1) packets first,
    # cause_class fields on them (requirement-8 read).
    todo = []
    for pid in diags + reps:
        if pid in pilot:
            continue
        pa = RA._cache_path('judge_a', pid)
        pb = RA._cache_path('judge_b', pid)
        ra = json.load(open(pa)) if os.path.exists(pa) else None
        rb = json.load(open(pb)) if os.path.exists(pb) else None
        if not ((ra or {}).get('judgment') and
                (rb or {}).get('judgment')):
            continue
        lep = pmap[pid].get('lep_execution_label')
        ja = ra['judgment'].get('per_execution') or {}
        jb = rb['judgment'].get('per_execution') or {}
        ua = ((ja.get(lep) or {}).get('union'))
        ub = ((jb.get(lep) or {}).get('union'))
        if ua != ub:
            todo.append(pid)
    diagset = {pid for pid in todo if pid.startswith('R2D__')}
    order = sorted(diagset) + sorted(set(todo) - diagset)
    done = []
    for pid in order:
        p = pmap[pid]
        want_cause = pid.startswith('R2D__')
        # build the adjudication context (the two judges' verdicts,
        # anonymized X/Y)
        ja = json.load(open(RA._cache_path('judge_a', pid)))
        jb = json.load(open(RA._cache_path('judge_b', pid)))
        lep = p.get('lep_execution_label')
        cases = {f'{pid}::{lep}': {
            'judge_X': ((ja.get('judgment') or {})
                        .get('per_execution') or {}).get(lep),
            'judge_Y': ((jb.get('judgment') or {})
                        .get('per_execution') or {}).get(lep)}}
        user = RA.judge_prompt(p, adjudication_context=cases,
                               cause=want_cause)
        RA.budget_guard(1, 'adjudicate')   # 720-call global cap
        res = RA.live_call(RA.JUDGES['adjudicator'], RA.SYSTEM, user,
                           max_retries=2)
        RA.record_call('adjudicator', pid, 'adjudicate',
                       RA.JUDGES['adjudicator'], res, user, RA.SYSTEM)
        judgment = RA.parse_json_lenient(res['text']) \
            if res.get('text') else None
        rec = {'judge': 'adjudicator', 'pack_id': pid,
               'ok': bool(res.get('text')),
               'valid_json': isinstance(judgment, dict),
               'judgment': judgment, 'usage': res.get('usage') or {},
               'cause_fields': want_cause,
               'prompt_version': RA.PROMPT_VERSION, 'ts': res.get('ts')}
        json.dump(rec, open(RA._cache_path('adjudicator', pid,
                                           'adjudicate'), 'w'),
                  indent=1)
        done.append(pid)
        u = res.get('usage') or {}
        print(f'adjudicator {pid[:38]:38s} in={u.get("input_tokens")}'
              f' out={u.get("output_tokens")} valid={rec["valid_json"]}'
              f' cause={want_cause}', flush=True)
    json.dump(done, open(os.path.join(HERE, 'out',
                                      'adjudicated_packs.json'), 'w'),
              indent=1)
    print('ADJUDICATE done. live calls total:', RA.count_calls())


def main():
    phase = sys.argv[1] if len(sys.argv) > 1 else 'confirm'
    if phase == 'pilot':
        phase_pilot()
    elif phase == 'confirm':
        phase_confirm()
    elif phase == 'adjudicate':
        phase_adjudicate()
    else:
        raise SystemExit('phase = pilot|confirm|adjudicate')


if __name__ == '__main__':
    if 'PYTHONHASHSEED' not in os.environ:
        os.environ['PYTHONHASHSEED'] = '0'
        os.execve(sys.executable, [sys.executable] + sys.argv,
                  os.environ)
    main()
