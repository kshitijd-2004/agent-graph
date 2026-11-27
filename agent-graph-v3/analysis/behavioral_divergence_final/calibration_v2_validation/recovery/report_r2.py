#!/usr/bin/env python3.11
"""R2_RESULTS.md — concise round-2 validation results.

Generated ONLY after the confirm + adjudicate phases complete (reads
ledger/r2_results.json).  No pre-baked numbers: everything is
computed.  Sections: gates + P/R/F1 (with fixture-cluster + pi-weighted
CIs), per-dimension / per-LEP, FN/FN-reasons mechanistic read, H0
null-FP, inter-judge agreement, C4.  No LLM calls.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RULES = ('S1_shrinkage', 'C0_original', 'C1_family_q98', 'V41_union')


def main():
    d = json.load(open(os.path.join(HERE, 'ledger',
                                    'r2_results.json')))
    L = ['# R2_RESULTS — round-2 corrected validation (concise)',
         '',
         f"confirmatory rows: {d['n_confirm']} (pilot-excluded) · "
         f"scored: {d['n_scored']} · **independent ref positives: "
         f"{d['n_pos']}** · unresolved+invalid: "
         f"{d['unresolved_rate']:.3f} · gate status: "
         f"**{d['gate_status']}** (C1–C3 established only at "
         f"n_pos≥30 AND unresolved≤0.15)",
         '',
         '## 1. Gates + P/R/F1 (frozen criteria; no analyzer change)',
         '',
         '| rule | P | R | F1 | P_w (π-weighted) | F1 95% CI (cluster) | F1 95% CI (π-weighted) |',
         '|---|---|---|---|---|---|---|']
    for rn in RULES:
        r = d['per_rule'][rn]
        ci = r['fixture_cluster_ci95']

        def fmt(ci2):
            return f'[{ci2[0]}, {ci2[1]}]' if ci2[0] is not None \
                else 'n/a'
        L.append(f"| **{rn}** | {r['precision']} | {r['recall']} | "
                 f"{r['f1']} | {ci['P_w_point']} | "
                 f"{fmt(ci['F1'])} | {fmt(ci['F1_w'])} |")
    L += ['', 'Gates C1 (P≥0.70) / C2 (R≥0.70) / C3 (F1≥0.65):']
    for rn in ('S1_shrinkage', 'V41_union'):
        g = d['per_rule'][rn]['C1_pass_ge_0.70']
        g2 = d['per_rule'][rn]['C2_pass_ge_0.70']
        g3 = d['per_rule'][rn]['C3_pass_ge_0.65']
        L.append(f'- {rn}: C1={g} C2={g2} C3={g3} '
                 f'(None = not established at this support)')
    L += ['', 'C4 (execution-level benign FPR, deterministic B2B '
              'p3 probe-union — inherited, no LLM): '
            f"**{d['C4_gate']['probe_union_fpr']}** "
            f"(pass ≤0.05: {d['C4_gate']['pass']}); row-OR "
            f"{d['C4_gate']['row_or_fpr']} (separate scope, never "
            f'the gate).',
         '',
         '## 2. Per-dimension + per-LEP (S1)',
         '',
         '| dimension | n_eval | P | R | F1 |',
         '|---|---|---|---|---|']
    pd = d['per_rule']['S1_shrinkage']['per_dim']
    for k, v in pd.items():
        L.append(f"| {k} | {v['n_eval']} | {v['P']} | {v['R']} | "
                 f"{v['F1']} |")
    L += ['', 'per-LEP (support-flagged):',
          '',
          '| LEP | n | TP/FP/FN/TN | P | R | F1 | stable(≥30 pos) |',
          '|---|---|---|---|---|---|---|']
    pl = d['per_rule']['S1_shrinkage']['per_lep']
    for k, v in pl.items():
        L.append(f"| {k} | {v['n']} | "
                 f"{v['TP']}/{v['FP']}/{v['FN']}/{v['TN']} | "
                 f"{v['P']} | {v['R']} | {v['F1']} | "
                 f"{v['stable_support']} |")
    L += ['',
          '## 3. Why the false negatives (S1; V4.1 comparison)',
          '']
    m = d['mechanistic']['S1_shrinkage']
    mv = d['mechanistic']['V41_union']
    s1hl = d['per_rule']['S1_shrinkage']['per_lep'][
        'LEP_HANDOFF_CORRUPTION']['F1']
    v41hl = d['per_rule']['V41_union']['per_lep'][
        'LEP_HANDOFF_CORRUPTION']['F1']
    L += [f"- S1 **FN = {m['n_FN']}** / ref-positives "
          f"{d['n_pos']} (recall shortfall); by LEP: "
          f"{m['FN_by_lep']}; by family: {m['FN_by_fam']}.",
          f"- Reference positive-dimension mix on S1-FN rows: "
          f"{m['FN_ref_dim_mix']} — the dimensions where the "
          f"independent reference saw a meaningful change that S1 "
          f"missed.",
          f"- soft-FN share (S1 fired a DIFFERENT dimension than the "
          f"ref-positive one, i.e. partial detection): "
          f"{m['FN_soft_share']}.",
          f"- V4.1 comparison: FN = {mv['n_FN']} (S1 FN "
          f"{m['n_FN']}); HANDOFF per-LEP F1: S1 {s1hl} vs V4.1 "
          f"{v41hl}.",
          '']
    L += ['## 4. Why the false positives (S1)',
          '',
          f"- S1 **FP = {m['n_FP']}** (of the {d['n_neg']} ref-"
          f"negatives). FP-by-LEP: {m['FP_by_lep']}; the "
          f"dimensions that fired on FP rows: "
          f"{m['FP_fired_dim_mix']}.",
          '',
          '## 5. H0 null-FP (fresh two-judge; corroborating, not the '
          'gate)',
          '']
    h = d['h0_null_fp']
    L += [f"- probe-level diverges calls on pure-benign 4-ways: "
          f"{h['probe_level']['n_pos']} / "
          f"{h['probe_level']['n_judge_packets']} judge-packets; "
          f"exec-level FP: {h['exec_level']}.",
          '',
          '## 6. Inter-judge agreement',
          '']
    ij = d['inter_judge']
    ua_ratio = (ij['union_agree'] / ij['n_two_judge']) \
        if ij['n_two_judge'] else None
    ua_str = ('%.2f' % ua_ratio) if ua_ratio is not None else 'n/a'
    L += [f"- two-judge rows: {ij['n_two_judge']}; union-agree "
          f"{ij['union_agree']}/{ij['n_two_judge']} ({ua_str}); "
          f"per-dimension cells agree "
          f"{ij['dim_cells_agree']}/{ij['dim_cells']}.",
          '',
          '## 7. Interpretation (honest; frozen criteria)',
          '',
          'S1 is the object under validation.  The gate is '
          'establishable only at n_pos≥30 with unresolved ≤0.15; '
          'beyond that, P/R/F1 speak for themselves.  The '
          'π-weighted estimators + fixture-cluster CIs are the '
          'design-unbiased round-2 quantities; unweighted values are '
          'the raw sample reads.  No threshold, extractor, or '
          'criterion was changed.']
    open(os.path.join(HERE, 'R2_RESULTS.md'), 'w').write('\n'.join(L)
                                                         + '\n')
    print('wrote R2_RESULTS.md (n_pos=%d, gate=%s)'
          % (d['n_pos'], d['gate_status']))


if __name__ == '__main__':
    main()
