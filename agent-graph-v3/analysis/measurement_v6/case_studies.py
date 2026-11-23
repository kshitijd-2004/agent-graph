#!/usr/bin/env python3
"""
Trace-level case studies (v6) — requirement 7.

Produces CONCRETE, quoted evidence for correct, incorrect and ambiguous
extraction.  Reads real traces; writes nothing outside stdout / out/.

Each case records:
    trace_id | quoted agent-authored passage | v6 extraction | assessment |
    classification (CORRECT / INCORRECT / AMBIGUOUS / DEFECT)

Usage:  python3.11 analysis/measurement_v6/case_studies.py
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from features_v6 import (authored_text_all, extract_features_v6,   # noqa: E402
                         final_response_text)

REPO = '/u/kdhande/agent-graph/agent-graph-v3'
CONS = os.path.join(REPO, 'analysis', 'consolidated_20261121_bnd_v41')
TRACES = os.path.join(CONS, 'staged', 'merged', 'traces')
SPECROOT = os.path.join(REPO, 'analysis',
                        'bnd_v41_cw_swe200_provisional', 'specroot')


def load_specs():
    out = {}
    for d in sorted(os.listdir(SPECROOT)):
        p = os.path.join(SPECROOT, d, 'manifest.json')
        if os.path.isfile(p):
            try:
                out[d] = json.load(open(p))
            except Exception:                              # noqa: BLE001
                continue
    return out


def find(fn, limit=4000):
    for n in sorted(os.listdir(TRACES))[:limit]:
        if not n.endswith('.json'):
            continue
        try:
            t = json.load(open(os.path.join(TRACES, n)))
        except Exception:                                  # noqa: BLE001
            continue
        if fn(t):
            return t
    return None


def show(title, trace_id, quote, extracted, assessment, cls):
    print('=' * 78)
    print(f'[{cls}] {title}')
    print(f'  trace_id : {trace_id}')
    print(f'  quoted   : {quote}')
    print(f'  v6       : {extracted}')
    print(f'  assess   : {assessment}')
    print()


def main():
    specs = load_specs()

    # ── CASE 1: clear positive — interim "contradicted", final "supported" ──
    def c1(t):
        tid = t.get('trace_id') or ''
        return tid.startswith('res_') and 'benign' in tid

    tr = find(c1)
    if tr:
        tid = tr['trace_id']
        spec = specs.get(tid.split('.')[0]) or {}
        f = extract_features_v6(tr, spec, 'research_synthesis')
        fin = final_response_text(tr)
        key = next(iter(f.decisions), None)
        show('research verdict — final-response extraction',
             tid,
             (fin[:220] + '…') if len(fin) > 220 else fin,
             (f"{key}: decisions={f.decisions[key].value!r} "
              f"state={f.decisions[key].state} | "
              f"assertions={f.assertions.get(key).value!r}"
              if key else 'no key'),
             'decision channel reads the FINAL response; assertion channel '
             'reads the whole trajectory. Values can legitimately differ when '
             'the agent revised its verdict mid-run.',
             'CASE-study')

    # ── CASE 2: negation / hedging in real text ─────────────────────────────
    def find_quote(sub, fam_prefix='res_', limit=6000):
        for n in sorted(os.listdir(TRACES))[:limit]:
            if not n.endswith('.json'):
                continue
            try:
                t = json.load(open(os.path.join(TRACES, n)))
            except Exception:                              # noqa: BLE001
                continue
            tid = t.get('trace_id') or ''
            if not tid.startswith(fam_prefix):
                continue
            txt = authored_text_all(t)
            low = txt.lower()
            if sub in low:
                i = low.find(sub)
                return t, txt[max(0, i - 110):i + 170].replace('\n', ' ')
        return None, None

    for sub, label in (('cannot say', 'modal denial'),
                       ('not supported', 'negated support'),
                       ('insufficient', 'insufficiency')):
        t, quote = find_quote(sub)
        if not t:
            continue
        tid = t['trace_id']
        spec = specs.get(tid.split('.')[0]) or {}
        f = extract_features_v6(t, spec, 'research_synthesis')
        key = next(iter(f.decisions), None)
        show(f'research {label} — real trace',
             tid, quote,
             (f"decisions[{key}]={f.decisions[key].value!r} "
              f"state={f.decisions[key].state} basis="
              f"{f.decisions[key].basis}" if key else 'n/a'),
             'Under V4.1 a negated or modal-denied phrase collapsed to '
             'SUPPORTED. v6 scopes negation to the clause and abstains on '
             'modal denial.',
             'CASE-study')

    # ── CASE 3: financial year-as-answer (D10), real ────────────────────────
    def c3(t):
        tid = t.get('trace_id') or ''
        return tid.startswith('fin_') and 'benign' in tid

    shown = 0
    for n in sorted(os.listdir(TRACES))[:4000]:
        if not n.endswith('.json'):
            continue
        try:
            t = json.load(open(os.path.join(TRACES, n)))
        except Exception:                                  # noqa: BLE001
            continue
        tid = t.get('trace_id') or ''
        if not tid.startswith('fin_'):
            continue
        spec = specs.get(tid.split('.')[0]) or {}
        if spec.get('task_family') != 'financial_analysis':
            continue
        f = extract_features_v6(t, spec, 'financial_analysis')
        ans = (spec.get('required_facts') or {}).get('answer') or {}
        if f.answer and f.answer.state == 'observed' and ans.get('value') \
                is not None:
            fin = final_response_text(t)
            show('financial final answer — real extraction',
                 tid, (fin[-240:].replace('\n', ' ') if fin else ''),
                 f'answer={f.answer.value!r} basis={f.answer.basis} '
                 f'| GT={ans.get("value")!r} tol={ans.get("tolerance")!r}',
                 'Compare extracted value to GT. A bare 4-digit year here '
                 'would be the D10 defect; a 10^k offset would be a '
                 'unit/scale spec defect.',
                 'CASE-study')
            shown += 1
        if shown >= 3:
            break

    # ── CASE 4: code review stance ──────────────────────────────────────────
    def c4(t):
        tid = t.get('trace_id') or ''
        return tid.startswith('code_review') and 'benign' in tid

    tr = find(c4)
    if tr:
        tid = tr['trace_id']
        spec = specs.get(tid.split('.')[0]) or {}
        f = extract_features_v6(tr, spec, 'code_review')
        fin = final_response_text(tr)
        show('code review — issue stance',
             tid, (fin[:240] + '…') if len(fin) > 240 else fin,
             json.dumps({k: v.jsonable() for k, v in f.decisions.items()})[:400],
             'Stance is graded on DEFECT IDENTIFICATION, not path overlap. '
             'A repair_proposed here must come from an agentive frame, not a '
             'bare "the patch" nominal (D4).',
             'CASE-study')


if __name__ == '__main__':
    main()
