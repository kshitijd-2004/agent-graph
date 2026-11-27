#!/usr/bin/env python3.11
"""Verify the V4.4 final measurement freeze against its manifest.

Run:
    python3.11 analysis/behavioral_divergence_final/v44_freeze/verify.py

The V4.4 freeze pins the selected operating-point implementations, the
frozen v6 grader, the round-2 reference labels (read-only), the cached
inputs, the validation aggregates, the final per-execution objects, and
the v42/v43 freeze manifests (earlier freezes must stay byte-identical).
Any drift is a violation: the V4.4 freeze is the point of no return
before PropHunter development.
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
man = json.load(open(os.path.join(HERE, 'manifest.json')))


def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


bad = []
for p, exp in man['artifacts'].items():
    fp = os.path.join(REPO, p)
    if not os.path.exists(fp):
        bad.append((p, 'MISSING'))
        continue
    got = sha(fp)
    if got != exp:
        bad.append((p, got[:16] + ' != ' + exp[:16]))
if bad:
    print('V4.4 FREEZE VIOLATIONS (%d):' % len(bad))
    for b in bad[:20]:
        print('  ', b)
    sys.exit(1)
print('V4.4 freeze verified: %d/%d artifacts byte-identical to manifest'
      % (len(man['artifacts']), len(man['artifacts'])))
# earlier freezes must ALSO still verify (they are pinned in this
# manifest and have their own verify.py)
for vz, vp in (('v42', 'analysis/behavioral_divergence_final/'
                        'v42_freeze/verify.py'),
               ('v43', 'analysis/behavioral_divergence_final/'
                        'v43_freeze/verify.py')):
    vvp = os.path.join(REPO, vp)
    if os.path.exists(vvp):
        print('re-verifying %s freeze: %s' % (vz, os.path.relpath(
            vvp, REPO)))
        import subprocess
        r = subprocess.run([sys.executable, vvp], capture_output=True,
                           text=True)
        print('  ' + (r.stdout or r.stderr).strip())
        if r.returncode != 0:
            print('  VIOLATION: %s freeze no longer verifies' % vz)
            sys.exit(1)
    else:
        print('note: %s verify.py not found; skipped (its manifest '
              'is pinned in this freeze)' % vz)
