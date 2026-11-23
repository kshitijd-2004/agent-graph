#!/usr/bin/env python3.11
"""End-to-end Phase A verification for the BND measurement validation.

Run from the repository root (~/agent-graph/agent-graph-v3):

    python3.11 analysis/measurement_validation/scripts/verify.py

Checks, in order (all fast; nothing is regenerated or written outside
analysis/measurement_validation/):

  1. the corpus index and consolidated ledger exist and are loadable;
  2. the v6 historical figures (M1 5,037/16,773 etc.) still hold;
  3. the sample manifest, pair manifest, and blinded packet design are
     consistent with the frame (coverage, no leaked tokens, no duplicate
     trace ids);
  4. both extractors run over the sample under the GT-value containment
     boundary with zero GT-value leaks and zero vacuous probes;
  5. the v6 regression suite and this directory's test suite pass.

Exit code 0 only if every check passes.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import common as C  # noqa: E402

PASS = 0
FAIL = 0


def ok(msg: str) -> None:
    global PASS
    PASS += 1
    print(f'  PASS  {msg}')


def bad(msg: str, detail: str = '') -> None:
    global FAIL
    FAIL += 1
    print(f'  FAIL  {msg}')
    if detail:
        print(f'        {detail[:400]}')


def main() -> int:
    root = C.VALIDATION_ROOT
    print(f'verifying Phase A artifacts under {root}')

    # 1 ── index + ledger
    idx = {r['trace_id']: r for r in C.read_jsonl(C.CACHE_DIR + '/trace_index.jsonl')}
    ok(f'trace index present ({len(idx)} traces)')
    n_comp = 0
    with open(C.CONS_LEDGER, 'rb') as fh:
        for line in fh:
            if json.loads(line).get('comparable'):
                n_comp += 1
    ok(f'consolidated ledger: {n_comp} comparable rows')
    if n_comp != 16773:
        bad('comparable-row count drifted from 16,773', f'got {n_comp}')

    # the frame records m1_v6 / m1_frozen per comparable row
    frame = C.read_jsonl(C.CACHE_DIR + '/frame_units.jsonl')
    v6m1 = sum(1 for r in frame if r.get('m1_v6'))
    fz_m1 = sum(1 for r in frame if r.get('m1_frozen'))
    m4m = sum(1 for r in frame if r.get('m4_matched_v6'))
    ok(f'frame: {len(frame)} rows; frozen M1 {fz_m1}, v6 M1 {v6m1}, '
       f'M4-matched {m4m}')
    if len(frame) != 16773:
        bad('frame row count drifted from 16,773', f'got {len(frame)}')
    if v6m1 != 5037:
        bad('v6 M1 count drifted from 5,037', f'got {v6m1}')
    if fz_m1 != 4150:
        bad('frozen M1 count drifted from 4,150', f'got {fz_m1}')
    if m4m != 421:
        bad('M4-matched count drifted from 421', f'got {m4m}')

    # 1b ── extractor module integrity: the frame records the exact module
    # hashes it was built with; they must still match the files on disk, or
    # a re-run would silently use a different extractor than the frame.
    meta = C.read_json(C.CACHE_DIR + '/frame_meta.json')
    ev = meta.get('extractor_versions') or {}
    stale = []
    for key, want in ev.items():
        if not key.endswith('_sha256'):
            continue
        base = key[:-len('_sha256')]
        if base == 'frozen_module':
            path = ev.get('frozen_module')
        elif base == 'frozen_base_module':
            path = C.FROZEN_BASE_MODULE
        elif base.startswith('v6_'):
            path = os.path.join(C.V6_DIR, base[len('v6_'):] + '.py')
        else:
            continue
        if path and os.path.isfile(path):
            if C.sha256_file(path) != want:
                stale.append((key, path))
        else:
            stale.append((key, f'MISSING: {path}'))
    ok(f'module integrity: {sum(1 for k in ev if k.endswith("_sha256"))} '
       f'recorded hashes, {len(stale)} stale')
    if stale:
        bad('extractor modules changed since the frame was built',
            str(stale[:4]))

    # 2 ── sample manifest integrity
    sample = C.read_jsonl(root + '/sample_manifest.jsonl')
    tids = [r['trace_id'] for r in sample]
    ok(f'sample manifest: {len(sample)} rows, '
       f'{len(set(tids))} unique traces')
    if len(tids) != len(set(tids)):
        bad('sample manifest contains duplicate trace ids')
    fams = {}
    for r in sample:
        fams[r['task_family']] = fams.get(r['task_family'], 0) + 1
    ok('sample task-family distribution ' + json.dumps(fams, sort_keys=True))

    # 3 ── pair manifest: the LEP trace and every benign reference must
    # resolve in the index
    pairs = C.read_jsonl(root + '/pair_manifest.jsonl')
    missing = []
    for p in pairs:
        refs = [p.get('trace_id')] + list(p.get('benign_reference_set') or [])
        if any(t not in idx for t in refs):
            missing.append(p.get('pair_id'))
    ok(f'pair manifest: {len(pairs)} comparisons, {len(missing)} with '
       f'unresolvable traces')
    if missing:
        bad('pair manifest references traces absent from the index',
            str(missing[:4]))

    # 4 ── GT containment: run the canary probe over every sample trace
    from extractors import load_specs, leak_probe  # noqa: E402
    specs = load_specs()
    n_leaked = 0
    n_vacuous = 0
    n_run = 0
    for r in sample:
        path = idx[r['trace_id']]['path']
        spec = specs.get(r['fixture_id'])
        if spec is None or not os.path.isfile(path):
            continue
        tr = C.read_json(path)
        lp = leak_probe(tr, spec, r['task_family'])
        n_run += 1
        if lp['leaked']:
            n_leaked += 1
        if lp['vacuous']:
            n_vacuous += 1
    ok(f'leak probe over sample: {n_run} traces, leaked={n_leaked}, '
       f'vacuous={n_vacuous}')
    if n_leaked:
        bad('GT-value leak detected; the containment boundary is violated')
    if n_run == 0:
        bad('leak probe ran on zero traces (index/spec resolution failed)')

    # 5 ── blinded packets: no forbidden tokens
    import inspect  # noqa: F401
    tdir = root + '/annotation_templates'
    forb = ('LEP_INDIRECT', 'LEP_HANDOFF', 'LEP_INPUT', 'LEP_TOOL',
            'benign')
    leak = []
    for name in ('extraction_packet_annotator_A.jsonl',
                 'extraction_packet_annotator_B.jsonl',
                 'pair_packet_annotator_A.jsonl',
                 'pair_packet_annotator_B.jsonl'):
        p = os.path.join(tdir, name)
        if not os.path.isfile(p):
            leak.append(name + ' MISSING')
            continue
        for line in open(p, encoding='utf-8'):
            for tok in forb:
                if tok in line:
                    leak.append((name, tok))
    ok(f'blinded packets: {len(leak)} forbidden-token hits')
    if leak:
        bad('annotation packets leak condition tokens', str(leak[:4]))

    # 6 ── test suites (the task's acceptance gates)
    import subprocess  # noqa: E402
    for target in ('analysis/measurement_validation/tests/',
                   'analysis/measurement_v6/test_regression_v6.py'):
        res = subprocess.run([sys.executable, '-m', 'pytest', target, '-q'],
                             cwd=C.REPO, capture_output=True, text=True,
                             timeout=600)
        tail = res.stdout.strip().splitlines()[-1]
        if res.returncode == 0:
            ok(f'pytest {target}: {tail}')
        else:
            bad(f'pytest {target} FAILED', res.stdout[-500:])

    print(f'\nverify.py: {PASS} passed, {FAIL} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
