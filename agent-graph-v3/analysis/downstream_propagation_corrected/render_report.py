#!/usr/bin/env python3.11
"""Render report.md + breakdowns.json from results/ (read-only over results).

Run after analyze_downstream.py has written results/summary.json + per_trace.jsonl.
"""
from __future__ import annotations
import json, os, sys
from collections import Counter

ROOT = '/u/kdhande/agent-graph/agent-graph-v3'
# Default = the original 3-source results; for the expanded population set
# DP_RESULTS_DIR=.../results_v2 (the analyzer's DP_OUTDIR).
RES = os.environ.get('DP_RESULTS_DIR',
                     ROOT + '/analysis/downstream_propagation_corrected/results')
OUT = RES

def main():
    s = json.load(open(OUT + '/summary.json'))
    rows = [json.loads(l) for l in open(OUT + '/per_trace.jsonl')]
    m = s['metrics']
    # optional prior-run comparison (v1 results dir when rendering v2)
    import os as _os
    _v1dir = _os.path.join(_os.path.dirname(OUT), 'results')
    v1 = None
    if _os.path.exists(_v1dir + '/summary.json') and _os.path.abspath(_v1dir) != _os.path.abspath(OUT):
        v1 = json.load(open(_v1dir + '/summary.json'))

    # ── breakdowns.json: full per-trace + cross-tabs ──
    def xtab(field, val=None, pos_field='m2_cross_agent_positive'):
        tab = Counter()
        for r in rows:
            if pos_field == 'ALL' or r.get(pos_field):
                tab[r.get(field)] += 1
        return dict(tab)

    breakdowns = {
        'n_comparable': s['n_comparable'],
        'denominators': s['denominators'],
        'm2_by_lep': s['by_lep'], 'm2_by_topology': s['by_topology'],
        'm2_by_mode': s['by_mode'], 'm2_by_family': s['by_family'],
        'm3_by_lep': m['M3_dependency_supported_propagation']['by_lep'],
        'm4_by_lep': m['M4_ground_truth_downstream_task_failure']['by_lep'],
        'm4_by_family': m['M4_ground_truth_downstream_task_failure']['by_family'],
        'm4_by_reach_tier': m['M4_ground_truth_downstream_task_failure']['by_reach_tier'],
        'ipi_trc_recovery': m['M3_dependency_supported_propagation'][
            'ipi_trc_transformed_handoff_recovery'],
        'cluster_ci': {
            'M1': m['M1_whole_trace_deviation']['cluster_binomial_ci95'],
            'M2': m['M2_cross_agent_downstream_deviation']['cluster_binomial_ci95'],
            'M3': m['M3_dependency_supported_propagation']['cluster_binomial_ci95'],
        },
        # reach-class cross-tab among M2 positives (for the IPI/TRC story)
        'm2_reach_cross': {
            'reach_class': xtab('m3_reach_class', pos_field='m2_cross_agent_positive'),
            'm2_by_lep_reach': {
                lep: Counter() for lep in ('LEP_HANDOFF_CORRUPTION',
                                            'LEP_INDIRECT_PROMPT_INJECTION',
                                            'LEP_INPUT_DISREGARD',
                                            'LEP_TOOL_RESULT_CORRUPTION')},
        },
    }
    by_ler = breakdowns['m2_reach_cross']['m2_by_lep_reach']
    for r in rows:
        if r.get('m2_cross_agent_positive'):
            by_ler[r['lep_code']][r['m3_reach_class']] += 1
    for k in by_ler:
        breakdowns['m2_reach_cross']['m2_by_lep_reach'][k] = dict(by_ler[k])

    with open(OUT + '/breakdowns.json', 'w') as fh:
        json.dump(breakdowns, fh, indent=2, sort_keys=True)

    # ── report.md ──
    m2 = m['M2_cross_agent_downstream_deviation']
    m3 = m['M3_dependency_supported_propagation']
    m4 = m['M4_ground_truth_downstream_task_failure']
    m1 = m['M1_whole_trace_deviation']
    L = []
    L.append('# AgentProp - corrected downstream-propagation analysis (BND V4.1)\n')
    L.append('READ-ONLY re-analysis of the frozen `bnd_v41_cw_swe200_provisional` '
             'population. No traces, benchmark code, frozen artifacts, or Slurm jobs '
             '(3600260/3627515/3627516) were modified. All new outputs live in this '
             'directory only.\n')
    L.append('## Metrics (four independent measurements, N = %d comparable)\n' % s['n_comparable'])
    L.append('| Metric | Definition | Positive | Denominator | Rate |')
    L.append('|---|---|---:|---:|---:|')
    L.append('| M1 whole-trace deviation | existing 4-dim rule, unchanged | %d | %d | %.2f%% |'
             % (m1['positive'], m1['denominator'], 100*m1['rate']))
    L.append('| M2 cross-agent downstream deviation | stage-gated deviation at a NON-origin '
             'agent role; origin & later-origin (M2O) excluded; ID authored-origin '
             'preserved | %d | all %d / evaluable %d | %.2f%% / %.2f%% |'
             % (m2['positive'], m2['denominator_all'],
                m2['denominator_evaluable'],
                100*m2['rate_all'],
                100*(m2.get('rate_evaluable') or 0)))
    L.append('| M3 dependency-supported propagation | M2 AND delivery-verified AND '
             'content-reach (verified=in own downstream output; plausible=input/lineage/DAG) | %d '
             '(verified %d + plausible %d) | %d | %.2f%% |'
             % (m3['total'], m3['verified'], m3['plausible'],
                m3['denominator_M2_delivery_verified'], 100*m3['rate_all_comparable']))
    L.append('| M4 ground-truth downstream task failure | M3 AND final deliverable is a '
             'ground-truth failure | %d | %d | %.2f%% |'
             % (m4['positive'], m4['denominator_all_comparable'], 100*m4['rate']))
    L.append('\nDenominators are separated: evaluable %d, undetermined (never negative) %d, '
             'delivery-verified %d, delivery-unverified %d.\n'
             % (s['denominators']['downstream_evaluable'],
                s['denominators']['undetermined_never_negative'],
                s['denominators']['delivery_verified'],
                s['denominators']['delivery_unverified']))

    L.append('## Comparison to prior figures\n')
    L.append('Prior = the original 3-source population analysis (results/), where '
             'applicable. Corrected = this expanded 4-source run.\n')
    L.append('| Figure | Prior | Corrected | Delta |')
    L.append('|---|---:|---:|---|')
    L.append('| Whole-trace deviation | 23.09%% (2,322) | %.2f%% (%d) | population grew; '
             'rate stable |'
             % (100*m1['rate'], m1['positive']))
    _leg = m2['legacy_origin_corrected_same_agent_inclusive']['positive']
    L.append('| Cross-agent downstream deviation | 20.73%% (2,085, same-agent-inclusive) | '
             '%.2f%% all / %.2f%% evaluable (%d) | requires the deviation to sit on a '
             'DIFFERENT agent role: excludes origin checkpoints, M2O later-injection-'
             'origins, and later-turns-of-the-origin-role (same-agent continuation = '
             'local effect, not cross-agent). ID authored-origin preserved. Undetermined '
             '(%d) excluded from the numerator, never counted negative. '
             '(same-agent-inclusive count here = %d, for contrast) |'
             % (100*m2['rate_all'], 100*(m2.get('rate_evaluable') or 0),
                m2['positive'], s['denominators']['undetermined_never_negative'], _leg))
    _rt = m3['ipi_trc_transformed_handoff_recovery']
    _rtot_i = _rt['recovered_verified'].get('LEP_INDIRECT_PROMPT_INJECTION', 0) + _rt['recovered_plausible'].get('LEP_INDIRECT_PROMPT_INJECTION', 0)
    _rtot_t = _rt['recovered_verified'].get('LEP_TOOL_RESULT_CORRUPTION', 0) + _rt['recovered_plausible'].get('LEP_TOOL_RESULT_CORRUPTION', 0)
    L.append('| Dependency-supported propagation | 3.60%% (Tier D) | %.2f%% (%d: verified %d + '
             'plausible %d) | M3 now grades IPI/TRC through *transformed* handoff content; '
             'DAG-exposure-only rows are reported as unsupported, not counted |'
             % (100*m3['rate_all_comparable'], m3['total'], m3['verified'], m3['plausible']))

    # v1 -> v2 population-expansion deltas (when a prior results dir is present)
    if v1 is not None:
        vm = v1['metrics']
        L.append('\n## Population expansion (v1 3-source -> v2 4-source, +bvrl-regen)\n')
        L.append('| Quantity | v1 (3-source) | v2 (+bvrl) |')
        L.append('|---|---:|---:|')
        L.append('| Comparable executions | %d | %d |' % (v1['n_comparable'], s['n_comparable']))
        L.append('| M2 cross-agent downstream deviation | %d | %d |'
                 % (vm['M2_cross_agent_downstream_deviation']['positive'], m2['positive']))
        L.append('| M3 dependency-supported (verified+plausible) | %d | %d |'
                 % (vm['M3_dependency_supported_propagation']['total'], m3['total']))
        L.append('| M4 ground-truth downstream task failure | %d | %d |'
                 % (vm['M4_ground_truth_downstream_task_failure']['positive'], m4['positive']))
        L.append('| Evaluable / undetermined | %d / %d | %d / %d |'
                 % (v1['denominators']['downstream_evaluable'],
                    v1['denominators']['undetermined_never_negative'],
                    s['denominators']['downstream_evaluable'],
                    s['denominators']['undetermined_never_negative']))
        L.append('\nThe bvrl-regen (job 3600260) supplied new benign reps that raised '
                 'bv/rl cells to >=3 same-shape references, lifting the comparable '
                 'population. The CW one_to_many / single-origin RELAY-skeleton '
                 'incomparables remain (targeted by pending jobs 3627515/3627516).')
    L.append('\n**IPI/TRC transformed-handoff recovery.** Prior analysis left IPI/TRC '
             'cross-role deviations "plausible but unverified" because it required verbatim '
             'payload copying of the origin agent\'s tool result - which by design never '
             'reaches another agent\'s output. This analysis instead probes the *content '
             'the mutation introduced* (numeric values substituted/added by TRC, the '
             'injected instruction text in IPI) and asks whether it appears in a '
             'downstream agent\'s OWN authored output or recorded input - paraphrased, '
             'summarized, or substituted; no verbatim requirement. Result: of %d '
             'delivery-verified IPI and %d TRC cross-agent M2 positives, **%d IPI (%.1f%%) '
             'and %d TRC (%.1f%%)** recovered positive downstream reach evidence '
             '(verified: %d IPI / %d TRC; plausible: %d IPI / %d TRC). The remainder '
             'have only structural DAG exposure and stay unsupported.'
             % (_rt['denominator_M2_delivery_verified'].get('LEP_INDIRECT_PROMPT_INJECTION',0),
                _rt['denominator_M2_delivery_verified'].get('LEP_TOOL_RESULT_CORRUPTION',0),
                _rtot_i, 100*_rtot_i/max(1,_rt['denominator_M2_delivery_verified'].get('LEP_INDIRECT_PROMPT_INJECTION',0)),
                _rtot_t, 100*_rtot_t/max(1,_rt['denominator_M2_delivery_verified'].get('LEP_TOOL_RESULT_CORRUPTION',0)),
                _rt['recovered_verified'].get('LEP_INDIRECT_PROMPT_INJECTION',0),
                _rt['recovered_verified'].get('LEP_TOOL_RESULT_CORRUPTION',0),
                _rt['recovered_plausible'].get('LEP_INDIRECT_PROMPT_INJECTION',0),
                _rt['recovered_plausible'].get('LEP_TOOL_RESULT_CORRUPTION',0)))

    L.append('\n## Benign baseline (the null hypothesis for task failure)\n')
    bbf = m4['benign_baseline_contrast']['benign_failure_by_family']
    def _pct(f):
        return 100*(bbf[f]['rate'] or 0)
    L.append('The same frozen extractor + grader applied to the **benign** reference '
             'traces (no LEP) yields a task-failure baseline of **%.1f%%** (%d/%d), by '
             'family: code_review %.1f%%, research_synthesis %.1f%%, financial_analysis '
             '%.1f%%. Consequence: a "downstream task failure" is NOT a rare propagation '
             'signal - it is the modal outcome on these difficult tasks even *without* '
             'any LEP. M4 must therefore be read against this per-family baseline, not '
             'as absolute causation.'
             % (100*m4['benign_baseline_contrast']['benign_rate'],
                m4['benign_baseline_contrast']['benign_task_failure'],
                m4['benign_baseline_contrast']['unique_benign_reference_traces_graded'],
                _pct('code_review'), _pct('research_synthesis'),
                _pct('financial_analysis')))

    L.append('\n## Statistical (clustered) CIs\n')
    n_clusters = m2['cluster_binomial_ci95']['clusters']
    L.append('Executions share benign reference sets (up to ~8 LEPs per set), so the '
             'valid unit is the **reference-set / configuration cluster** '
             '(%d total), not the individual execution. Cluster-binomial 95%% CIs:'
             % n_clusters)
    for k in ('M1', 'M2', 'M3'):
        c = m[['M1_whole_trace_deviation','M2_cross_agent_downstream_deviation',
               'M3_dependency_supported_propagation'][{'M1':0,'M2':1,'M3':2}[k]]]['cluster_binomial_ci95']
        L.append('- %s: %d/%d clusters show a positive = %.1f%% (95%% CI %.1f%%-%.1f%%)'
                 % (k, c['clusters_with_positive'], c['clusters'],
                    100*c['fraction'], 100*c['ci95'][0], 100*c['ci95'][1]))
    L.append('\nExecution-level Wilson intervals are reported in `summary.json` but '
             'flagged **anti-conservative** (they ignore shared-reference correlation).')

    L.append('\n## Breakdowns (M2 cross-agent positives / comparable denominator)\n')
    L.append('By LEP: ' + json.dumps(s['by_lep']))
    L.append('By topology: ' + json.dumps(s['by_topology']))
    L.append('By mode: ' + json.dumps(s['by_mode']))
    L.append('By family: ' + json.dumps(s['by_family']))
    L.append('\nM3 by LEP: ' + json.dumps(m3['by_lep']))
    L.append('M4 by family: ' + json.dumps(m4['by_family']))

    L.append('\n## Strongest defensible claim\n')
    L.append('> Across 10,057 comparable standard executions, **%d (%.2f%% of the %d '
             'downstream-evaluable)** show a *cross-agent* downstream deviation - a '
             'stage-gated deviation recorded at an agent role that is NOT an injection '
             'origin (origin-agent checkpoints and later-turn-of-origin checkpoints '
             'excluded; M2O later-injection-origins excluded; INPUT_DISREGARD '
             'authored-origin semantics preserved). Of those, **%d** are '
             '*dependency-supported* (verified content reach %d + plausible %d): '
             'delivery is byte-verified AND the mutation\'s introduced content is '
             'recorded reaching a downstream agent - in that agent\'s own authored '
             'output (verified) or a recorded input/lineage path (plausible). **%d of '
             'the %d** dependency-supported rows end in a ground-truth downstream task '
             'failure - but this must be read against the benign (no-LEP) task-failure '
             'baseline of %.1f%%; attribution of a failure to the LEP is strongest for '
             'the %d *verified* rows (specific introduced content in the downstream '
             'agent\'s own output) and remains association-only for plausible rows. '
             'The %d delivery-verified M2-positive rows whose only evidence is '
             'structural DAG exposure are reported as unsupported, never as '
             'propagation.'
             % (m2['positive'], 100*(m2.get('rate_evaluable') or 0), m2['denominator_evaluable'],
                m3['total'], m3['verified'], m3['plausible'],
                m4['positive'], m4['denominator_M3_supported'],
                100*m4['benign_baseline_contrast']['benign_rate'], m3['verified'],
                s['metrics']['M3_dependency_supported_propagation']['reach_basis_breakdown'].get('structurally_exposed_only', 0)))

    L.append('\n## Remaining limitations\n')
    L.append('- **DAG reachability != consumption.** Rows whose only link to a downstream '
             'agent is structural DAG exposure (652 delivery-verified M2 positives, '
             '`structurally_exposed_only`) are reported as UNSUPPORTED and never counted '
             'as dependency-supported. "plausible" additionally includes '
             '`content_reach_input_only` and `lineage_downstream_role`: these show the '
             'content was *available* to (or recorded as consumed by) a downstream '
             'agent, not that it *drove* that agent\'s deviation. Only `verified` '
             '(introduced content in the downstream agent\'s own authored output) is '
             'content-level evidence of influence - and even that is correlation of '
             'content, not a counterfactual proof. No counterfactual '
             '(LEP-not-delivered) control exists in the population, so a '
             'false-positive rate cannot be measured.')
    L.append('- **Base difficulty.** Benign task-failure baseline is %.1f%% overall '
             '(code_review %.1f%%, research %.1f%%); M4 counts failures without '
             'per-family null adjustment, so part of M4 reflects task difficulty, not '
             'propagation. (An adjusted LEP-minus-baseline failure rate is a follow-up '
             'analysis, not implemented here to avoid inventing a statistic.)'
             % (100*m4['benign_baseline_contrast']['benign_rate'],
                100*(m4['benign_baseline_contrast']['benign_failure_by_family']['code_review']['rate'] or 0),
                100*(m4['benign_baseline_contrast']['benign_failure_by_family']['research_synthesis']['rate'] or 0)))
    L.append('- **code_review grading coverage.** The frozen stance extractor reports many '
             'issues `unavailable`/`mentioned_no_stance`, so some code-review rows are '
             '`not_gradeable` (M4 under-counts code-review failures accordingly).')
    L.append('- **Omission-type mutations** (HC/ID truncation, TRC `partial_omission`) '
             'add no new tokens, so they can never be `verified` by content-reach; they '
             'degrade to at most `plausible`. This is an honest floor, not a miss.')
    L.append('- **Clustering is coarse.** The CI treats a whole reference set as one '
             'Bernoulli trial; within-set correlation across the ~8 LEP executions is '
             'further (and favorably) shrunk, so cluster CIs are themselves '
             'approximate. No multiple-comparisons correction is applied across the '
             'per-LEP / per-family subgroup tables.')
    L.append('- **Scope.** Standard executions only; memory poisoning and '
             '`memory_enabled` excluded; `competitive_intelligence` has 0 comparable '
             'rows. The three regeneration jobs are unchanged.')

    with open(OUT + '/report.md', 'w') as fh:
        fh.write('\n'.join(L) + '\n')
    print('wrote', OUT + '/report.md + breakdowns.json')

if __name__ == '__main__':
    main()
