"""
behavioral_divergence_final — shared bootstrap.

Single writable root: analysis/behavioral_divergence_final/**.  Everything
else in the repo is a read-only input.  Reuses, by explicit file path (the
v7 base.py convention, so no module shadows another):

  * the frozen V4.1 extractor (byte-pinned 6f0f7ff3 copy under
    analysis/bnd_v41_cw_swe200_provisional/frozen_run/),
  * the v6 correction layer,
  * the v7 candidate layer,
  * the validated GT-containment feed (measurement_validation/extractors).

All writes route through ``wpath()`` which enforces the write guard
(mechanical precedent: measurement_validation/scripts/common.py).
"""
from __future__ import annotations

import hashlib
import glob
import json
import os
import sys
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
FINAL_DIR = os.path.abspath(HERE)
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
ANALYSIS = os.path.join(REPO, 'analysis')
V6_DIR = os.path.join(ANALYSIS, 'measurement_v6')
V7_DIR = os.path.join(ANALYSIS, 'measurement_v7')
VALIDATION_ROOT = os.path.join(ANALYSIS, 'measurement_validation')
SPECROOT = os.path.join(ANALYSIS, 'bnd_v41_cw_swe200_provisional', 'specroot')
CONS = os.path.join(ANALYSIS, 'consolidated_20261121_bnd_v41')
CONS_TRACES = os.path.join(CONS, 'staged', 'merged', 'traces')
CONS_LEDGER = os.path.join(CONS, 'bnd_v41_run', 'bnd_v41_per_trace.jsonl')
CONS_DOWNSTREAM = os.path.join(CONS, 'downstream_results')
VALIDATION_CACHE = os.path.join(VALIDATION_ROOT, '.cache')
OUT = os.path.join(FINAL_DIR, 'out')
RESULTS = os.path.join(FINAL_DIR, 'results')

DIMS = ('assertions', 'decisions', 'actions', 'outcomes')


# ── write guard ──────────────────────────────────────────────────────────────
def wpath(p: str) -> str:
    """Resolve a path and refuse any target outside this directory tree."""
    ap = os.path.abspath(p)
    if not (ap == FINAL_DIR or ap.startswith(FINAL_DIR + os.sep)):
        raise PermissionError(
            f'write guard: {ap!r} is outside {FINAL_DIR}; '
            f'repo artifacts are read-only inputs')
    return ap


def outdir(sub: str = '') -> str:
    d = os.path.join(OUT, sub) if sub else OUT
    os.makedirs(wpath(d), exist_ok=True)
    return wpath(d)


def write_json(obj: Any, *rel: str) -> str:
    d, f = os.path.split(wpath(os.path.join(OUT, *rel)))
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f), 'w') as fh:
        json.dump(obj, fh, indent=2, default=str)
        fh.write('\n')
    return os.path.join(d, f)


def write_jsonl(rows, *rel: str) -> str:
    d, f = os.path.split(wpath(os.path.join(OUT, *rel)))
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f)
    with open(p, 'w') as fh:
        for r in rows:
            fh.write(json.dumps(r, default=str) + '\n')
    return p


def write_text(txt: str, *rel: str) -> str:
    d, f = os.path.split(wpath(os.path.join(RESULTS, *rel)))
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f)
    with open(p, 'w') as fh:
        fh.write(txt)
    return p


# ── module loading (explicit paths; the v7 shadowing-hazard convention) ─────
def _load_by_path(name: str, path: str):
    import importlib.util as il
    if name in sys.modules:
        existing = sys.modules[name]
        if getattr(existing, '__file__', None) == path:
            return existing
    spec = il.spec_from_file_location(name, path)
    mod = il.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_val_extractors():
    """Load the validation framework's GT-feed module the way
    measurement_validation itself does.  ``extractors.py`` does
    ``import common as C`` (its sibling scripts/common.py, which carries
    DESIGN).  The import machinery consults ``sys.modules`` BEFORE the path,
    so this module's own registered name ('common') must be temporarily
    swapped out of the registry for the duration of the exec, then
    restored — otherwise the validation module would bind to US and fail on
    DESIGN."""
    import importlib.util as il
    vdir = os.path.join(VALIDATION_ROOT, 'scripts')
    saved_common = sys.modules.get('common')
    sys.modules.pop('common', None)
    sys.path.insert(0, vdir)
    try:
        p = os.path.join(vdir, 'extractors.py')
        spec = il.spec_from_file_location('extractors', p)
        mod = il.module_from_spec(spec)
        sys.modules['extractors'] = mod
        spec.loader.exec_module(mod)
    finally:
        if saved_common is not None:
            sys.modules['common'] = saved_common
        try:
            sys.path.remove(vdir)
        except ValueError:
            pass
    return mod


def _bootstrap() -> None:
    # FINAL_DIR FIRST: this module's helpers win; the validation-side module
    # names are always loaded by explicit path, never by bare import.
    for p in (FINAL_DIR, REPO, V6_DIR, V7_DIR):
        if p not in sys.path:
            sys.path.insert(0, p)


def pipelines():
    """Import frozen + v6 read-only, exactly as v7/base.py does."""
    _bootstrap()
    from benchmark import baseline_deviation_v41 as FROZEN      # noqa
    V6CORE = _load_by_path('extract_core', os.path.join(V6_DIR, 'extract_core.py'))
    V6F = _load_by_path('features_v6', os.path.join(V6_DIR, 'features_v6.py'))
    V6D = _load_by_path('distances', os.path.join(V6_DIR, 'distances.py'))
    V6U = _load_by_path('union', os.path.join(V6_DIR, 'union.py'))
    V6G = _load_by_path('graders', os.path.join(V6_DIR, 'graders.py'))
    M4 = _load_by_path('m4_matched', os.path.join(V6_DIR, 'm4_matched.py'))
    VAL_EX = _load_val_extractors()
    return {'FROZEN': FROZEN, 'V6CORE': V6CORE, 'V6F': V6F, 'V6D': V6D,
            'V6U': V6U, 'V6G': V6G, 'M4': M4, 'VAL_EX': VAL_EX}


def load_specs(force: bool = False) -> Dict[str, Dict[str, Any]]:
    """fixture_id -> full spec manifest (WITH GT; graders only)."""
    out: Dict[str, Dict[str, Any]] = {}
    for d in sorted(os.listdir(SPECROOT)):
        p = os.path.join(SPECROOT, d, 'manifest.json')
        if os.path.isfile(p):
            try:
                out[d] = json.load(open(p))
            except Exception:
                continue
    return out


def fixture_id_of(trace_id: str) -> str:
    return trace_id.split('.')[0]


# ── frozen-artifact integrity ────────────────────────────────────────────────
#: sealed fingerprints (FROZEN_PROTOCOL §4, machine-readable).
SEALED: List[Dict[str, str]] = json.load(
    open(os.path.join(OUT, '..', 'seal', 'fingerprints.json'))
) if os.path.exists(os.path.join(OUT, '..', 'seal', 'fingerprints.json')) else []


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def check_sealed(repo: Optional[str] = None) -> Dict[str, Any]:
    """Re-verify every sealed fingerprint.  Returns {'ok': bool, 'mismatches': []}.

    Verifies against the LATEST re-pinned instrument state
    (the LATEST repin: fingerprints_v4 > v3 > v2 > original) when
    present, else the original `fingerprints.json`.  Original +
    intermediate seals are preserved untouched for the record."""
    repo = repo or REPO
    fn = None
    # latest repin wins: fingerprints_v*.json (highest N) else original
    vfiles = sorted(glob.glob(os.path.join(FINAL_DIR, 'seal',
                                            'fingerprints_v*.json')),
                    key=lambda x: int(x.split('fingerprints_v')[1]
                                      .split('.json')[0]))
    cands = [os.path.basename(x) for x in reversed(vfiles)] + \
        ['fingerprints.json']   # latest repin first
    for cand in cands:
        p3 = os.path.join(FINAL_DIR, 'seal', cand)
        if os.path.exists(p3):
            fn = p3
            break
    if fn is None:
        fn = os.path.join(FINAL_DIR, 'seal', 'fingerprints.json')
    if not os.path.exists(fn):
        return {'ok': True, 'note': 'seal not yet written; nothing to check'}
    fp = {r['path']: r['sha256'] for r in json.load(open(fn))}
    mismatches = []
    for rel, want in sorted(fp.items()):
        p = os.path.join(repo, rel)
        if not os.path.exists(p):
            mismatches.append({'path': rel, 'expected': want, 'actual': 'MISSING'})
        else:
            got = sha256_of(p)
            if got != want:
                mismatches.append({'path': rel, 'expected': want, 'actual': got})
    return {'ok': not mismatches, 'n_checked': len(fp), 'mismatches': mismatches}
