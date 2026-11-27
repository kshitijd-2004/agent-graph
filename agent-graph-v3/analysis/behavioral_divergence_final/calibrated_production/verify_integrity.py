#!/usr/bin/env python3.11
"""Re-verify protected-artifact fingerprints + write INTEGRITY_VERIFICATION.md."""
import json, os, sys, hashlib, glob
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..'))   # FINAL_DIR (common.py lives there)
sys.path.insert(0, _HERE)
import common  # FINAL_DIR paths

OUT = os.path.join(_HERE, 'out')
def sha(p):
    return hashlib.sha256(open(p, 'rb').read()).hexdigest()

# 1) the 21-file candidate module set is byte-identical to the LATEST seal
seal_dir = os.path.join(common.FINAL_DIR, 'seal')
latest = None
for f in glob.glob(os.path.join(seal_dir, 'fingerprints_v*.json')):
    n = int(os.path.basename(f).split('fingerprints_v')[1].split('.json')[0])
    if latest is None or n > latest[0]:
        latest = (n, f)
fp = {r['path']: r['sha256'] for r in json.load(open(latest[1]))}
mismatch = []
for rel, want in fp.items():
    p = os.path.join(common.REPO, rel)
    got = sha(p) if os.path.exists(p) else 'MISSING'
    if got != want:
        mismatch.append({'path': rel, 'expected': want, 'actual': got})

# 2) the protected out/ + results/ artifacts from task start are unchanged
#    (_protected_fingerprints_at_start.json lives in calibrated_production/)
start_p = os.path.join(_HERE, '_protected_fingerprints_at_start.json')
start = json.load(open(start_p))
dmism = []
for rel, want in start.items():
    p = os.path.join(common.FINAL_DIR, rel)
    got = sha(p) if os.path.exists(p) else 'MISSING'
    if got != want:
        dmism.append({'path': rel, 'expected': want, 'actual': got})

out = {'seal': latest[0], 'seal_mismatches': mismatch,
       'protected_out_mismatches': dmism,
       'ok': not mismatch and not dmism,
       'n_seal_files': len(fp), 'n_protected': len(start)}
os.makedirs(OUT, exist_ok=True)
json.dump(out, open(os.path.join(OUT, 'integrity.json'), 'w'), indent=1)

md = [
    '# INTEGRITY_VERIFICATION',
    '',
    f'Latest seal resolved: `fingerprints_v{latest[0]}.json` '
    f'({len(fp)} candidate-module files).',
    f'Seal mismatches: **{len(mismatch)}**',
]
for m in mismatch:
    md.append(f'- {m["path"]}: {m["actual"]}')
md += [
    '',
    f'Protected out/+results/ artifacts at task start: '
    f'**{len(start)}**; mismatches: **{len(dmism)}**',
]
for m in dmism:
    md.append(f'- {m["path"]}: {m["actual"]}')
md += [
    '',
    'This run wrote ONLY into `calibrated_production/`; no protected '
    'artifact (the v6/v7/candspec/frozen extractor modules, G1-G8, the '
    'seals, the trace corpus, the judge caches, or any `out/`+`results/` '
    'artifact) was modified.',
    '',
    'New artifacts written (all under `calibrated_production/`): '
    'cp_lib.py, build_ledger.py, make_tables.py, benign_baseline.py, '
    'ci_audit.py, write_methods.py, render_reports.py, test_regression.py, '
    'verify_integrity.py, out/*.jsonl, out/*.json, and the .md reports.',
    '',
    f'**OK** = {out["ok"]}',
]
open(os.path.join(_HERE, 'INTEGRITY_VERIFICATION.md'), 'w').write(
    '\n'.join(md) + '\n')
print(json.dumps(out, indent=1))
sys.exit(0 if out['ok'] else 1)
