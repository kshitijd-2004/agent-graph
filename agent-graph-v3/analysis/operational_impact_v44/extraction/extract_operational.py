#!/usr/bin/env python3.11
"""Operational-impact extraction for V4.4 (deterministic, read-only on frozen artifacts).

Builds two tables from the canonical trace pool that the V4.4 ledger was
constructed from (analysis/consolidated_20261121_bnd_v41/staged/merged —
symlinks into /projects/biha/agentprop_consolidated_20261121/traces),
joined with the frozen V4.4 per-execution labels, the frozen V4.4 ledger
(benign reference identities + attempt_id pins), and the benchmark_records
(recorded end-to-end wall-clock runtime_seconds).

Outputs (JSONL, deterministic — pure function of file bytes; no randomness):
  out/operational_impact_per_execution.jsonl   one row per V4.4 execution (16,767)
  out/benign_reference_operational.jsonl        one row per distinct benign ref in V4.4 pools
  out/extraction_coverage.json                  record-matching + trace-matching census

Metric conventions (canonical for every analysis agent):
  REC = directly recorded;  RECON = reconstructed from recorded events (labeled proxy).
  M0  end_to_end_wall_clock_s      REC    benchmark_records.runtime_seconds, matched to the
                                           trace via the trace file's own metadata.attempt_id
                                           (V4.4 ledger pins the same attempt for perturbed tids).
  M1  trace_span_s                 REC    last-first event timestamp (same trace).
  M2  llm_inference_s              RECON  sum of inter-event gaps (i-1 -> i) where event i is a
                                           'reasoning' event. Events are logged at step completion:
                                           reasoning and its emitted tool_call/final_response share
                                           a timestamp, so generation time sits in the gap that
                                           ENDS at a reasoning event.
  M3  tool_execution_s             RECON  sum of tool_call -> tool_result gaps (in-process tools; ~0).
  M4  n_events / M5 n_reasoning (LLM-call count) / M6 n_tool_calls / M7 n_tool_results /
       M8 n_tool_errors / M9 n_agent_handoffs / M10 n_topology_transitions / M11
       n_memory_write / M12 n_memory_retrieval / M13 n_finalization_handoff              REC.
  M14 duplicate_tool_call_groups / M15 repeated_tool_call_excess       RECON (multiset over
       (role, tool, canonical-args); group with count>=2; excess = sum(count-1)).
  M16 backend_retry_count / M17 review_cap_forced_final / M18
       review_finalization_reason / M19 termination_reason                     REC (metadata).
  M20 per_role {duration_s, llm_inference_s, n_reasoning, n_tool_calls, n_handoffs} REC.
  M21 tool_call_multiset "role:tool" counts                                  REC.
  M22 chars_input_reasoning_proxy / chars_output_reasoning_proxy     RECON, FLAGGED PROXY.
       Actual token consumption is NOT recorded anywhere in this corpus (traces, records, and
       vLLM aggregate 10s-interval throughput logs). Char counts must never be presented as tokens.
  M23 stage_count / final_agent_role / dataset_eligible / labels.task_success /
       evaluation.task_success / evaluation.failure_types                      REC.
  M24 condition / lep_codes / repetition_index / execution_variant /
       unfulfilled_injections / injection_origin_count_field                   REC.
  M25 ts_valid (tz-aware ISO parse) / ts_monotonic / ts_nonmonotonic_count     REC (audit fields).

Concurrency: coordinator_workers may run agents in parallel — per-role durations above are
gap-based and are reported PER ROLE, NOT summed into a fake end-to-end total. The authoritative
end-to-end latency is M0 (recorded runtime_seconds); M1 (event span) is a trace-internal bound.
M2+M3 are shares of M1 only where the trace is effectively serial.

Determinism: PYTHONHASHSEED=0 recommended; sorted globs; no Date/random.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime

ROOT = '/u/kdhande/agent-graph/agent-graph-v3'
STAGED = os.path.join(
    ROOT, 'analysis/consolidated_20261121_bnd_v41/staged/merged/traces')
V44_PEREXEC = os.path.join(
    ROOT, 'analysis/behavioral_divergence_final/calibration_v3/out_v44/final/per_execution_v44.jsonl')
LEDGER = os.path.join(
    ROOT, 'analysis/full_production_baseline_deviation_v44/bnd_v44_per_trace.jsonl')
OUTDIR = os.path.join(ROOT, 'analysis/operational_impact_v44/out')

RECORD_PATTERNS = [
    'production_run/node_*/benchmark_records.jsonl',
    'production_run/bv_rl_regeneration_v2/node_*/benchmark_records.jsonl',
    'production_run/coordinator_workers_regeneration/node_*/benchmark_records.jsonl',
    'production_runs/swebench_200_addition/node_*/benchmark_records.jsonl',
]

_TID_HEAD_RE = re.compile(r'"trace_id":\s*"([^"]+)"')


def _canon_args(a):
    return json.dumps(a, sort_keys=True, default=str) if a is not None else None


def load_records():
    by_attempt = {}
    by_tid = defaultdict(list)
    n = 0
    for pat in RECORD_PATTERNS:
        for f in sorted(glob.glob(os.path.join(ROOT, pat))):
            with open(f) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not r.get('trace_id'):
                        continue
                    n += 1
                    by_tid[r['trace_id']].append(r)
                    att = r.get('attempt_id')
                    if att:
                        by_attempt.setdefault(att, r)
    return by_attempt, by_tid, n


def trace_metrics(path: str) -> dict:
    with open(path, 'rb') as fh:
        raw = fh.read()
    d = json.loads(raw)
    evs = d['events']
    md = d.get('metadata', {}) or {}
    n = len(evs)
    ts = []
    ok_ts = True
    try:
        ts = [datetime.fromisoformat(e['timestamp']) for e in evs]
        ok_ts = len(ts) == n and all(t.tzinfo is not None for t in ts)
    except Exception:
        ts = [None] * n
        ok_ts = False
    nonmono = sum(1 for i in range(1, n) if ts[i] < ts[i - 1]) if ok_ts else 0
    span = (ts[-1] - ts[0]).total_seconds() if ok_ts and n >= 2 else None

    llm = 0.0
    tool_exec = 0.0
    chars_in = 0
    chars_out = 0
    n_reasoning = n_toolcalls = n_toolres = n_handoffs = n_trans = 0
    n_memw = n_memr = n_fin = 0
    tool_multiset = Counter()
    role_gap = defaultdict(float)
    role_llm = defaultdict(float)
    role_reasoning = Counter()
    role_toolcalls = Counter()
    role_handoffs = Counter()
    role_events = Counter()

    for i, e in enumerate(evs):
        et = e['event_type']
        role = e.get('agent_role') or ''
        role_events[role] += 1
        gap = 0.0
        if i > 0 and ok_ts:
            gap = (ts[i] - ts[i - 1]).total_seconds()
            if gap < 0:
                gap = 0.0
            role_gap[role] += gap
        if et == 'reasoning':
            n_reasoning += 1
            role_reasoning[role] += 1
            chars_in += len(e.get('input_text') or '')
            chars_out += len(e.get('output_text') or '')
            llm += gap
            role_llm[role] += gap
        elif et == 'tool_call':
            n_toolcalls += 1
            role_toolcalls[role] += 1
            key = (role, e.get('tool_name') or '', _canon_args(e.get('tool_arguments')))
            tool_multiset[key] += 1
        elif et == 'tool_result':
            n_toolres += 1
            if i > 0 and ok_ts and evs[i - 1].get('event_type') == 'tool_call':
                g2 = (ts[i] - ts[i - 1]).total_seconds()
                if g2 > 0:
                    tool_exec += g2
        elif et == 'agent_handoff':
            n_handoffs += 1
            role_handoffs[role] += 1
        elif et == 'topology_transition':
            n_trans += 1
        elif et == 'memory_write':
            n_memw += 1
        elif et == 'memory_retrieval':
            n_memr += 1
        elif et == 'final_response':
            n_fin += 1

    n_toolerr = sum(1 for e in evs if e.get('tool_error'))
    dup_groups = sum(1 for c in tool_multiset.values() if c >= 2)
    dup_excess = sum(c - 1 for c in tool_multiset.values() if c >= 2)
    roles = sorted(set(list(role_gap) + list(role_reasoning) + list(role_events)))

    return {
        'n_events': n,
        'trace_span_s': span,
        'llm_inference_s_reconstructed': llm,
        'tool_execution_s_reconstructed': tool_exec,
        'orchestration_wait_s_reconstructed': (span - llm - tool_exec) if span is not None else None,
        'n_reasoning': n_reasoning,
        'n_tool_calls': n_toolcalls,
        'n_tool_results': n_toolres,
        'n_tool_errors': n_toolerr,
        'n_agent_handoffs': n_handoffs,
        'n_topology_transitions': n_trans,
        'n_memory_write': n_memw,
        'n_memory_retrieval': n_memr,
        'n_finalization_handoff': n_fin,
        'duplicate_tool_call_groups': dup_groups,
        'repeated_tool_call_excess': dup_excess,
        'tool_call_multiset': {
            f'{r}:{t}': c for (r, t, _a), c in sorted(tool_multiset.items()) if t
        },
        'ts_valid': ok_ts,
        'ts_monotonic': nonmono == 0,
        'ts_nonmonotonic_count': nonmono,
        'chars_input_reasoning_proxy': chars_in,
        'chars_output_reasoning_proxy': chars_out,
        'per_role': {
            r: {
                'duration_s': role_gap[r],
                'llm_inference_s': role_llm[r],
                'n_reasoning': role_reasoning[r],
                'n_tool_calls': role_toolcalls[r],
                'n_handoffs': role_handoffs[r],
                'n_events': role_events[r],
            } for r in roles
        },
        'metadata': {
            'attempt_id': md.get('attempt_id'),
            'condition': md.get('condition'),
            'lep_codes': md.get('lep_codes'),
            'repetition_index': md.get('repetition_index'),
            'backend_retry_count': md.get('backend_retry_count'),
            'review_cycle_count': md.get('review_cycle_count'),
            'review_cap_forced_final': md.get('review_cap_forced_final'),
            'review_finalization_reason': md.get('review_finalization_reason'),
            'termination_reason': md.get('termination_reason'),
            'handoff_count': md.get('handoff_count'),
            'stage_count': md.get('stage_count'),
            'final_agent_role': md.get('final_agent_role'),
            'dataset_eligible': md.get('dataset_eligible'),
            'execution_variant': md.get('execution_variant'),
            'unfulfilled_injections': md.get('unfulfilled_injections'),
            'injection_origin_count_field': d.get('injection_origin_count'),
        },
        'labels': {
            k: (d.get('labels') or {}).get(k)
            for k in ('task_success', 'downstream_failure', 'failure_type', 'factual_score',
                      'completeness_score', 'evaluator_confidence')
        },
        'evaluation_task_success': (md.get('evaluation') or {}).get('task_success'),
        'evaluation_failure_types': (md.get('evaluation') or {}).get('failure_types'),
        'trace_sha256': hashlib.sha256(raw).hexdigest(),
        'trace_source_basename': os.path.basename(os.path.realpath(path)),
    }


def _source_prefix(basename):
    for pre in ('original_', 'bv_rl_', 'cw_default_', 'v3_gap_'):
        if basename.startswith(pre):
            return pre.rstrip('_')
    return 'unknown'


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    print('[extract] loading benchmark_records ...', flush=True)
    by_attempt, by_tid, nrec = load_records()
    print(f'[extract] records={nrec} distinct_attempts={len(by_attempt)}', flush=True)

    print('[extract] loading V4.4 rows + frozen ledger ...', flush=True)
    v44 = [json.loads(l) for l in open(V44_PEREXEC)]
    led = {}
    with open(LEDGER) as fh:
        for l in fh:
            if l.strip():
                r = json.loads(l)
                led[r['trace_id']] = r
    need = set(r['trace_id'] for r in v44)
    refs = set()
    for t in need:
        L = led.get(t)
        if L:
            refs.update(L.get('references', []))
    need_all = need | refs
    print(f'[extract] perturbed={len(need)} benign_refs={len(refs)} union={len(need_all)}', flush=True)

    print('[extract] scanning staged pool (head-read) ...', flush=True)
    files_by_tid = defaultdict(list)
    nscanned = 0
    for f in sorted(glob.glob(os.path.join(STAGED, '*_trace.json'))):
        p = os.path.realpath(f)
        if not os.path.exists(p):
            continue
        with open(p, 'r') as fh:
            head = fh.read(512)
        mm = _TID_HEAD_RE.search(head)
        if not mm:
            continue
        tid = mm.group(1)
        if tid in need_all:
            files_by_tid[tid].append(f)
            nscanned += 1
    print(f'[extract] head-scanned {nscanned} staged files; tids seen '
          f'{len(files_by_tid)}/{len(need_all)}', flush=True)
    missing = need_all - set(files_by_tid)
    print(f'[extract] tids with no staged file: {len(missing)}', flush=True)

    print('[extract] selecting canonical file per tid ...', flush=True)
    selected = {}
    dup_count = 0
    for tid, fs in files_by_tid.items():
        if len(fs) == 1:
            selected[tid] = fs[0]
            continue
        dup_count += 1
        pin = (led.get(tid) or {}).get('attempt_id')
        best = fs[0]
        for f in fs:
            p = os.path.realpath(f)
            if not os.path.exists(p):
                continue
            with open(p, 'rb') as fh:
                dd = json.loads(fh.read())
            att = (dd.get('metadata') or {}).get('attempt_id')
            if pin is not None and att == pin:
                best = f
                break
            if att is not None and att in by_attempt:
                best = f
                break
            if att is not None:
                best = f
                break
        selected[tid] = best

    print(f'[extract] dup cells resolved: {dup_count}; computing trace metrics ...', flush=True)
    by_tid_meta = {}
    for i, tid in enumerate(sorted(selected)):
        by_tid_meta[tid] = trace_metrics(os.path.realpath(selected[tid]))
        if (i + 1) % 2000 == 0:
            print(f'  ... {i + 1} traces done', flush=True)
    print(f'[extract] traced {len(by_tid_meta)} files', flush=True)

    def record_for(tid):
        """Match a benchmark record to tid: trace attempt_id first, ledger pin second,
        single-record fallback, else ambiguous (None)."""
        att = (by_tid_meta.get(tid) or {}).get('metadata', {}).get('attempt_id')
        if att and att in by_attempt:
            return by_attempt[att], 'attempt_id_match_trace'
        pin = (led.get(tid) or {}).get('attempt_id')
        if pin and pin in by_attempt:
            return by_attempt[pin], 'attempt_id_match_ledger_pin'
        recs = by_tid.get(tid, [])
        if len(recs) == 1:
            return recs[0], 'single_record_by_tid'
        if recs:
            return None, 'multi_record_no_pin_ambiguous'
        return None, 'no_record'

    print('[extract] writing per-execution table ...', flush=True)
    per_exec_path = os.path.join(OUTDIR, 'operational_impact_per_execution.jsonl')
    with open(per_exec_path, 'w') as out:
        for r in v44:
            tid = r['trace_id']
            tm = by_tid_meta.get(tid)
            rec, how = record_for(tid)
            L = led.get(tid) or {}
            bench_refs = L.get('references', [])
            row = {
                'execution_id': r['execution_id'],
                'trace_id': tid,
                'fixture_id': r['fixture_id'],
                'lep_code': r['lep_code'],
                'task_family': r['task_family'],
                'topology': r['topology'],
                'propagation_mode': r['propagation_mode'],
                'M1_positive': bool(r['M1_selected']['positive']),
                'M1_fired_dimensions': [k for k, v in
                                        r['M1_selected']['fires_per_dimension'].items() if v is True],
                'M2_positive': bool(r['M2_selected']['positive']),
                'M2_dependency_supported': bool(r['M2_selected'].get('dependency_supported', False)),
                'M2_reach_class': r['M2_selected'].get('reach_class'),
                'M3_observed_class': r['M3_selected']['observed_class'],
                'M3_matched_degraded_observed': bool(r['M3_selected']['matched_degraded_observed']),
                'M4_frozen_matched_degraded': bool(r['M3_selected']['frozen_matched_degraded']),
                'M4_frozen_m4_class': r['M3_selected']['frozen_m4_class'],
                'gradeable': r['measurement_availability']['gradeable'],
                'n_gradeable_benign': r['measurement_availability']['n_gradeable_benign'],
                'n_pairs_benign_unavailable': r['measurement_availability']['n_pairs_benign_unavailable'],
                'n_pairs_lep_unavailable': r['measurement_availability']['n_pairs_lep_unavailable'],
                'benign_refs': bench_refs,
                'n_benign_refs_traced': sum(1 for b in bench_refs if b in by_tid_meta),
                'record_match': how,
                'record': None,
                'trace': tm,
            }
            if rec is not None:
                row['record'] = {
                    'runtime_seconds': rec.get('runtime_seconds'),
                    'num_events': rec.get('num_events'),
                    'num_tool_calls': rec.get('num_tool_calls'),
                    'num_handoffs': rec.get('num_handoffs'),
                    'num_stages': rec.get('num_stages'),
                    'num_turns': rec.get('num_turns'),
                    'termination_reason': rec.get('termination_reason'),
                    'task_success': rec.get('task_success'),
                    'evaluator_passed': rec.get('evaluator_passed'),
                    'success': rec.get('success'),
                    'error': rec.get('error'),
                    'review_cap_forced_final': rec.get('review_cap_forced_final'),
                    'recovery_detected': rec.get('recovery_detected'),
                    'run_id': rec.get('run_id'),
                    'recorded_at': rec.get('timestamp'),
                }
            out.write(json.dumps(row, separators=(',', ':')) + '\n')

    print('[extract] writing benign reference table ...', flush=True)
    ben_path = os.path.join(OUTDIR, 'benign_reference_operational.jsonl')
    with open(ben_path, 'w') as out:
        for tid in sorted(refs):
            tm = by_tid_meta.get(tid)
            rec, how = record_for(tid)
            row = {
                'trace_id': tid,
                'record_match': how,
                'record': None,
                'trace': tm,
            }
            if rec is not None:
                row['record'] = {
                    'runtime_seconds': rec.get('runtime_seconds'),
                    'num_events': rec.get('num_events'),
                    'num_tool_calls': rec.get('num_tool_calls'),
                    'num_handoffs': rec.get('num_handoffs'),
                    'num_stages': rec.get('num_stages'),
                    'num_turns': rec.get('num_turns'),
                    'termination_reason': rec.get('termination_reason'),
                    'task_success': rec.get('task_success'),
                    'evaluator_passed': rec.get('evaluator_passed'),
                    'success': rec.get('success'),
                    'error': rec.get('error'),
                    'review_cap_forced_final': rec.get('review_cap_forced_final'),
                    'recovery_detected': rec.get('recovery_detected'),
                    'run_id': rec.get('run_id'),
                    'recorded_at': rec.get('timestamp'),
                }
            out.write(json.dumps(row, separators=(',', ':')) + '\n')

    # census
    match_counts = Counter()
    trace_avail = 0
    with open(per_exec_path) as fh:
        for l in fh:
            row = json.loads(l)
            match_counts[row['record_match']] += 1
            trace_avail += 1 if row.get('trace') else 0
    ben_match = Counter()
    with open(ben_path) as fh:
        for l in fh:
            row = json.loads(l)
            ben_match[row['record_match']] += 1
    coverage = {
        'perturbed': len(need),
        'benign_refs': len(refs),
        'perturbed_traced': trace_avail,
        'missing_tids': sorted(missing),
        'dup_cells_resolved': dup_count,
        'record_match_counts_perturbed': dict(match_counts),
        'record_match_counts_benign': dict(ben_match),
        'records_loaded': nrec,
        'distinct_attempts': len(by_attempt),
    }
    with open(os.path.join(OUTDIR, 'extraction_coverage.json'), 'w') as fh:
        json.dump(coverage, fh, indent=2)
    print('[extract] coverage:', json.dumps(coverage, default=str)[:600], flush=True)
    print('[extract] done.', flush=True)


if __name__ == '__main__':
    main()
