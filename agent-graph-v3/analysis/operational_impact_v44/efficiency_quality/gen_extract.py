#!/usr/bin/env python3.11
"""Generation-length + LLM-call-duration extractor (extension §5).

Reads ONLY the staged merged trace pool (the exact pool the V4.4 ledger
consumed) and emits a compact per-trace-id JSONL of MODEL-GENERATED content
lengths and per-call durations. Deterministic (pure function of file bytes).

KEY MEASUREMENT MODEL (documented in GENERATION_LENGTH_AUDIT.md):
  * `reasoning.output_text` is a `[tool_use:<name>]` marker (~25 chars), NOT
    the model's chain-of-thought. The CoT is not stored in this corpus.
  * Model-generated free text is captured in the content-producing TOOL-CALL
    arguments:  write_file.content, handoff.summary, submit_final.answer,
    memory_write.content.  Each of these is DUPLICATED across events
    (agent_handoff.output_text == handoff arg summary == topology_transition.
    observable.handoff_summary;  final_response.output_text == submit_final
    arg answer).  To avoid double-counting, generated content is summed
    ONCE per source tool_call, never from the duplicated event copies.
  * EXCLUDED (not model-generated): user_input, system_init, tool_result
    (externally supplied tool output), reasoning.input_text (accumulated
    prompt/context), the [tool_use:X] markers themselves.
  * Per-call duration: the inter-event gap that ENDS at a reasoning event
    (events are logged at step completion). Sum over calls = llm_inference_s.

Dedup invariant asserted: handoff/submit_final arg text equals the
duplicated event text, so counting only the tool_call source is the
conservative (no-double-count) choice.
"""
from __future__ import annotations

import glob
import json
import os
import re
from collections import defaultdict
from datetime import datetime

ROOT = '/u/kdhande/agent-graph/agent-graph-v3'
STAGED = os.path.join(ROOT, 'analysis/consolidated_20261121_bnd_v41/staged/merged/traces')
V44 = os.path.join(
    ROOT, 'analysis/behavioral_divergence_final/calibration_v3/out_v44/final/per_execution_v44.jsonl')
LEDGER = os.path.join(ROOT, 'analysis/full_production_baseline_deviation_v44/bnd_v44_per_trace.jsonl')
OUTDIR = os.path.join(ROOT, 'analysis/operational_impact_v44/efficiency_quality/out')

_TID_RE = re.compile(r'"trace_id":\s*"([^"]+)"')


def _content_chars(tool_name, args):
    """Model-composed free-text characters in a tool_call argument dict."""
    if not isinstance(args, dict):
        return 0
    t = (tool_name or '').lower()
    total = 0
    if t == 'write_file' or t == 'write' or t in ('create_file', 'write_files'):
        total += len(str(args.get('content') or args.get('file_content') or ''))
    elif t == 'handoff':
        # summary is the model-composed transfer text; target_agent is a role token
        total += len(str(args.get('summary') or ''))
    elif t == 'submit_final' or t == 'submit' or t == 'finalize':
        for v in args.values():
            if isinstance(v, str):
                total += len(v)
    elif t in ('memory_write', 'write_memory', 'store'):
        for k in ('content', 'value', 'text', 'data'):
            if k in args:
                total += len(str(args.get(k) or ''))
                break
    else:
        # other tools: count only clearly-free-text string args (not paths/commands)
        for v in args.values():
            if isinstance(v, str) and len(v) > 20 and not v.startswith('documents/') \
                    and not v.startswith('reports/') and not v.startswith('notes/'):
                total += len(v)
    return total


def _gen_content_string(tool_name, args):
    """The single model-composed free-text string for a content tool (dedup)."""
    if not isinstance(args, dict):
        return ''
    t = (tool_name or '').lower()
    if t in ('write_file', 'write', 'create_file', 'write_files'):
        return str(args.get('content') or args.get('file_content') or '')
    if t == 'handoff':
        return str(args.get('summary') or '')
    if t in ('submit_final', 'submit', 'finalize'):
        return ' '.join(str(v) for v in args.values() if isinstance(v, str))
    if t in ('memory_write', 'write_memory', 'store'):
        for k in ('content', 'value', 'text', 'data'):
            if k in args:
                return str(args.get(k) or '')
        return ''
    return ''


def _trace_gen(path):
    with open(path, 'rb') as fh:
        raw = fh.read()
    d = json.loads(raw)
    evs = d['events']
    try:
        ts = [datetime.fromisoformat(e['timestamp']) for e in evs]
        ok = all(t.tzinfo is not None for t in ts)
    except Exception:
        ts = [None] * len(evs)
        ok = False

    n_llm = sum(1 for e in evs if e['event_type'] == 'reasoning')
    # per-call generated content + per-call duration.
    # Generated content is counted ONCE per source tool_call (the content-
    # producing call). Content-producing tools are dedup'd against their
    # duplicated event copies (agent_handoff/final_response output_text,
    # topology_transition.observable.handoff_summary) by construction.
    per_call_chars = []
    per_call_durs = []
    gen_chars = 0
    gen_words = 0
    role_gen = defaultdict(int)
    marker_chars = 0
    final_chars = 0
    tool_exec = 0.0
    llm_inf = 0.0
    CONTENT_TOOLS = ('write_file', 'write', 'create_file', 'write_files',
                     'handoff', 'submit_final', 'submit', 'finalize',
                     'memory_write', 'write_memory', 'store')
    for i, e in enumerate(evs):
        et = e['event_type']
        role = e.get('agent_role') or ''
        gap = 0.0
        if i > 0 and ok:
            gap = (ts[i] - ts[i - 1]).total_seconds()
            if gap < 0:
                gap = 0.0
        if et == 'reasoning':
            marker_chars += len(e.get('output_text') or '')
            llm_inf += gap
            per_call_durs.append(gap)
            # attribute the FOLLOWING tool_call's composed content to this call
            if i + 1 < len(evs) and evs[i + 1]['event_type'] == 'tool_call':
                nc = evs[i + 1]
                if (nc.get('tool_name') or '').lower() in CONTENT_TOOLS:
                    c = _content_chars(nc.get('tool_name'), nc.get('tool_arguments'))
                    per_call_chars.append(c)
                    gen_chars += c
                    gen_words += len(_gen_content_string(nc.get('tool_name'),
                                                         nc.get('tool_arguments') or {}).split())
                    role_gen[role] += c
                else:
                    per_call_chars.append(0)
        elif et == 'tool_call':
            # count a content-producing call NOT already attributed to a
            # preceding reasoning call (covers stray calls); avoids double-count
            if i == 0 or evs[i - 1]['event_type'] != 'reasoning':
                if (e.get('tool_name') or '').lower() in CONTENT_TOOLS:
                    c = _content_chars(e.get('tool_name'), e.get('tool_arguments'))
                    gen_chars += c
                    gen_words += len(_gen_content_string(e.get('tool_name'),
                                                         e.get('tool_arguments') or {}).split())
                    role_gen[role] += c
        elif et == 'tool_result':
            if i > 0 and ok and evs[i - 1]['event_type'] == 'tool_call':
                tool_exec += gap
        elif et == 'final_response':
            final_chars = len(e.get('output_text') or '')

    per_call_chars = per_call_chars or [0]
    return {
        'n_llm_calls': n_llm,
        'llm_inference_s_total': llm_inf,
        'llm_inference_s_per_call': (llm_inf / n_llm) if n_llm else None,
        'per_call_durations_s': per_call_durs,
        'per_call_generated_chars': per_call_chars,
        'mean_generated_chars_per_llm_call': (sum(per_call_chars) / len(per_call_chars)) if per_call_chars else 0.0,
        'median_generated_chars_per_llm_call': _median(per_call_chars) if per_call_chars else 0.0,
        'generated_content_chars': gen_chars,
        'generated_content_words': gen_words,
        'final_response_chars': final_chars,
        'reasoning_marker_chars': marker_chars,
        'tool_execution_s_total': tool_exec,
        'per_role_generated_chars': dict(role_gen),
        'ts_valid': ok,
    }


def _median(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    v44 = [json.loads(l) for l in open(V44) if l.strip()]
    led = {r['trace_id']: r for r in (json.loads(l) for l in open(LEDGER) if l.strip())}
    need = set(r['trace_id'] for r in v44)
    refs = set()
    for t in need:
        L = led.get(t)
        if L:
            refs.update(L.get('references', []))
    need_all = need | refs
    print(f'[gen_extract] need={len(need)} refs={len(refs)} union={len(need_all)}', flush=True)

    # index staged files by trace_id (head-read)
    files_by_tid = defaultdict(list)
    nscan = 0
    for f in sorted(glob.glob(os.path.join(STAGED, '*_trace.json'))):
        p = os.path.realpath(f)
        if not os.path.exists(p):
            continue
        with open(p) as fh:
            head = fh.read(512)
        m = _TID_RE.search(head)
        if m and m.group(1) in need_all:
            files_by_tid[m.group(1)].append(f)
            nscan += 1
    print(f'[gen_extract] head-scanned {nscan} staged files', flush=True)

    def _attempt_of(f):
        with open(os.path.realpath(f)) as fh:
            return (json.load(fh).get('metadata') or {}).get('attempt_id')

    out = os.path.join(OUTDIR, 'generation_length_per_trace.jsonl')
    n = 0
    n_multi = 0
    with open(out, 'w') as fh:
        for tid in sorted(need_all):
            fs = files_by_tid.get(tid)
            if not fs:
                continue
            chosen = fs[0]
            if len(fs) > 1:
                # rare: pick the staged file whose attempt_id matches the ledger pin
                pin = (led.get(tid) or {}).get('attempt_id')
                n_multi += 1
                for cand in fs:
                    if _attempt_of(cand) == pin:
                        chosen = cand
                        break
            gen = _trace_gen(os.path.realpath(chosen))
            rec = {'trace_id': tid, **gen}
            fh.write(json.dumps(rec, separators=(',', ':')) + '\n')
            n += 1
            if n % 4000 == 0:
                print(f'  ... {n} traces done', flush=True)
    print(f'[gen_extract] wrote {n} rows ({n_multi} multi-file tids resolved) -> {out}', flush=True)


if __name__ == '__main__':
    main()
