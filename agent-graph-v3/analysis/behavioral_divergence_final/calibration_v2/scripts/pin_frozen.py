#!/usr/bin/env python3.11
"""Pin the dev-selected candidate for the confirmatory consumption.

MUST run AFTER compute_candidates.py (dev selection) and BEFORE
run_validation.py (the single confirmatory consumption).  Writes:
  ledger/frozen_candidate.json   — the pinned candidate document
  ledger/frozen_pin_sha256.txt   — IMMUTABLE content-hash sidecar,
                                    written at pin time (so the
                                    pre-consumption pin is auditable)

Idempotence: if the pin sidecar already exists, the candidate is NOT
overwritten (single-pin discipline; a re-pin is a no-op unless the
sidecar is explicitly removed — the stale-run mistake the validator
flagged).  Content-hash = sha256 of the sorted-JSON document with the
'sha256' key removed (self-describing, not whole-file).
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
V2 = os.path.abspath(os.path.join(HERE, '..'))
OUT = os.path.join(V2, 'ledger')
sys.path.insert(0, V2)
import cv2  # noqa: E402


def main():
    os.makedirs(OUT, exist_ok=True)
    pin_path = os.path.join(OUT, 'frozen_pin_sha256.txt')
    if os.path.exists(pin_path):
        print('PIN EXISTS (', open(pin_path).read().strip()[:16],
              ') — not re-pinning (single-consumption discipline).')
        print('To re-pin: remove ledger/frozen_pin_sha256.txt AND the '
              'confirmatory consumption has NOT yet happened.')
        return
    cand = json.load(open(os.path.join(OUT, 'candidates.json')))
    full = cv2.load_full_cells()
    v6dim = cv2.load_v6_dim()
    pools = cv2.build_pools(full)
    pair_pools = cv2.build_pair_pools(full)
    ztab = cv2.shrinkage_weights(full, v6dim, pair_pools, pools)
    outnull = cv2.outcome_null(full)
    outpool = cv2.fam_p3_out_pool_full()

    pol = 'S1_shrinkage'  # dev-selected (admissible + dominant F1,
    # lowest B2B of the admissible set; see CANDIDATE_COMPARISON §6)
    assert pol in cand['policies']

    frozen = {
        'selected_policy': pol,
        'a_d_a_threshold':
            ('theta_S1 = z(Fam|d)*F[cell] + (1-z)*(B_d + theta1_d) on the '
             'shared raw-T scale.  z -> 0 => the recentered-original rule '
             '(V4.1 recentered-extremal construct = spec-anchored frozen '
             'M1 4,150); z -> 1 => the C1 family-pooled exact-LOO q98 '
             'rule (audited G5 null, probe-union B2B 1430/50319=2.84%).  '
             'B_d = in-cell benign-pair mean (the ordinary-variability '
             'recentering term; missing => dimension uncalibrated, never '
             'forced).  F = family-pooled q0.98 exact leave-one-cell-out '
             'of the p3 probe pool (cp_lib byte-compatible).  z = EB '
             'weight sigma2_obs/(sigma2_obs+sigma2_b): sigma2_obs = '
             'bootstrap(N_BOOT=2000, seed 20270201) variance of '
             '(mean+max of 3 resampled family direct-pair distances); '
             'sigma2_b = leave-one-cell-out between-cell variance of the '
             '(B_d + theta1_d) endpoint (closed form; the cell\'s own '
             'endpoint value excluded).  Pure data-driven; named '
             'constants only: N_BOOT, seeds, q=0.98, alpha_out=0.05.'),
        'outcome_policy':
            ('O1 one-sided tie-aware exceedance test on the '
             'POPULATION-level family-pooled p3 disagree-mass null '
             '(13,074 / 8,642 / 16,581 over P1; LOO-excluding the '
             'cell; fire iff P(null >= s) <= alpha_out=0.05; '
             's=3*T_lep_outcome).  Parametric Binomial(3, p_hat) tail '
             'reported as robustness only.  Re-enables the outcome '
             'channel that q98 disables (threshold=1.0=max).  The '
             'population null is used for EVERY partition — never a '
             'partition-local pool.'),
        'b2b_control_discipline':
            ('every rule uses the p3 probe control: each cell\'s 3 p3 '
             'probes (benign<->benign means under H0; the LEP row is '
             'the exchangeable 4th execution, no ground truth) scored '
             'against the SAME per-cell threshold the LEP faces '
             '(matched + non-circular).  probe-union FPR (the audit '
             '1430/50319 object) is the execution-level control for '
             'whole-trace M1; the cell row-OR (cluster multiple-'
             'opportunity) and the checkpoint-scope row-OR are '
             'reported separately, never conflated.  The clean p2 '
             'leave-self-out control was rejected: a 2-ref mean probe '
             'vs a 3-ref-matched threshold is a cardinality mismatch '
             '(the exact G5 defect the audit repaired).'),
        'checkpoint_scope':
            ('M2 = per-component family-pooled q98 LOO benign-pair '
             'thresholds at intermediate scope {assertions,actions} and '
             'final scope {assertions,decisions,outcomes}; checkpoint '
             'divergence = logical union of available positive '
             'components (NO weighted composite anywhere in the '
             'scored pipeline); cross-agent + post-injection-boundary '
             'checkpoints only; non-origin agents; many-to-one origin '
             'exclusion; delivery verification; explicit no-checkpoint '
             'unavailable (3,245 rows), never forced.'),
        'checkpoint_policy':
            ('dimension-specific checkpoint gate, composite removed: '
             'per-component unweighted family-pooled q98 LOO benign-'
             'pair thresholds at each checkpoint scope; union of '
             'available positive dimensions; multiple-opportunity '
             'accounted at probe-observation (0.668%) and execution '
             'row-OR (2.50%) scopes, never conflated.'),
        'b2b_control':
            ('p3 probe control for every rule (matched + non-circular): '
             'probe-union FPR for whole-trace M1; cell row-OR as the '
             'cluster multiple-opportunity diagnostic; the p2 leave-'
             'self-out variant is rejected on cardinality-mismatch '
             'grounds (the G5 defect).'),
        'selected_on':
            'dev partition (agentprop-cv2-devsplit-202702, 154 un-'
            'inspected fixtures), admissibility gates only; the '
            'production rate was never an input to selection',
        'confirmatory_reference':
            'frozen V4.1 RECENTRED-extremal per-dimension union '
            '(independent byte-pinned extractor; = 4,150 over P1 = '
            'the spec-anchored frozen M1); the composite 4,497 is '
            'disclosed secondary only',
        'reference': {
            'primary':
                ('frozen V4.1 RECENTRED-extremal per-dimension union '
                 '(delta_d = T_d - B_d > theta1_d, None-safe, OR over '
                 'dims; byte-pinned independent extractor) = 4,150 '
                 'over P1 = the spec-anchored FROZEN M1; confirmatory '
                 'coverage 1.0, 835 positives.'),
            'secondary_disclosed':
                ('V4.1 composite behavioral_anomaly (4,497; off-design '
                 'per spec 0.2.2) and the unrecentered per-dim '
                 'extremal (5,377) — cross-checks only, never the gate '
                 'surface.'),
        },
        'parameters': {
            'q': cv2.Q, 'alpha_out': cv2.ALPHA_OUT, 'N_BOOT': cv2.N_BOOT,
            'seed_shrink': cv2.SEED_SHRINK, 'seed_ci': cv2.SEED_CI,
            'devsplit_seed': cv2.SEED_DEVSPLIT,
            'confirm_seed': cv2.SEED_CONFIRM,
            'outcome_null': {k: {kk: (round(vv, 6) if
                                    isinstance(vv, float) else vv)
                                 for kk, vv in v.items()}
                             for k, v in outnull.items()},
            'outcome_pool_sizes': {k: len(v)
                                   for k, v in outpool.items()},
            'shrinkage_z':
                {f'{k[0]}|{k[1]}':
                    {kk: (round(vv, 6) if isinstance(vv, float) else vv)
                     for kk, vv in zt.items()}
                 for k, zt in sorted(ztab.items())},
        },
        'dev_evidence_final': {p: {
            'dev_F1': cand['policies'][p]['dev']['confusion']['f1'],
            'dev_precision': cand['policies'][p]['dev']['confusion']['precision'],
            'dev_recall': cand['policies'][p]['dev']['confusion']['recall'],
            'b2b_probe_union':
                cand['policies'][p]['b2b_full']['probe_union_fpr'],
            'b2b_execution_rowOR':
                cand['policies'][p]['b2b_full']['row_or_fpr'],
            'outcome_b2b_probe':
                cand['policies'][p]['b2b_full']['outcome_probe_fpr'],
            'admissible_primary': p in ('S1_shrinkage',
                                        'S4_shrinkage_stratum',
                                        'C1_family_q98'),
        } for p in ('C0_original', 'C1_family_q98', 'S1_shrinkage',
                    'S2_localmax', 'S3_minfloor', 'S4_shrinkage_stratum')},
        'selection_note':
            ('dev-selected (agentprop-cv2-devsplit-202702) on the pre-'
             'registered admissibility gates only (B2B probe-union <= '
             '0.05 + no disabled dim + coverage); NOT on the production '
             'rate.  Among admissible {C1, S1, S4}: S1 chosen — '
             'dominant dev F1 (0.551) with the lowest B2B of any '
             'candidate (0.0148); S4 is a near-tie guard; C0/S2/S3 are '
             'B2B-uncontrolled sensitivity bounds (0.24-0.30), '
             'diagnostics only.'),
    }
    frozen.pop('sha256', None)
    blob = json.dumps(frozen, sort_keys=True).encode()
    frozen['sha256'] = hashlib.sha256(blob).hexdigest()
    json.dump(frozen, open(os.path.join(OUT, 'frozen_candidate.json'),
                           'w'), indent=1)
    # the immutable sidecar (write-once; run_validation verifies it)
    open(pin_path, 'w').write(frozen['sha256'] + '\n')
    print('pinned', pol, '->', frozen['sha256'])


if __name__ == '__main__':
    if 'PYTHONHASHSEED' not in os.environ:
        os.environ['PYTHONHASHSEED'] = '0'
        os.execve(sys.executable, [sys.executable] + sys.argv,
                  os.environ)
    main()
