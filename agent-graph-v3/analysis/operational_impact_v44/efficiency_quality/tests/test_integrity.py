#!/usr/bin/env python3.11
"""Integrity & regression tests for the efficiency/quality extension.

Run:  cd analysis/operational_impact_v44/efficiency_quality
      PYTHONPATH=. python3.11 tests/test_integrity.py
"""
import json
import math
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, 'out')
BASE = os.path.dirname(HERE)


def _aggregates():
    return json.load(open(os.path.join(OUT, 'efficiency_quality_aggregates.json')))


def _supplemental():
    return json.load(open(os.path.join(OUT, 'supplemental_contrasts.json')))


def _pex():
    return [json.loads(l) for l in open(os.path.join(OUT, 'efficiency_quality_per_execution.jsonl')) if l.strip()]


# ── T1: base artifacts preserved (the extension must not modify them) ─────
def test_base_artifacts_preserved():
    # base per-execution table row count + a frozen aggregate value
    base_pex = [json.loads(l) for l in
                open(os.path.join(BASE, 'out/operational_impact_per_execution.jsonl')) if l.strip()]
    assert len(base_pex) == 16767
    base_agg = json.load(open(os.path.join(BASE, 'operational_impact_aggregates.json')))
    d = base_agg['resilience']['downstream_resilience']
    assert d['n'] == 948 and d['denominator'] == 3545
    assert abs(d['rate'] - (1 - 948 / 3545)) < 1e-12


# ── T2: extension per-execution table integrity ────────────────────────────
def test_pex_integrity():
    rows = _pex()
    assert len(rows) == 16767
    assert len(set(r['trace_id'] for r in rows)) == 16767
    # latency-mode validity ordering: trusted_runtime ⊆ combined == event_span
    n_tr = sum(1 for r in rows if r.get('diff_trusted_runtime') is not None)
    n_es = sum(1 for r in rows if r.get('diff_event_span') is not None)
    n_cb = sum(1 for r in rows if r.get('diff_combined') is not None)
    assert n_tr < n_cb == n_es, (n_tr, n_cb, n_es)
    # combined fallback rule: trusted present -> combined==trusted latency
    mism = 0
    for r in rows:
        if r.get('lat_trusted_runtime') is not None:
            if abs(r['lat_combined'] - r['lat_trusted_runtime']) > 1e-9:
                mism += 1
        elif r.get('lat_event_span') is not None:
            if abs(r['lat_combined'] - r['lat_event_span']) > 1e-9:
                mism += 1
    assert mism == 0, f'combined latency fallback violated on {mism} rows'
    # sign field consistency
    for r in rows[:2000]:
        if r.get('diff_combined') is not None:
            exp = 1 if r['diff_combined'] > 0 else (-1 if r['diff_combined'] < 0 else 0)
            assert r['sign_combined'] == exp


# ── T3: contingency table sums & base reconciliation ───────────────────────
def test_contingency_reconciles_base():
    A = _aggregates()
    c = A['sections']['s3_primary']['contingency']
    s3 = A['sections']['s3_primary']
    assert c['faster_degraded'] + c['faster_not_degraded'] == s3['faster']['n']
    assert c['slower_degraded'] + c['slower_not_degraded'] == s3['slower']['n']
    assert c['unchanged_degraded'] + c['unchanged_not_degraded'] == s3['unchanged']['n']
    # extension primary pop (12,625): 612/442/938 = 1,992 M3-positive
    assert c['faster_degraded'] == 612 and c['slower_degraded'] == 442 and c['unchanged_degraded'] == 938
    assert c['faster_degraded'] + c['slower_degraded'] + c['unchanged_degraded'] == 1992
    # base record-population recon (12,562): 612/441/934 = 1,987 (5 record-null rows out)
    recon = s3['reconcile_base_counts']
    assert recon['base_tradeoff_pop_n'] == 12562
    assert recon['m3_degraded_faster'] == 612 and recon['m3_degraded_slower'] == 441 and recon['m3_degraded_unchanged'] == 934
    assert recon['sum'] == 1987
    # 1,992 M3-positive = 1,987 (record-pop) + 5 record-null; base 'faster+slower' framing
    # covered 612+441 = 1,053 (or 608+444 = 1,052 on the early trusted-record pass)
    assert recon['m3_degraded_faster'] + recon['m3_degraded_slower'] + recon['m3_degraded_unchanged'] == 1987
    assert recon['excluded_from_1052'] == 5


# ── T4: joint ARD/RR/OR estimand sanity + CI validity ─────────────────────
def test_joint_estimators():
    A = _aggregates()
    s3 = A['sections']['s3_primary']
    fr = s3['faster']['degradation_rate']; sr = s3['slower']['degradation_rate']
    ard = s3['ard']; rr = s3['rr']; orr = s3['or']
    assert abs(ard - (fr - sr)) < 1e-9
    assert abs(rr - fr / sr) < 1e-9
    # OR consistency: (f_deg/(f_n-f_deg)) / (s_deg/(s_n-s_deg))
    fd, fn = s3['faster']['n_degraded'], s3['faster']['n']
    sd, sn = s3['slower']['n_degraded'], s3['slower']['n']
    exp_or = (fd * (sn - sd)) / (sd * (fn - fd))
    assert abs(orr - exp_or) < 1e-9
    # CI must bracket the point estimate and be within [0,1] bounds
    lo, hi = s3['ard_ci']
    assert lo <= ard <= hi
    assert lo >= -1.0 and hi <= 1.0
    # the primary ARD CI includes 0 (the null result)
    assert s3['ard_ci'][0] < 0 < s3['ard_ci'][1]


# ── T5: magnitude bins partition the valid population ─────────────────────
def test_bins_partition():
    A = _aggregates()
    b = A['sections']['s4_bins7']['bins']
    total = sum(v['n'] for v in b.values())
    assert total == A['sections']['s4_bins7']['n']
    # no sparse bin in the 7-bin primary table (all n > 30)
    assert all(not v['sparse'] for v in b.values())
    # J-shape: extreme-faster is the max-rate bin; a moderate-faster bin is the min
    rates = {k: v['degradation_rate'] for k, v in b.items()}
    assert rates['faster_>25%'] == max(rates.values())
    assert rates['faster_5_10%'] == min(rates.values())


# ── T6: generation table dedup + audit invariants ──────────────────────────
def test_generation_table():
    gen = {json.loads(l)['trace_id']: json.loads(l) for l in
           open(os.path.join(OUT, 'generation_length_per_trace.jsonl')) if l.strip()}
    assert len(gen) == 24954
    for t, g in gen.items():
        assert g['ts_valid']
        # marker chars are NOT counted as generated content
        assert g['reasoning_marker_chars'] > 0 or g['n_llm_calls'] == 0
        # generated words <= generated chars
        assert g['generated_content_words'] <= g['generated_content_chars']
        # per-call durations sum to the total
        if g['per_call_durations_s']:
            assert abs(sum(g['per_call_durations_s']) - g['llm_inference_s_total']) < 1e-9
            assert len(g['per_call_durations_s']) == g['n_llm_calls']


# ── T7: adjusted model + Simpson structure ─────────────────────────────────
def test_adjusted_simpson():
    A = _aggregates()
    adj = A['sections']['s7_adjusted']
    wf = A['sections']['s8_sensitivities']['within_family_adjusted']
    # crude ARD is positive but the within-family coefficients disagree in sign
    s3 = A['sections']['s3_primary']
    assert s3['ard'] > 0                       # crude positive
    coefs = [wf[f]['coef'] for f in ('code_review', 'financial_analysis', 'research_synthesis')]
    # code_review negative, the others positive -> sign disagreement (Simpson)
    assert coefs[0] < 0 < coefs[1]
    assert coefs[0] < 0 < coefs[2]


# ── T8: extreme-faster tail robust across latency modes, null in success-ref
def test_tail_robustness():
    S = _supplemental()
    modes = S['extreme_vs_within5_by_mode']
    for m in ('combined', 'trusted_runtime', 'event_span'):
        assert modes[m]['ard_extreme_minus_within5'] > 0.08, m
        assert modes[m]['ard_ci'][0] > 0, m        # significant
    pops = S['extreme_vs_within5_by_population']
    assert pops['infra_clean']['ard_extreme_minus_within5'] > 0.08
    assert pops['infra_clean']['ard_ci'][0] > 0          # robust in infra-clean
    assert pops['success_ref']['ard_extreme_minus_within5'] < 0.05
    assert pops['success_ref']['ard_ci'][0] < 0          # attenuates to null


# ── T9: mechanism flag integrity ───────────────────────────────────────────
def test_mechanism_flags():
    A = _aggregates()
    m6 = A['sections']['s6_mechanisms']
    # MG7: essentially all faster-degraded and slower-degraded are complete-but-wrong
    assert m6['faster_degraded']['MG7_complete_but_wrong']['n_flagged'] >= 600
    assert m6['slower_degraded']['MG7_complete_but_wrong']['n_flagged'] >= 430
    # MG4 (premature finalization) is near-null
    assert m6['faster_degraded']['MG4_earlier_finalization']['n_flagged'] <= 3
    # MG2 must be None everywhere (not observable)
    ex = [json.loads(l) for l in open(os.path.join(HERE, 'mechanism_examples.jsonl')) if l.strip()]
    assert len(ex) > 0
    for e in ex:
        assert e['mg_flags']['MG2_reduced_evidence_coverage'] is None
        assert e['matched_benign_refs'], 'examples must carry matched-benign reference IDs'


# ── T10: joint cells ────────────────────────────────────────────────────────
def test_joint_cells():
    A = _aggregates()
    j = A['sections']['s9_joint']
    share = j['degraded_classified_not_slower_by_latency_only']['share']
    # ~77% of M3-degraded are not slower (faster or unchanged)
    assert 0.70 <= share <= 0.85


if __name__ == '__main__':
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    fails = 0
    for fn in fns:
        try:
            fn()
            print('PASS', fn.__name__)
        except Exception:
            fails += 1
            print('FAIL', fn.__name__)
            traceback.print_exc()
    sys.exit(1 if fails else 0)
