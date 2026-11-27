#!/usr/bin/env python3.11
"""V4.4 operational-impact: behavioral-mechanism extraction (structural evidence).

Operationalizes section 9 of the operational-impact contract. Every mechanism is
defined operationally from OBSERVABLE trace evidence only (event counts, tool
multisets, per-role activity, metadata flags, recorded labels). Each mechanism
record carries:
  * structural: the objective trace fact (counts / flags) — reproducible
  * interpretation: the SEMANTIC reading (labeled, NOT asserted) — e.g. "skipped
    verification" is an interpretation; the structural fact is the count.

Topologies have different role inventories (verified against the trace pool):
  branch_and_verify:  researcher/analyst -> (branch) -> verifier (final)
  coordinator_workers: coordinator -> specialist_a/b -> synthesizer (final)
  review_loop:        (inspector|analyst|extractor) -> reviewer
Mechanisms therefore key off the TOPOLOGY-SPECIFIC verification role.

Mechanisms (perturbed value vs matched-benign median, per execution):
  MK1 missing_stage_contribution  final_agent_role or n_stages differs from the
                                   benign median pattern (structural stage-completeness gap).
  MK2 skipped_verification        n_reasoning of the topology's verification role == 0
                                   while benign median > 0 (OR review_cycle_count == 0
                                   when benign median > 0 in review_loop).
  MK3 premature_finalization      n_events < 0.5*benign median AND n_llm_calls <
                                   0.5*benign median (execution ended early relative to its
                                   own benign references).
  MK4 repeated_redundant_tools    repeated_tool_excess > 0 (identical
                                   (role, tool, canonical-args) calls >= 2).
  MK5 unnecessary_handoffs        n_agent_handoffs > benign median + 1.
  MK6 error_driven_retries        n_tool_errors > 0 OR backend_retry_count > 0.
  MK7 reduced_evidence_gathering  evidence-gathering tool calls of the first
                                   worker role (read_text_file/read_file/
                                   list_directory/exec_search) < benign median - 1.
  MK8 increased_effort_no_quality n_llm_calls > benign median AND gradeable AND
                                   M3_observed_class == 'persistent_failure' (the
                                   failure class; "increased effort without improved
                                   quality" — improved-class rows are excluded by
                                   construction, not by absence of success proof).

Output:
  out/mechanism_examples.jsonl      compact per-execution flags (all 16,767)
  out/mechanism_aggregates.json     prevalence by cohort + outcome association
"""
from __future__ import annotations

import json
import os
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out')

# topology -> (verification role, first-worker role, evidence tools)
TOPO_ROLE = {
    'branch_and_verify':   ('verifier',  'researcher',  ('read_text_file', 'read_file', 'list_directory', 'exec_search')),
    'coordinator_workers': ('synthesizer', 'specialist_a', ('read_text_file', 'read_file', 'list_directory', 'exec_search')),
    'review_loop':         ('reviewer',  'inspector',   ('read_text_file', 'read_file', 'list_directory', 'exec_search')),
}
# worker role fallback: for review_loop the first worker is inspector|analyst|extractor —
# evidence is counted across all NON-verification, NON-coordinator roles.


def _med(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    vals = sorted(vals)
    n = len(vals)
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0


def load():
    rows = [json.loads(l) for l in open(os.path.join(OUT, 'operational_impact_per_execution.jsonl')) if l.strip()]
    ben = {json.loads(l)['trace_id']: json.loads(l) for l in
           open(os.path.join(OUT, 'benign_reference_operational.jsonl')) if l.strip()}
    return rows, ben


def tget(row, *path):
    cur = row
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def role_reasoning(row, role):
    return tget(row, 'trace', 'per_role', role, 'n_reasoning') or 0


def worker_evidence_calls(row, topo_hint=None):
    """Evidence-gathering tool calls across worker (non-verification, non-coordinator) roles."""
    topo = topo_hint or row.get('topology') or tget(row, 'trace', 'metadata', 'topology')
    verif, _, evid_tools = TOPO_ROLE.get(topo, ('__none__', 'researcher', ('read_text_file',)))
    ms2 = tget(row, 'trace', 'tool_call_multiset') or {}
    verif_roles = {verif, 'coordinator'}
    total = 0
    for key, cnt in ms2.items():
        if ':' not in key:
            continue
        r_, tool = key.split(':', 1)
        if r_ in verif_roles:
            continue
        if tool in evid_tools:
            total += cnt
    return total


def _flat(row):
    t = row.get('trace') or {}
    md = t.get('metadata') or {}
    row['n_events'] = t.get('n_events')
    row['n_llm_calls'] = t.get('n_reasoning')
    row['n_tool_calls'] = t.get('n_tool_calls')
    row['n_tool_errors'] = t.get('n_tool_errors')
    row['n_agent_handoffs'] = t.get('n_agent_handoffs')
    row['repeated_tool_excess'] = t.get('repeated_tool_call_excess')
    row['backend_retries'] = md.get('backend_retry_count')
    row['n_stages_completed'] = md.get('stage_count')
    row['review_cycles'] = md.get('review_cycle_count')
    row['final_agent_role'] = md.get('final_agent_role')
    row['record_task_success'] = (row.get('record') or {}).get('task_success')
    trust = row.get('runtime_trust')
    rec = row.get('record') or {}
    if trust in ('attempt_id', 'consistent') and rec.get('runtime_seconds') is not None:
        row['latency_s'] = rec['runtime_seconds']
        row['latency_source'] = 'runtime_recorded'
    else:
        row['latency_s'] = t.get('trace_span_s')
        row['latency_source'] = 'trace_span_fallback'
    return row


def build_mechanism_flags(rows, ben):
    MK = ('MK1_missing_stage', 'MK2_skipped_verification', 'MK3_premature_finalization',
          'MK4_repeated_tools', 'MK5_excess_handoffs', 'MK6_error_retries',
          'MK7_reduced_evidence', 'MK8_more_effort_no_quality')
    for r in rows:
        topo = r['topology']
        verif, _worker, _evid = TOPO_ROLE.get(topo, ('__none__', 'researcher', ('read_text_file',)))
        refs = [ben[b] for b in r.get('benign_refs', []) if b in ben and ben[b].get('trace')]
        if not refs:
            r['mechanisms'] = {k: None for k in MK}
            r['n_refs_traced'] = 0
            r['n_refs_total'] = len(r.get('benign_refs', []))
            continue
        r['n_refs_total'] = len(r.get('benign_refs', []))
        r['n_refs_traced'] = len(refs)
        bm = {
            'n_events': _med([tget(x, 'trace', 'n_events') for x in refs]),
            'n_llm': _med([tget(x, 'trace', 'n_reasoning') for x in refs]),
            'n_handoffs': _med([tget(x, 'trace', 'n_agent_handoffs') for x in refs]),
            'stages': _med([tget(x, 'trace', 'metadata', 'stage_count') for x in refs]),
            'verif_llm': _med([role_reasoning(x, verif) for x in refs]) if verif != '__none__' else None,
            'review_cycles': _med([tget(x, 'trace', 'metadata', 'review_cycle_count') for x in refs]),
            'repeated': _med([tget(x, 'trace', 'repeated_tool_excess') for x in refs]),
            'evidence': _med([worker_evidence_calls(x, topo) for x in refs]),
            'latency': _med([x.get('latency_s') for x in refs]),
        }
        # final-role agreement: fraction of refs whose final role matches verif
        bm['final_role_match'] = (
            sum(1 for x in refs if tget(x, 'trace', 'metadata', 'final_agent_role') == verif) / len(refs))

        f = {}
        # MK1 missing stage contribution
        if bm['stages'] is not None and r.get('n_stages_completed') is not None:
            f['MK1_missing_stage'] = bool(r['n_stages_completed'] < bm['stages'])
        else:
            f['MK1_missing_stage'] = None
        # MK2 skipped verification (topology-specific)
        if bm['verif_llm'] is not None:
            f['MK2_skipped_verification'] = (role_reasoning(r, verif) == 0 and bm['verif_llm'] > 0)
        elif bm['review_cycles'] is not None:
            f['MK2_skipped_verification'] = (
                (r.get('review_cycles') or 0) == 0 and bm['review_cycles'] > 0)
        else:
            f['MK2_skipped_verification'] = None
        # MK3 premature finalization
        if bm['n_events'] and bm['n_llm'] and r.get('n_events') is not None and r.get('n_llm_calls') is not None:
            f['MK3_premature_finalization'] = bool(
                r['n_events'] < 0.5 * bm['n_events'] and r['n_llm_calls'] < 0.5 * bm['n_llm'])
        else:
            f['MK3_premature_finalization'] = None
        # MK4 repeated redundant tools
        f['MK4_repeated_tools'] = (r.get('repeated_tool_excess') or 0) > 0
        # MK5 unnecessary handoffs
        if bm['n_handoffs'] is not None and r.get('n_agent_handoffs') is not None:
            f['MK5_excess_handoffs'] = bool(r['n_agent_handoffs'] > bm['n_handoffs'] + 1)
        else:
            f['MK5_excess_handoffs'] = None
        # MK6 error-driven retries
        f['MK6_error_retries'] = bool((r.get('n_tool_errors') or 0) > 0 or (r.get('backend_retries') or 0) > 0)
        # MK7 reduced evidence gathering
        if bm['evidence'] is not None:
            f['MK7_reduced_evidence'] = bool(worker_evidence_calls(r) < bm['evidence'] - 1)
        else:
            f['MK7_reduced_evidence'] = None
        # MK8 increased effort without improved quality
        if bm['n_llm'] and r.get('n_llm_calls') is not None and r.get('gradeable'):
            f['MK8_more_effort_no_quality'] = bool(
                r['n_llm_calls'] > bm['n_llm'] and r.get('M3_observed_class') == 'persistent_failure')
        else:
            f['MK8_more_effort_no_quality'] = None
        r['mechanisms'] = f
    return rows


def cohort(row):
    m1 = row['M1_positive']
    m2 = row['M2_positive']
    return ('neither' if not (m1 or m2) else
            'm1_only' if m1 and not m2 else
            'm2_only' if m2 and not m1 else 'both')


MK = ('MK1_missing_stage', 'MK2_skipped_verification', 'MK3_premature_finalization',
      'MK4_repeated_tools', 'MK5_excess_handoffs', 'MK6_error_retries',
      'MK7_reduced_evidence', 'MK8_more_effort_no_quality')

_DEFINITIONS = {
    'MK1_missing_stage': 'structural: n_stages_completed < matched-benign median',
    'MK2_skipped_verification': 'structural: topology verification role has 0 reasoning (or review_loop: review_cycles 0 vs benign >0); INTERPRETATION: skipped verification',
    'MK3_premature_finalization': 'structural: n_events < 0.5x benign median AND n_llm < 0.5x benign median; INTERPRETATION: early termination',
    'MK4_repeated_tools': 'structural: repeated (role,tool,canonical-args) calls with excess > 0; INTERPRETATION: redundant work',
    'MK5_excess_handoffs': 'structural: handoff count > benign median + 1; INTERPRETATION: unnecessary handoffs',
    'MK6_error_retries': 'structural: tool_errors > 0 or backend_retry_count > 0; INTERPRETATION: error-driven rework (infrastructure OR behavioral)',
    'MK7_reduced_evidence': 'structural: worker-role evidence-gathering tool calls < benign median - 1; INTERPRETATION: reduced evidence gathering',
    'MK8_more_effort_no_quality': 'structural: more LLM calls than benign median while gradeable and M3 observed class == persistent_failure; INTERPRETATION: extra effort without quality',
}


def select_examples(rows, per_exec):
    """Deterministic representative + counterexample selection (contract §9).

    No cherry-picking: fixed seed, stratified by (cohort × LEP), 2 examples per
    stratum for the dominant-trend cells, and the same procedure applied to
    the CONTRA-trend cells so counterexamples are selected with identical rules.
    Every selected row carries a stable trace_id.
    """
    import random
    rng = random.Random(20270301)  # pinned seed
    per_exec_by_tid = {r['trace_id']: r for r in per_exec}
    # rel/diff live on the enriched per-execution rows; cohort is computed here
    def rel_of(r):  return per_exec_by_tid.get(r['trace_id'], {}).get('rel_wall_clock_s')
    def diff_of(r): return per_exec_by_tid.get(r['trace_id'], {}).get('diff_wall_clock_s')
    def diffllm_of(r): return per_exec_by_tid.get(r['trace_id'], {}).get('diff_n_llm_calls')
    def repex_of(r):
        tr = (per_exec_by_tid.get(r['trace_id'], {}).get('trace') or {})
        return tr.get('repeated_tool_call_excess', 0)

    def pick(pop, n, key):
        s = sorted([r for r in pop if rel_of(r) is not None], key=lambda r: key(r))
        out = []
        strata = defaultdict(list)
        for r in s:
            strata[(cohort(r), r['lep_code'])].append(r)
        for sk in sorted(strata):
            out.append(strata[sk][0])
        extra = [r for r in s if r not in out]
        rng.shuffle(extra)
        out += extra[: max(0, n - len(out))]
        return out[: n]

    examples = []

    def emit(tag, group, n, sortkey, note, relgate=None):
        sel = pick(group, n, sortkey)
        for r in sel:
            examples.append({
                'tag': tag,
                'execution_id': r['execution_id'],
                'trace_id': r['trace_id'],
                'fixture_id': r['fixture_id'],
                'topology': r['topology'],
                'task_family': r['task_family'],
                'lep_code': r['lep_code'],
                'cohort': cohort(r),
                'M1_positive': r['M1_positive'],
                'M2_positive': r['M2_positive'],
                'M3_observed_class': r.get('M3_observed_class'),
                'gradeable': r.get('gradeable'),
                'rel_wall_clock_s': rel_of(r),
                'diff_wall_clock_s': diff_of(r),
                'diff_n_llm_calls': diffllm_of(r),
                'repeated_tool_excess': repex_of(r),
                'n_events': r.get('n_events'),
                'n_llm_calls': r.get('n_llm_calls'),
                'record_task_success': r.get('record_task_success'),
                'runtime_trust': r.get('runtime_trust'),
                'mechanisms': r['mechanisms'],
                'note': note,
            })

    ge = [r for r in rows if r['gradeable']]
    def deg(r): return r['M3_matched_degraded_observed']
    def fast(r): return rel_of(r) is not None and rel_of(r) < -0.10
    def slow(r): return rel_of(r) is not None and rel_of(r) > 0.10
    # DOMINANT-TREND cell: faster AND degraded (contract §6 cell 3)
    emit('faster_degraded', [r for r in ge if fast(r) and deg(r)], 8,
         lambda r: rel_of(r),
         'faster than benign median AND observed matched degradation; inspect MK2/MK3/MK7 structural support')
    # COUNTEREXAMPLE to "fast => work omitted"
    emit('faster_not_degraded_counterexample', [r for r in ge if fast(r) and not deg(r)], 8,
         lambda r: rel_of(r),
         'COUNTEREXAMPLE to fast=>omitted: faster with NO observed degradation')
    # naive "wasted effort" cell
    emit('slower_degraded', [r for r in ge if slow(r) and deg(r)], 8,
         lambda r: rel_of(r),
         'slower AND degraded; check MK4/MK8/MK6 for the added effort')
    # COUNTEREXAMPLE to "slow => recovery": slower + more LLM + not degraded
    emit('slower_not_degraded_more_llm',
         [r for r in rows if slow(r) and not deg(r) and (diffllm_of(r) or 0) > 0], 8,
         lambda r: -rel_of(r),
         'slower + more LLM calls + NOT degraded; extra effort, no quality gain')
    # dominant structural mechanism MK4
    emit('MK4_repeated_tools_examples', [r for r in rows if r['mechanisms']['MK4_repeated_tools']], 8,
         lambda r: -repex_of(r),
         'structural redundancy (identical role+tool+args repeated)')
    return examples


def main():
    rows, ben = load()
    rows = [_flat(r) for r in rows]
    for b in ben.values():
        _flat(b)
    rows = build_mechanism_flags(rows, ben)
    per_exec = [json.loads(l) for l in open(os.path.join(OUT, 'operational_impact_per_execution.jsonl')) if l.strip()]

    mech_lines = []
    for r in rows:
        out = {
            'execution_id': r['execution_id'],
            'trace_id': r['trace_id'],
            'fixture_id': r['fixture_id'],
            'topology': r['topology'],
            'task_family': r['task_family'],
            'lep_code': r['lep_code'],
            'cohort': cohort(r),
            'M1_positive': r['M1_positive'],
            'M2_positive': r['M2_positive'],
            'M3_observed_class': r.get('M3_observed_class'),
            'M3_matched_degraded_observed': r['M3_matched_degraded_observed'],
            'gradeable': r['gradeable'],
            'record_task_success': r.get('record_task_success'),
            'latency_s': r.get('latency_s'),
            'n_events': r.get('n_events'),
            'n_llm_calls': r.get('n_llm_calls'),
            'n_refs_traced': r.get('n_refs_traced'),
            'mechanisms': r['mechanisms'],
        }
        mech_lines.append(json.dumps(out, separators=(',', ':')))
    with open(os.path.join(OUT, 'mechanism_examples.jsonl'), 'w') as fh:
        for line in mech_lines:
            fh.write(line + '\n')
    # canonical top-level deliverable (contract §11 file name)
    with open(os.path.join(HERE, 'mechanism_examples.jsonl'), 'w') as fh:
        for line in mech_lines:
            fh.write(line + '\n')

    examples = select_examples(rows, per_exec)
    ex_lines = [json.dumps(ex, separators=(',', ':')) for ex in examples]
    with open(os.path.join(OUT, 'mechanism_example_selection.jsonl'), 'w') as fh:
        for line in ex_lines:
            fh.write(line + '\n')
    with open(os.path.join(HERE, 'mechanism_example_selection.jsonl'), 'w') as fh:
        for line in ex_lines:
            fh.write(line + '\n')

    agg = {'mechanism_definitions': _DEFINITIONS,
           'topology_role_map': TOPO_ROLE,
           'prevalence_by_cohort': {}, 'association_with_outcome': {},
           'interpretation_caveat': ('structural facts only; the semantic labels are interpretations '
                                     'and must be read with the evidence fields')}
    by_coh = defaultdict(list)
    for r in rows:
        by_coh[cohort(r)].append(r)
    for c, crs in by_coh.items():
        pre = {}
        for k in MK:
            vals = [r['mechanisms'][k] for r in crs]
            tv = [v for v in vals if v is not None]
            pre[k] = {'n_flagged': sum(1 for v in tv if v), 'n_evaluable': len(tv),
                      'prevalence': (sum(1 for v in tv if v) / len(tv)) if tv else None}
        agg['prevalence_by_cohort'][c] = {'n': len(crs), **pre}

    g = [r for r in rows if r['gradeable']]
    deg = [r for r in g if r['M3_matched_degraded_observed']]
    ok = [r for r in g if not r['M3_matched_degraded_observed']]
    for grp, label in ((g, 'gradeable_all'), (deg, 'M3_degraded'), (ok, 'M3_not_degraded')):
        out = {}
        for k in MK:
            tv = [r['mechanisms'][k] for r in grp if r['mechanisms'][k] is not None]
            out[k] = {'n_flagged': sum(1 for v in tv if v),
                      'prevalence': (sum(1 for v in tv if v) / len(tv)) if tv else None}
        agg['association_with_outcome'][label] = {'n': len(grp), **out}
    # recorded task_success is usable only where the matched record describes
    # the measured execution (runtime_trust in {attempt_id, consistent})
    withrec = [r for r in g
               if r.get('runtime_trust') in ('attempt_id', 'consistent')
               and isinstance(r.get('record_task_success'), bool)]
    deg_t = [r for r in deg if r.get('runtime_trust') in ('attempt_id', 'consistent')
             and isinstance(r.get('record_task_success'), bool)]
    ok_t = [r for r in ok if r.get('runtime_trust') in ('attempt_id', 'consistent')
            and isinstance(r.get('record_task_success'), bool)]
    agg['association_with_outcome']['recorded_task_success_gradeable'] = {
        'n_valid': len(withrec),
        'rate': (sum(1 for r in withrec if r['record_task_success']) / len(withrec)) if withrec else None,
        'M3_degraded_subset': _ts_rate(deg_t),
        'M3_not_degraded_subset': _ts_rate(ok_t),
        'note': 'record.task_success on TRUSTED records only (failed-harness-attempt records excluded)',
    }
    with open(os.path.join(OUT, 'mechanism_aggregates.json'), 'w') as fh:
        json.dump(agg, fh, indent=2)
    print('mechanism prevalence by cohort:')
    for c, v in agg['prevalence_by_cohort'].items():
        print('  %-8s %s' % (c, {k: (None if v[k]['prevalence'] is None else round(v[k]['prevalence'], 4)) for k in MK}))
    print('association:')
    print(json.dumps(agg['association_with_outcome']['recorded_task_success_gradeable'], indent=1))


def _ts_rate(subset):
    return {'n_valid': len(subset),
            'rate': (sum(1 for r in subset if r['record_task_success']) / len(subset)) if subset else None}


if __name__ == '__main__':
    main()
