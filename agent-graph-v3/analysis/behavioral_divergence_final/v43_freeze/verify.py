#!/usr/bin/env python3.11
"""Verify the frozen V4.3 stack against its manifest.

Run at V4.4 completion (and at V4.4 production-evaluation start):

    python3.11 analysis/behavioral_divergence_final/v43_freeze/verify.py

The V4.3 freeze is SEPARATE from v42_freeze (the V4.2 development baseline,
which stays the V4.4 comparison baseline alongside V4.3).  V4.4 ships as
new versioned files and must not modify any artifact pinned here.
"""
import hashlib, json, os, sys
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
    print('V4.3 FREEZE VIOLATIONS:')
    for b in bad:
        print('  ', b)
    sys.exit(1)
print('V4.3 freeze verified: %d/%d artifacts byte-identical to manifest'
      % (len(man['artifacts']), len(man['artifacts'])))
