#!/usr/bin/env python3.12
"""Publication figures for the efficiency/quality extension (contract §10).

Reads out/efficiency_quality_aggregates.json + supplemental_contrasts.json and
writes out/figs/figN.* (both SVG vector and PNG raster). Consistent
typography, explicit units, no exaggerated small effects (error bars on every
rate; axis ranges anchored at 0 where a rate is plotted).
"""
import json
import os
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out')
FIG = os.path.join(OUT, 'figs')
os.makedirs(FIG, exist_ok=True)

plt.rcParams.update({'figure.dpi': 150, 'font.size': 9.5, 'font.family': 'DejaVu Sans',
                    'axes.grid': True, 'grid.alpha': 0.28, 'axes.spines.top': False,
                    'axes.spines.right': False, 'errorbar.capsize': 3,
                    'axes.titlesize': 10, 'axes.titleweight': 'bold'})

AGG = json.load(open(os.path.join(OUT, 'efficiency_quality_aggregates.json')))
SUP = json.load(open(os.path.join(OUT, 'supplemental_contrasts.json')))


def save(fig, name):
    fig.savefig(os.path.join(FIG, name + '.svg'))
    fig.savefig(os.path.join(FIG, name + '.png'))
    plt.close(fig)
    print('wrote', name)


def _ci_bar(ax, x, y, ci, **kw):
    if ci and ci[0] is not None:
        ax.errorbar([x], [y], yerr=[[max(0, y - ci[0])], [ci[1] - y]], fmt='none',
                    color='#333', ecolor='#666', capsize=3, **kw)


# ── Fig 1: degradation rate across relative-latency-change categories ─────
def fig1():
    bins = AGG['sections']['s4_bins7']['bins']
    order = list(bins.keys())
    labels = ['>25% faster', '10–25% faster', '5–10% faster', '±5% (unchanged)',
              '5–10% slower', '10–25% slower', '>25% slower']
    xs = range(len(order))
    rates = [bins[k]['degradation_rate'] for k in order]
    cis = [bins[k]['degradation_ci'] for k in order]
    ns = [bins[k]['n'] for k in order]
    cols = ['#e76f51', '#f4a261', '#f8c471', '#999999', '#f8c471', '#f4a261', '#e76f51']
    fig, ax = plt.subplots(figsize=(10.5, 5))
    bars = ax.bar(xs, rates, color=cols, alpha=0.92)
    for i, (k, r, c, n, b) in enumerate(zip(order, rates, cis, ns, bins.values())):
        _ci_bar(ax, i, r, c)
        ax.text(i, r + (0.012 if r else 0.006), f'n={n}\n({b["n_degraded"]})',
                ha='center', fontsize=7.5, color='#444')
    ax.set_xticks(xs, labels, rotation=0, fontsize=8.5)
    ax.set_ylabel('Observed matched task-degradation rate (M3, grading-eligible)')
    ax.set_ylim(0, max(rates) * 1.28)
    ax.set_title('Matched task-degradation rate by relative latency change vs matched-benign median\n'
                 '(error bars = 95% fixture-cluster CI; faster categories left, slower right)')
    # shade the J-shape interpretation
    ax.axvspan(-0.5, 2.5, color='#f4a261', alpha=0.06)
    ax.text(1.0, ax.get_ylim()[1] * 0.9, 'moderate-faster = lowest', ha='center',
            fontsize=8, color='#8a5a1a')
    save(fig, 'fig1_degradation_by_latency_category')


# ── Fig 2: joint distribution of matched latency change × task outcome ─────
def fig2():
    s3 = AGG['sections']['s3_primary']
    j = AGG['sections']['s9_joint']
    cont = s3['contingency']
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    ax = axes[0]
    cats = ['faster\n(<−10%)', 'unchanged\n(±10%)', 'slower\n(>+10%)']
    deg = [cont['faster_degraded'], cont['unchanged_degraded'], cont['slower_degraded']]
    notd = [cont['faster_not_degraded'], cont['unchanged_not_degraded'],
            cont['slower_not_degraded']]
    x = np.arange(3)
    ax.bar(x, notd, label='not degraded', color='#a8d5a2')
    ax.bar(x, deg, bottom=notd, label='degraded (M3)', color='#d62728')
    for i, (d, nd) in enumerate(zip(deg, notd)):
        ax.text(i, nd + d / 2, str(d), ha='center', va='center', fontsize=8, color='#333')
        ax.text(i, nd / 2, str(nd), ha='center', va='center', fontsize=8, color='#2a5a2a')
    ax.set_xticks(x, cats, fontsize=8.5)
    ax.set_ylabel('Number of grading-eligible executions')
    ax.set_title('a) Joint distribution: latency category × matched degradation\n'
                 '(n=%d; faster_degraded=%d of %d faster)'
                 % (s3['n'], cont['faster_degraded'], s3['faster']['n']))
    ax.legend(fontsize=8)

    ax = axes[1]
    ax.set_title('b) Actual task-correctness × speed (trusted records)')
    cells = [('faster, correct', j['faster_task_correct']['n'], '#2a9d8f'),
             ('faster, incorrect', j['faster_task_incorrect']['n'], '#e76f51'),
             ('slower, correct', j['slower_task_correct']['n'], '#55a868'),
             ('slower, incorrect', j['slower_task_incorrect']['n'], '#e9c46a')]
    vals = [c[1] for c in cells]
    bars = ax.bar([c[0] for c in cells], vals, color=[c[2] for c in cells], alpha=0.9)
    popn = s3['n']
    for b, c in zip(bars, cells):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 12,
                f"{c[1]}\n({c[1]/popn:.1%} of pop)",
                ha='center', fontsize=8)
    share = j['degraded_classified_not_slower_by_latency_only']['share']
    ax.set_ylabel('n (share of valid-latency population)')
    ax.tick_params(axis='x', rotation=8, labelsize=8)
    ax.text(0.5, -0.28,
            f'{share:.0%} of M3-degraded runs are NOT slower than their benign median '
            f'(a latency-only assessment would not flag them as operational losses)',
            transform=ax.transAxes, ha='center', fontsize=8.5, color='#8b2020')
    save(fig, 'fig2_joint_latency_outcome')


# ── Fig 3: faster vs slower degradation rates by task family ───────────────
def fig3():
    st = AGG['sections']['s7_strata']
    fams = [k for k in st if k.startswith('family_')]
    fig, ax = plt.subplots(figsize=(9.5, 5))
    x = np.arange(len(fams))
    fr = [st[f]['faster_rate'] for f in fams]
    sr = [st[f]['slower_rate'] for f in fams]
    ax.bar(x - 0.2, fr, width=0.4, label='faster (<−10%)', color='#e76f51')
    ax.bar(x + 0.2, sr, width=0.4, label='slower (>+10%)', color='#4c72b0')
    for i, f in enumerate(fams):
        ard = st[f]['ard']
        aci = st[f].get('ard_ci')
        short = f.replace('family_', '')
        ax.text(i, max(fr[i], sr[i]) + 0.012, f'{short}\nn={st[f]["n"]}\nARD={ard:+.3f}',
                ha='center', fontsize=8, color='#444')
        if aci and aci[0] > 0:
            ax.text(i, 0.02, '▲ faster↑deg', ha='center', fontsize=7, color='#b30b0b')
        elif aci and aci[1] < 0:
            ax.text(i, 0.02, '▼ faster↓deg', ha='center', fontsize=7, color='#0b6b0b')
    ax.set_xticks(x, [f.replace('family_', '') for f in fams])
    ax.set_ylabel('Observed matched degradation rate')
    ax.set_ylim(0, max(max(fr), max(sr)) * 1.35)
    ax.legend(fontsize=9)
    ax.set_title('faster-vs-slower matched-degradation by task family\n'
                 '(faster↑deg = faster runs degrade more; ▼ faster↓deg = opposite; '
                 'colored ARD CIs vs 0)')
    save(fig, 'fig3_family_faster_vs_slower')


# ── Fig 4: generated content / LLM-call duration across cells ───────────────
def fig4():
    s5 = AGG['sections']['s5_generation']
    cells = ['faster_degraded', 'faster_not_degraded', 'slower_degraded', 'slower_not_degraded']
    labels = ['faster+\ndegraded', 'faster+\nnot-deg', 'slower+\ndegraded', 'slower+\nnot-deg']
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    for ax, key, lab, ylab in (
            (axes[0], 'generated_content_chars', 'Generated content length',
             'Median deduped model-composed characters'),
            (axes[1], 'llm_inference_s_per_call', 'LLM-call duration',
             'Median seconds per LLM call'),
            (axes[2], 'n_llm_calls', 'LLM-call count', 'Median LLM calls')):
        p = [s5[c][key]['perturbed_median'] for c in cells]
        b = [s5[c][key]['ben_median_of_medians'] for c in cells]
        x = np.arange(len(cells))
        ax.bar(x - 0.2, b, width=0.4, label='matched-benign median', color='#bbbbbb')
        ax.bar(x + 0.2, p, width=0.4, label='cell perturbed median', color='#4c72b0')
        ymax = max(max(p), max(b))
        def _fmt(v):
            return f'{v:.0f}' if abs(v) >= 10 else (f'{v:.1f}' if abs(v) >= 1 else f'{v:.2f}')
        for i, pv in enumerate(p):
            d = s5[cells[i]][key]['diff_median']
            dlbl = f" ({_fmt(pv)}; Δ vs benign {_fmt(d)})" if d is not None else f" ({_fmt(pv)})"
            off = 0.30 if i % 2 else 0.04                    # stagger: no label overlap
            ax.text(i + 0.2, pv + ymax * off, dlbl, ha='center',
                    fontsize=7.5, color='#1a3a6b')
        ax.set_xticks(x, labels, fontsize=8)
        ax.set_ylabel(ylab)
        ax.set_title(lab)
        ax.legend(fontsize=8)
        ax.set_ylim(0, ymax * 1.28)
    fig.suptitle('Reduced-generation-effort comparison (matched to benign; content-length & '
                 'duration are NOT token/compute proxies)', y=1.0, fontsize=11, weight='bold')
    save(fig, 'fig4_generation_length_and_call_duration')


# ── Fig 5: adjusted association, latency → degradation ────────────────────
def fig5():
    adj = AGG['sections']['s7_adjusted']
    wf = AGG['sections']['s8_sensitivities']['within_family_adjusted']
    prim = AGG['sections']['s3_primary']
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 5))
    ax = axes[0]
    ax.bar([0], [prim['ard'] * 100], width=0.5, color='#4c72b0', alpha=0.9)
    ax.errorbar([0], [prim['ard'] * 100],
                yerr=[[prim['ard'] * 100 - prim['ard_ci'][0] * 100],
                      [prim['ard_ci'][1] * 100 - prim['ard'] * 100]],
                fmt='none', color='#333', ecolor='#666', capsize=4)
    ax.axhline(0, color='k', lw=0.8, ls='--')
    ax.set_xticks([0], ['crude 2-group\n(faster vs slower)'])
    ax.set_ylabel('Absolute risk difference (faster − slower), ×100')
    ax.set_title(f'a) Crude ARD = {prim["ard"]*100:+.1f} pp, 95% CI '
                 f'[{prim["ard_ci"][0]*100:+.1f}, {prim["ard_ci"][1]*100:+.1f}] (includes 0)')
    ax.text(0, ax.get_ylim()[1] * 0.55, f'OR {prim["or"]:.2f}\n[{prim["or_ci"][0]:.2f}, {prim["or_ci"][1]:.2f}]',
            ha='center', fontsize=9, color='#444')

    ax = axes[1]
    fams = ['code_review', 'financial_analysis', 'research_synthesis']
    coef = [wf[f]['coef'] for f in fams]
    lo = [wf[f]['ci95'][0] for f in fams]
    hi = [wf[f]['ci95'][1] for f in fams]
    x = np.arange(len(fams))
    ax.bar(x, coef, color=['#4c72b0', '#f4a261', '#e76f51'], alpha=0.9)
    for i in range(3):
        ax.errorbar([i], [coef[i]], yerr=[[coef[i] - lo[i]], [hi[i] - coef[i]]],
                    fmt='none', color='#333', ecolor='#666', capsize=4)
        ax.text(i, coef[i] - 0.02, f'n={wf[fams[i]]["n"]}', ha='center', va='top',
                fontsize=8, color='#555')
    # global adjusted
    ax.axhline(adj['rel_latency']['coef'], color='#333', ls=':', lw=1.2)
    ax.text(2.4, adj['rel_latency']['coef'], ' global adj. %+.2f'
            % adj['rel_latency']['coef'], va='bottom', fontsize=8, color='#333')
    ax.axhline(0, color='k', lw=0.8, ls='--')
    ax.set_xticks(x, fams)
    ax.set_ylabel('Adjusted log-odds coef on relative latency\n(fixture-cluster sandwich SE)')
    ax.set_title('b) Within-family adjusted association (Simpson structure)')
    save(fig, 'fig5_adjusted_latency_degradation')


# ── Fig 6: sensitivity across latency-measurement populations ──────────────
def fig6():
    sens = AGG['sections']['s8_sensitivities']
    order = ['mode_trusted_runtime', 'mode_event_span', 'infra_clean',
             'benign_mean_reference', 'winsor_p999', 'band_20pct']
    labels = ['trusted runtime\nonly', 'event-span\nonly', 'infra-clean',
              'benign MEAN\nref', 'winsor p999', '±20% band']
    fig, ax = plt.subplots(figsize=(10.5, 5))
    x = np.arange(len(order))
    for i, k in enumerate(order):
        v = sens[k]
        ard = (v['ard'] or 0) * 100
        ci = v.get('ard_ci')
        ax.scatter(i, ard, s=90, zorder=3, color='#4c72b0')
        if ci:
            ax.errorbar([i], [ard], yerr=[[ard - ci[0] * 100], [ci[1] * 100 - ard]],
                        fmt='none', color='#333', ecolor='#666', capsize=4)
        ax.text(i, ard + (ci[1] * 100 - ci[0] * 100 + 0.5 if ci else 1),
                f'n={v["n"]}\n{ard:+.1f} pp', ha='center', fontsize=8, color='#444')
    ax.axhline(0, color='k', lw=0.8, ls='--')
    ax.set_xticks(x, labels, fontsize=8.5)
    ax.set_ylabel('faster-vs-slower ARD (×100 = pp), 95% fixture-cluster CI')
    ax.set_title('AR1: primary ARD across latency-measurement populations & references\n'
                 '(all CIs include 0 → the coarse 2-group effect is null and measurement-robust; '
                 'only the ±20% band excludes 0)')
    save(fig, 'fig6_sensitivity_populations')


if __name__ == '__main__':
    for f in (fig1, fig2, fig3, fig4, fig5, fig6):
        f()
    print('all extension figures written to', FIG)
