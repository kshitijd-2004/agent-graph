#!/usr/bin/env python3.11
"""Publication-ready figures for the V4.4 operational-impact analysis.

Reads operational_impact_aggregates.json + out/*.jsonl; writes out/figs/*.png
(deterministic: fixed matplotlib rcParams; no random sampling in this script).

Figure list (contract §11):
  fig1_matched_wallclock_by_cohort.png    matched wall-clock change (diff & sign) by the 4 cohorts
  fig2_latency_change_distribution.png    latency-change distributions + tail behavior
  fig3_resource_consumption_by_cohort.png resource changes (LLM calls, tool calls, events, char proxy)
  fig4_cost_vs_degradation.png            cost/quality tradeoff categories
  fig5_operational_effects_family_lep.png effects across task families, LEPs, topologies
  fig6_mechanism_comparisons.png          workflow-completeness & mechanism prevalence

CIs shown are fixture-cluster bootstrap CIs (SEED=20270301, N_BOOT=4000),
carried in operational_impact_aggregates.json; where a CI is None the figure
shows the point estimate only and the caption/notes say so.
"""
import json
import os
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out')
FIG = os.path.join(OUT, 'figs')
os.makedirs(FIG, exist_ok=True)

plt.rcParams.update({'figure.dpi': 150, 'font.size': 9, 'axes.grid': True,
                     'grid.alpha': 0.3, 'legend.framealpha': 0.9,
                     'errorbar.capsize': 2})

A = json.load(open(os.path.join(HERE, 'operational_impact_aggregates.json')))
COHORTS = ['neither', 'm1_only', 'm2_only', 'both']
CLAB = {'neither': '#4d4d4d', 'm1_only': '#1f77b4', 'm2_only': '#ff7f0e',
        'both': '#d62728'}
CCAT = {'neither': 'M1− M2−', 'm1_only': 'M1 only',
        'm2_only': 'M2 only', 'both': 'M1∧M2'}


def _bar_ci(ax, names, vals, cis):
    """bar + errorbar (skip where CI is None)."""
    ax.bar(names, vals, color='#4c72b0', alpha=0.85)
    for i, (v, c) in enumerate(zip(vals, cis)):
        if c and c[0] is not None and c[1] is not None:
            ax.errorbar([i], [v], yerr=[[v - c[0]], [c[1] - v]], fmt='none',
                        color='#333', ecolor='#666', capsize=4, lw=1.2)


def _fr(v):
    return 'n/a' if v is None else f'{v:.2f}'


# ── Fig 1: matched wall-clock change by cohort ─────────────────────────────
def fig1():
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    med, lo, hi = [], [], []
    for c in COHORTS:
        m = A['cohorts'][c]['matched']['wall_clock_s']
        d = m['diff_dist']
        ci = m.get('diff_ci')
        med.append(d['median'])
        lo.append(ci[0] if ci and ci[0] is not None else d['median'])
        hi.append(ci[1] if ci and ci[1] is not None else d['median'])
    ax = axes[0]
    ax.errorbar(range(4), med, yerr=[np.array(med) - np.array(lo),
                                      np.array(hi) - np.array(med)],
                fmt='o', ms=9, color='#333', ecolor='#888', capsize=4)
    ax.set_ylim(min(lo) - 0.25, max(hi) + 0.9)
    for i, c in enumerate(COHORTS):
        d = A['cohorts'][c]['matched']['wall_clock_s']['diff_dist']
        ci = A['cohorts'][c]['matched']['wall_clock_s'].get('diff_ci')
        top = ci[1] if ci and ci[1] is not None else hi[i]
        ax.text(i, top + 0.08,
                f"median {med[i]:+.2f} s\nn={d['n_valid']}",
                ha='center', fontsize=8, color='#555')
    ax.axhline(0, color='k', lw=0.8, ls='--')
    ax.set_xticks(range(4), [CCAT[c] for c in COHORTS])
    ax.set_ylabel('Wall-clock Δ vs matched-benign median (s)')
    ax.set_title('a) Matched absolute latency change\n(cohort medians, 95% fixture-cluster CI)')

    ax = axes[1]
    pos = [A['cohorts'][c]['matched']['wall_clock_s']['frac_faster'] for c in COHORTS]
    un = [A['cohorts'][c]['matched']['wall_clock_s']['frac_unchanged'] for c in COHORTS]
    neg = [A['cohorts'][c]['matched']['wall_clock_s']['frac_slower'] for c in COHORTS]
    x = np.arange(4)
    ax.bar(x - 0.3, pos, width=0.3, label='faster', color='#2a9d8f')
    ax.bar(x, un, width=0.3, label='exactly equal (0.0 s)', color='#cccccc')
    ax.bar(x + 0.3, neg, width=0.3, label='slower', color='#e76f51')
    ax.set_xticks(x, [CCAT[c] for c in COHORTS])
    ax.set_ylabel('Fraction of executions')
    ax.set_ylim(0, 1.05)
    ax.legend(loc='upper right', fontsize=7.5)
    ax.set_title('b) Latency-change sign vs benign median — no directionality (≈50/50 every cohort).\n'
                 '(The ±10% “unchanged band” cell is reported in fig4, not here.)')
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, 'fig1_matched_wallclock_by_cohort.png'))
    plt.close(fig)
    print('wrote fig1')


# ── Fig 2: latency-change distributions + tails ─────────────────────────────
def fig2():
    rows = [json.loads(l) for l in open(os.path.join(OUT, 'operational_impact_per_execution.jsonl')) if l.strip()]
    diffs = np.array([r['diff_wall_clock_s'] for r in rows if r.get('diff_wall_clock_s') is not None])
    rels = np.array([r['rel_wall_clock_s'] for r in rows if r.get('rel_wall_clock_s') is not None])
    fig, axes = plt.subplots(1, 3, figsize=(13, 4))
    ax = axes[0]
    ax.hist(np.clip(diffs, -120, 120), bins=90, color='#4c72b0', alpha=0.85)
    ax.set_xlabel('Wall-clock Δ vs benign median (s), clipped ±120 s')
    ax.set_title(f'a) Distribution (n={len(diffs)}); median {np.median(diffs):+.2f} s, '
                 f'50th-pct of |Δ| is {np.median(np.abs(diffs)):.2f} s')
    ax = axes[1]
    ax.hist(np.clip(rels, -1, 3), bins=140, color='#55a868', alpha=0.85)
    ax.set_xlabel('Relative Δ (×100%, clipped −100…300%)')
    ax.set_title(f'b) Relative change; P95 = {np.percentile(rels, 95):+.0f}%, '
                 f'P99 = {np.percentile(rels, 99):+.0f}%')
    ax = axes[2]
    for q, lab, col in ((0.90, 'P90', '#888'), (0.95, 'P95', '#c49a3c'),
                        (0.99, 'P99', '#d62728'), (0.999, 'P99.9', '#7f4fbf')):
        v = np.percentile(diffs, q * 100)
        ax.axvline(v, color=col, ls='--', lw=1.3, label=f'{lab} = {v:+.0f} s')
    ax.hist(np.clip(diffs, -350, 560), bins=160, color='#333', alpha=0.25)
    ax.set_xlabel('Wall-clock Δ (s), full range')
    ax.legend(fontsize=8, loc='center left')
    ax.set_title('c) Tail cuts on the full (unclipped) change')
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, 'fig2_latency_change_distribution.png'))
    plt.close(fig)
    print('wrote fig2')


# ── Fig 3: resource consumption by cohort ───────────────────────────────────
def fig3():
    fig, axes = plt.subplots(2, 2, figsize=(11, 7.5))
    keys = [('n_llm_calls', 'LLM calls'), ('n_tool_calls', 'Tool calls'),
            ('n_events', 'Execution events'), ('chars_out_proxy', 'Output chars (proxy)')]
    for ax, (k, lab) in zip(axes.flat, keys):
        for i, c in enumerate(COHORTS):
            mabs = A['cohorts'][c]['metrics'][k]
            md = A['cohorts'][c]['matched'].get(k)
            av = mabs['median'] if mabs else None
            dv = md['diff_dist']['median'] if md and md.get('diff_dist') else None
            if av is not None:
                ax.scatter(i, av, marker='o', s=42, facecolor='white',
                           edgecolor=CLAB[c], zorder=3, lw=1.4)
            if dv is not None:
                ax.scatter(i, dv, marker='D', s=62, color=CLAB[c], zorder=4)
        hdl = [Line2D([0], [0], marker='D', ls='', color='#333', label='Δ vs benign median'),
               Line2D([0], [0], marker='o', mfc='w', mec='#333', ls='', label='absolute cohort median')]
        ax.legend(handles=hdl, fontsize=7.5, loc='lower right')
        ax.axhline(0, color='k', lw=0.7, ls='--')
        ax.set_xticks(range(4), [CCAT[c] for c in COHORTS])
        ax.set_ylabel(lab)
        ax.set_title(lab + (' — char proxy, NOT token counts' if 'proxy' in lab else ''))
    fig.suptitle('Resource consumption: absolute level (○) vs matched-benign change (◆), by cohort',
                 y=0.995)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, 'fig3_resource_consumption_by_cohort.png'))
    plt.close(fig)
    print('wrote fig3')


# ── Fig 4: cost/quality tradeoff categories ────────────────────────────────
def fig4():
    tr = A['tradeoffs']['wall_clock']
    cats = ['faster_degraded', 'faster_ok', 'unchanged_degraded', 'unchanged_ok',
            'slower_degraded', 'slower_ok']
    cols = ['#e76f51', '#2a9d8f', '#f4a261', '#a8d5a2', '#e9c46a', '#4c72b0']
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6))
    ax = axes[0]
    base = tr['all_gradeable']
    vals = [base['rates'][c] for c in cats]
    bars = ax.bar(cats, vals, color=cols, alpha=0.9)
    for b, c in zip(bars, cats):
        ci = base['cis'].get(c)
        if ci and ci[0] is not None:
            ax.errorbar(b.get_x() + b.get_width() / 2, b.get_height(),
                        yerr=[[b.get_height() - ci[0]], [ci[1] - b.get_height()]],
                        fmt='none', color='#333', ecolor='#666', capsize=3)
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.004,
                f'{b.get_height():.3f}', ha='center', fontsize=8)
    ax.set_ylabel('Share of grading-eligible executions with valid matched timing\n'
                  f'(n={base["n_valid"]}, M3-degraded among these = {base["degraded_any"]})')
    ax.set_title('a) Cost × task-degradation tradeoff (all gradeable, 10% unchanged band)')
    ax.tick_params(axis='x', rotation=14, labelsize=8)
    ax.set_ylim(0, max(vals) * 1.28)

    ax = axes[1]
    x = np.arange(len(cats))
    for i, c in enumerate(COHORTS):
        block = tr.get(c) or {}
        rates = (block.get('all') or {}).get('rates') or {}
        if not rates:
            continue
        off = (i - 1.5) * 0.23
        ax.bar(x + off, [rates.get(k, 0.0) for k in cats], width=0.21,
               label=CCAT[c], color=CLAB[c], alpha=0.9)
    ax.set_xticks(x, cats, rotation=14, fontsize=8)
    ax.set_ylabel('Share within cohort (gradeable, valid timing)')
    ax.set_title('b) Same categories split by anomaly cohort')
    ax.legend(fontsize=8, loc='upper left')
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, 'fig4_cost_vs_degradation.png'))
    plt.close(fig)
    print('wrote fig4')


# ── Fig 5: effects across families / LEPs / topologies ─────────────────────
def fig5():
    st = A['stratified']
    fig, axes = plt.subplots(1, 3, figsize=(14.5, 4.8))
    ax = axes[0]
    fams = sorted(st['by_family'].keys())
    vals = [st['by_family'][f].get('wall_diff_median') or 0 for f in fams]
    cis = [st['by_family'][f].get('wall_diff_ci') for f in fams]
    _bar_ci(ax, fams, vals, cis)
    ymax = max(max(abs(min(vals)), abs(max(vals))), 1.0) * 1.45
    ax.set_ylim(-ymax, ymax)
    for i, f in enumerate(fams):
        m = st['by_family'][f]
        ax.text(i, ymax * 0.98,
                f"n={m['n']}  slower {_fr(m.get('frac_slower'))} · "
                f"degraded {_fr(m.get('task_degradation_rate_gradeable'))}",
                ha='center', va='top', fontsize=7.5, color='#555')
    ax.set_title('Task family: median wall-Δ (s), fixture-cluster CI')
    ax.axhline(0, color='k', lw=0.7, ls='--')
    ax.tick_params(axis='x', rotation=8, labelsize=8)

    ax = axes[1]
    leps = sorted(st['by_lep'].keys())
    short = [k.replace('LEP_', '').replace('INDIRECT_PROMPT_INJECTION', 'IPI')
             .replace('HANDOFF_CORRUPTION', 'HC')
             .replace('INPUT_DISREGARD', 'ID')
             .replace('TOOL_RESULT_CORRUPTION', 'TRC') for k in leps]
    vals = [st['by_lep'][k].get('wall_diff_median') or 0 for k in leps]
    cis = [st['by_lep'][k].get('wall_diff_ci') for k in leps]
    _bar_ci(ax, range(len(leps)), vals, cis)
    ymax = max(max(abs(min(vals)), abs(max(vals))), 1.0) * 1.45
    ax.set_ylim(-ymax, ymax)
    for i, k in enumerate(leps):
        m = st['by_lep'][k]
        ax.text(i, ymax * 0.98,
                f"n={m['n']}\nslower {_fr(m.get('frac_slower'))} · "
                f"degraded {_fr(m.get('task_degradation_rate_gradeable'))}",
                ha='center', va='top', fontsize=7.5, color='#555')
    ax.set_xticks(range(len(leps)), short)
    ax.set_title('LEP: median wall-Δ (s) — the LEP-specific operating difference')
    ax.axhline(0, color='k', lw=0.7, ls='--')

    ax = axes[2]
    tops = sorted(st['by_topology'].keys())
    vals = [st['by_topology'][t].get('wall_diff_median') or 0 for t in tops]
    cis = [st['by_topology'][t].get('wall_diff_ci') for t in tops]
    _bar_ci(ax, range(len(tops)), vals, cis)
    ymax = max(max(abs(min(vals)), abs(max(vals))), 1.0) * 1.45
    ax.set_ylim(-ymax, ymax)
    for i, t in enumerate(tops):
        m = st['by_topology'][t]
        ax.text(i, ymax * 0.98,
                f"n={m['n']}  slower {_fr(m.get('frac_slower'))} · "
                f"degraded {_fr(m.get('task_degradation_rate_gradeable'))}",
                ha='center', va='top', fontsize=7.5, color='#555')
    ax.set_xticks(range(len(tops)), tops, rotation=10, fontsize=8)
    ax.set_title('Topology: median wall-Δ (s)')
    ax.axhline(0, color='k', lw=0.7, ls='--')
    fig.suptitle('Operational effects across task families, LEPs, topologies '
                 '(matched wall-Δ vs benign median; CIs fixture-cluster)', y=1.0)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, 'fig5_operational_effects_family_lep.png'))
    plt.close(fig)
    print('wrote fig5')


# ── Fig 6: mechanisms ───────────────────────────────────────────────────────
def fig6():
    mech = json.load(open(os.path.join(OUT, 'mechanism_aggregates.json')))
    MK = list(mech['mechanism_definitions'].keys())
    labels = ['MK1\nmissing\nstage', 'MK2\nskipped\nverification', 'MK3\npremature\nfinalization',
              'MK4\nrepeated\ntools', 'MK5\nexcess\nhandoffs', 'MK6\nerror\nretries',
              'MK7\nreduced\nevidence', 'MK8\nmore effort,\nno quality']
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    x = np.arange(len(MK))
    W = 0.2
    ax = axes[0]
    for i, c in enumerate(COHORTS):
        block = mech['prevalence_by_cohort'].get(c) or {}
        vals = [0 if (block.get(k) or {}).get('prevalence') is None
                else block[k]['prevalence'] for k in MK]
        ax.bar(x + (i - 1.5) * W, vals, width=W * 0.95, label=CCAT[c], color=CLAB[c], alpha=0.9)
    ax.set_xticks(x, labels, fontsize=7.5)
    ax.set_ylabel('Prevalence (share of cohort, evaluable executions)')
    ax.set_title('a) Structural mechanism prevalence by anomaly cohort\n'
                 '(MK4 is an exact-args redundancy measure; MK2 near-null by design)')
    ax.legend(fontsize=8)
    ax.set_ylim(0, 0.85)

    ax = axes[1]
    oc = mech['association_with_outcome']
    deg = oc.get('M3_degraded') or {}
    ok = oc.get('M3_not_degraded') or {}
    vals_d = [0 if (deg.get(k) or {}).get('prevalence') is None
              else deg[k]['prevalence'] for k in MK]
    vals_o = [0 if (ok.get(k) or {}).get('prevalence') is None
              else ok[k]['prevalence'] for k in MK]
    ax.bar(x - W / 2, vals_d, width=W, label='M3-degraded (gradeable)', color='#d62728', alpha=0.85)
    ax.bar(x + W / 2, vals_o, width=W, label='M3 not-degraded (gradeable)', color='#2a9d8f', alpha=0.85)
    ax.set_xticks(x, labels, fontsize=7.5)
    ax.set_ylabel('Prevalence')
    ax.set_title('b) Mechanisms vs observed matched task-degradation')
    ax.legend(fontsize=8)
    ts = oc.get('recorded_task_success_gradeable') or {}
    ds = ts.get('M3_degraded_subset') or {}
    os_ = ts.get('M3_not_degraded_subset') or {}
    ax.text(0.0, -0.24,
            f"recorded task-success (gradeable, trusted records): all {_fr(ts.get('rate'))}  ·  "
            f"M3-degraded {_fr(ds.get('rate'))}  vs  M3-not-degraded {_fr(os_.get('rate'))}",
            transform=ax.transAxes, fontsize=8, color='#444', ha='left')
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, 'fig6_mechanism_comparisons.png'))
    plt.close(fig)
    print('wrote fig6')


if __name__ == '__main__':
    for f in (fig1, fig2, fig3, fig4, fig5, fig6):
        f()
    print('all figures written to', FIG)
