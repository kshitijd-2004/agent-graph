#!/usr/bin/env python3.11
"""Verify the frozen V4.2 baseline against its manifest (run at V4.3 completion)."""
import hashlib, json, os, sys
HERE=os.path.dirname(os.path.abspath(__file__))
REPO=os.path.abspath(os.path.join(HERE,'..','..','..'))
man=json.load(open(os.path.join(HERE,'manifest.json')))
def sha(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        for chunk in iter(lambda: f.read(1<<20), b''): h.update(chunk)
    return h.hexdigest()
bad=[]
for p,exp in man['artifacts'].items():
    fp=os.path.join(REPO,p)
    if not os.path.exists(fp):
        bad.append((p,'MISSING')); continue
    got=sha(fp)
    if got!=exp: bad.append((p,got[:16]+' != '+exp[:16]))
if bad:
    print('V4.2 FREEZE VIOLATIONS:'); [print('  ',b) for b in bad]; sys.exit(1)
print('V4.2 freeze verified: %d/%d artifacts byte-identical to manifest'%(len(man['artifacts']),len(man['artifacts'])))
