#!/usr/bin/env python3.11
"""calibration_v2 — fingerprint protected artifacts (before/after the repair).

Mirrors calibration_sensitivity_audit/_fingerprint.py but snapshots into
calibration_v2/ledger/ only.  Run:  python3.11 _fingerprint.py before
                                          python3.11 _fingerprint.py after
Covers: the sealed instrument state (latest repin must verify ok=True), the
key read-only ledgers/index, the calibrated_production outputs, the reference
artifacts, and the judge cache (untouched — no new calls in this repair).
"""
import hashlib, json, os, time

HERE = os.path.dirname(os.path.abspath(__file__))
V2 = os.path.abspath(HERE)
FINAL = os.path.abspath(os.path.join(HERE, '..'))
REPO = os.path.abspath(os.path.join(FINAL, '..', '..'))
CALP = os.path.join(FINAL, 'calibrated_production')


def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as fh:
        for ch in iter(lambda: fh.read(1 << 20), b''):
            h.update(ch)
    return h.hexdigest()


def main():
    out_dir = os.path.join(V2, 'ledger')
    os.makedirs(out_dir, exist_ok=True)
    snap = {'generated_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'seal_states': {}, 'key_artifacts': {}, 'calibrated_production': {}}
    for cand in ('fingerprints.json',
                 *sorted(f'fingerprints_v{i}.json' for i in range(2, 9))):
        p = os.path.join(FINAL, 'seal', cand)
        if not os.path.exists(p):
            continue
        rec = {r['path']: r['sha256'] for r in json.load(open(p))}
        mism = []
        for rel, want in sorted(rec.items()):
            ap = os.path.join(REPO, rel)
            got = sha(ap) if os.path.exists(ap) else 'MISSING'
            if got != want:
                mism.append({'path': rel, 'expected': want, 'actual': got})
        snap['seal_states'][cand] = {'n_files': len(rec),
                                     'ok': not mism, 'n_mismatches': len(mism),
                                     'mismatch_paths': [m['path'] for m in mism]}
    key = [
        'analysis/measurement_v6/out/per_trace_v6.jsonl',
        'analysis/measurement_v7/out/per_trace_v7.jsonl',
        'analysis/consolidated_20261121_bnd_v41/bnd_v41_run/bnd_v41_per_trace.jsonl',
        'analysis/consolidated_20261121_bnd_v41/downstream_results/per_trace.jsonl',
        'analysis/behavioral_divergence_final/out/per_trace_candspec.jsonl',
        'analysis/measurement_validation/.cache/trace_index.jsonl',
        'analysis/behavioral_divergence_final/out/reference_heldout_pairs.jsonl',
        'analysis/behavioral_divergence_final/out/partitions_final.json',
        'analysis/behavioral_divergence_final/calibrated_production/out/cache_cells_full.json',
        'analysis/behavioral_divergence_final/calibrated_production/out/ledger_v6_calibrated.jsonl',
    ]
    for rel in key:
        ap = os.path.join(REPO, rel)
        snap['key_artifacts'][rel] = sha(ap) if os.path.exists(ap) else 'MISSING'
    for fn in sorted(os.listdir(CALP)):
        p = os.path.join(CALP, fn)
        if os.path.isfile(p):
            snap['calibrated_production'][fn] = {'sha256': sha(p)}
        elif fn == 'out':
            snap['calibrated_production']['out'] = {}
            for f2 in sorted(os.listdir(p)):
                p2 = os.path.join(p, f2)
                snap['calibrated_production']['out'][f2] = {
                    'sha256': sha(p2), 'size': os.path.getsize(p2)}
    tag = sys.argv[1] if len(sys.argv) > 1 else 'snapshot'
    outp = os.path.join(out_dir, f'fingerprints_{tag}.json')
    with open(outp, 'w') as fh:
        json.dump(snap, fh, indent=1, sort_keys=True)
    for st, v in snap['seal_states'].items():
        print(f"  {st}: {v['n_files']} files ok={v['ok']}"
              + (f" (mismatches: {v['n_mismatches']})" if v['n_mismatches'] else ''))
    latest = sorted([k for k in snap['seal_states'] if k.startswith('fingerprints_v')])
    print('latest seal ok:', snap['seal_states'][latest[-1]]['ok'] if latest else 'n/a')
    print('wrote', outp)


if __name__ == '__main__':
    import sys
    main()
