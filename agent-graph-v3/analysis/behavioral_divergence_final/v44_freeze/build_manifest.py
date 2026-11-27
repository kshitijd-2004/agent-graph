#!/usr/bin/env python3.11
"""Build the V4.4 final measurement freeze manifest (SHA-256).

Pins the V4.4 stack by group so "no label modification, no grader
modification, no extractor modification, earlier freezes unchanged"
are all VERIFIABLE byte-for-byte against this manifest:

  implementation    the V4.4 measurement + calibration + finalizer
  frozen_grader     the frozen v6 five-way grader (read-only)
  label_loader      the round-2 reference label loader (read-only)
  round2_labels     the round-2 reference labels + judge caches
                    (frozen; NEVER modified by V4.4)
  cached_inputs     the V4.4 ledger, checkpoint cache, frozen v6
                    grade ledger + summary
  validation        the sweep/dev/observed/verify aggregates that
                    anchor the selected operating points
  final_outputs     the V4.4 final per-execution + aggregate objects
  earlier_freezes   the v42/v43 manifests (must stay byte-identical)

Run:  python3.11 .../v44_freeze/build_manifest.py
Writes: v44_freeze/manifest.json
"""
import hashlib
import json
import os


def _repo():
    """.../analysis/behavioral_divergence_final/v44_freeze -> repo root."""
    h = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(h, '..', '..', '..'))


def main():
    BASE = _repo()
    FREEZE = os.path.abspath(os.path.join(
        BASE, 'analysis', 'behavioral_divergence_final', 'v44_freeze'))

    def sha(p):
        h = hashlib.sha256()
        with open(p, 'rb') as fh:
            for ch in iter(lambda: fh.read(1 << 20), b''):
                h.update(ch)
        return h.hexdigest()

    pins = []

    def pin(rel, group):
        ab = os.path.join(BASE, rel)
        if not os.path.exists(ab):
            raise SystemExit('MISSING: %s' % rel)
        pins.append({'path': rel, 'group': group, 'sha256': sha(ab)})

    CAL = 'analysis/behavioral_divergence_final/calibration_v3'

    for f in ('m44.py', 'sweep_v44.py', 'sweep_m2_fine_v44.py',
              'dev_eval_v44.py', 'compute_production_v44.py',
              'compute_checkpoint_v44.py', 'final_v44.py',
              'observed_degradation_v44.py', 'verify_o4_sets.py'):
        pin(CAL + '/' + f, 'implementation')

    pin('analysis/measurement_v6/m4_matched.py', 'frozen_grader')
    pin('analysis/behavioral_divergence_final/calibration_v2/scripts/'
        'm1_gate_v43.py', 'label_loader')

    RV = 'analysis/behavioral_divergence_final/calibration_v2_' \
         'validation/recovery'
    pin(RV + '/CORRECTED_SAMPLE_MANIFEST.jsonl', 'round2_labels')
    if os.path.exists(os.path.join(BASE, RV, 'out', 'pilot_packs.json')):
        pin(RV + '/out/pilot_packs.json', 'round2_labels')
    for root, _, files in os.walk(os.path.join(BASE, RV, 'out',
                                                'judge_cache')):
        for f in sorted(files):
            pin(os.path.relpath(os.path.join(root, f), BASE),
                'round2_labels')
    for root, _, files in os.walk(os.path.join(BASE, RV, 'out',
                                                'packets')):
        for f in sorted(files):
            pin(os.path.relpath(os.path.join(root, f), BASE),
                'round2_labels')

    pin('analysis/full_production_baseline_deviation_v44/'
        'bnd_v44_per_trace.jsonl', 'cached_inputs')
    pin(CAL + '/out_v44/checkpoint_pairs_v44.jsonl', 'cached_inputs')
    pin('analysis/measurement_v6/out/per_trace_v6.jsonl',
        'cached_inputs')
    pin('analysis/measurement_v6/out/summary_v6.json', 'cached_inputs')

    for f in ('sweep_v44.json', 'sweep_m2_fine_v44.json',
              'dev_eval_v44.json', 'observed_degradation_v44.json',
              'verify_o4_sets.json', 'm2m3_production_v44.json'):
        pin(CAL + '/out_v44/' + f, 'validation')

    for f in ('per_execution_v44.jsonl', 'm2_rows_O4_v44.jsonl',
              'final_aggregates_v44.json',
              'eligibility_census_v44.json'):
        pin(CAL + '/out_v44/final/' + f, 'final_outputs')

    pin('analysis/behavioral_divergence_final/v42_freeze/manifest.json',
        'earlier_freezes')
    pin('analysis/behavioral_divergence_final/v43_freeze/manifest.json',
        'earlier_freezes')

    manifest = {
        'freeze': 'v44',
        'immutable': True,
        'note': ('V4.4 FINAL measurement freeze.  Operating points '
                 'M1-C (c=0.50, alpha=0.05; self-cell recentered gate '
                 'on the V4.4 ledger) and M2-O4 (q=0.98, alpha=0.10; '
                 'checkpoint gate).  Three headline metrics: M1 whole-'
                 'trace divergence; M2 downstream divergence with '
                 'dependency-supported propagation as an internal '
                 'breakdown; M3 observed matched task degradation '
                 'independent of dependency support with the frozen '
                 'M4 as an internal breakdown.  Aggregation-only '
                 'finalization over cached artifacts; the frozen v6 '
                 'grader, round-2 reference labels and judge caches '
                 'are pinned read-only; the v42/v43 freeze manifests '
                 'are pinned unchanged.  Verify: python3.11 '
                 'analysis/behavioral_divergence_final/v44_freeze/'
                 'verify.py'),
        'operating_points': {
            'M1_selected': {
                'name': 'M1-C', 'c': 0.50, 'alpha': 0.05,
                'form': ('self-cell recentered: continuous dims '
                         '(assertions/actions) fire on '
                         '(T_max-B) > c*th, c=0.50; discrete dims '
                         '(decisions/outcomes) fire on the LEP-free '
                         '3-pair disagree-mass null, one-sided '
                         'P(null>=k)<=0.05; logical OR, no composite'),
            },
            'M2_selected': {
                'name': 'M2-O4', 'q': 0.98, 'alpha': 0.10,
                'form': ('checkpoint gate: continuous channels vs '
                         'the fixture-disjoint family q98 threshold; '
                         'discrete channels vs the LEP-free 3-pair '
                         'disagree-mass null at alpha=0.10; '
                         'dependency-supported propagation (reach in '
                         'verified|plausible AND delivery verified) '
                         'is an internal breakdown, never added into '
                         'the M2 count'),
            },
            'M3': ('observed matched task degradation: the frozen '
                   'five-way criterion (LEP failure AND >=1 '
                   'gradeable matched benign reference correct, '
                   'ANY-correct aggregation) with the dependency-'
                   'support gate removed; computed independently of '
                   'M1/M2/dependency support over cached task '
                   'grades; not_gradeable rows NAMED, never counted '
                   'as failures'),
        },
        'artifacts': {p['path']: p['sha256'] for p in pins},
        'groups': {g: [p['path'] for p in pins if p['group'] == g]
                   for g in ['implementation', 'frozen_grader',
                             'label_loader', 'round2_labels',
                             'cached_inputs', 'validation',
                             'final_outputs', 'earlier_freezes']},
    }
    os.makedirs(FREEZE, exist_ok=True)
    with open(os.path.join(FREEZE, 'manifest.json'), 'w') as fh:
        json.dump(manifest, fh, indent=1, sort_keys=True)
    print('V4.4 manifest written: %d pinned artifacts' %
          len(manifest['artifacts']))
    for g, paths in manifest['groups'].items():
        print('  %-16s %d' % (g, len(paths)))


if __name__ == '__main__':
    main()
