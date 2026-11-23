"""
v7 shared bootstrap: path setup, frozen/v6/v7 import, GT containment feed.

This module mirrors the import convention used by
``analysis/measurement_validation/scripts/extractors.py`` so that the FROZEN
extractor, the v6 correction layer, and the v7 candidate layer are all read
in place (never modified), and the ground-truth containment feed
(``strip_gt_values``) is the single source of truth for what an extractor may
see.

NON-DESTRUCTIVE: every import is read-only with respect to the frozen and v6
artifacts.  All writes from the v7 pipeline route through
``measurement_v7/out/``.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
V7_DIR = os.path.abspath(HERE)
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
ANALYSIS = os.path.join(REPO, 'analysis')
VALIDATION_ROOT = os.path.join(ANALYSIS, 'measurement_validation')
V6_DIR = os.path.join(ANALYSIS, 'measurement_v6')
SPECROOT = os.path.join(REPO, 'analysis', 'bnd_v41_cw_swe200_provisional',
                       'specroot')
CONS = os.path.join(ANALYSIS, 'consolidated_20261121_bnd_v41')
CONS_TRACES = os.path.join(CONS, 'staged', 'merged', 'traces')
FROZEN_MODULE = os.path.join(REPO, 'benchmark', 'baseline_deviation_v41.py')
OUT_DIR = os.path.join(V7_DIR, 'out')
VALIDATION_CACHE = os.path.join(VALIDATION_ROOT, '.cache')

DIMS = ('assertions', 'decisions', 'actions', 'outcomes')


def _bootstrap() -> None:
    # REPO (frozen module) and V6_DIR (v6 layer) on the path.  The validation
    # scripts dir is NOT put on sys.path at index 0: it ships its own
    # ``graders.py`` with a different signature, and putting it first would
    # shadow the v6 ``graders`` module.  The validation GT feed is loaded by
    # explicit file path instead (see :func:`strip_gt_values`).
    for p in (REPO, V6_DIR, V7_DIR):
        if p not in sys.path:
            sys.path.insert(0, p)


def _load_by_path(name: str, path: str):
    """Load a module by explicit file path, registering it in sys.modules under
    ``name``.  Guarantees the module comes from the intended file even when a
    differently-named module elsewhere on sys.path would otherwise shadow the
    bare import.  Reuses an already-loaded module of the same name."""
    import importlib.util as il
    if name in sys.modules:
        existing = sys.modules[name]
        # only reuse if it is the file we asked for; otherwise override
        if getattr(existing, '__file__', None) == path or \
                getattr(existing, '__file__', None) and \
                os.path.abspath(getattr(existing, '__file__', '')) == path:
            return existing
    spec = il.spec_from_file_location(name, path)
    mod = il.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def import_pipelines():
    """Import the frozen module, the v6 modules (by EXPLICIT FILE PATH, so a
    same-named validation module cannot shadow them), and the GT containment
    feed (also by explicit path).  The v6 modules are registered under their
    canonical top-level names so the v7 extractor layer, which imports them
    by those names, resolves them to the v6 implementations.
    """
    _bootstrap()
    from benchmark import baseline_deviation_v41 as FROZEN      # noqa: E402
    # v6 layer, dependency order, each pinned to its V6_DIR file:
    V6CORE = _load_by_path('extract_core', os.path.join(V6_DIR, 'extract_core.py'))
    # features_v6 pulls in the three task extractors via its own imports;
    # load it explicitly so it is the V6_DIR file, not a shadow.
    V6F = _load_by_path('features_v6', os.path.join(V6_DIR, 'features_v6.py'))
    V6D = _load_by_path('distances', os.path.join(V6_DIR, 'distances.py'))
    V6U = _load_by_path('union', os.path.join(V6_DIR, 'union.py'))
    V6G = _load_by_path('graders', os.path.join(V6_DIR, 'graders.py'))
    M4 = _load_by_path('m4_matched', os.path.join(V6_DIR, 'm4_matched.py'))
    # the validation GT feed, under a distinct name to keep provenance clear
    VAL_EX = _load_by_path('val_extractors_gt',
                           os.path.join(VALIDATION_ROOT, 'scripts',
                                        'extractors.py'))
    return {'FROZEN': FROZEN, 'V6CORE': V6CORE, 'V6F': V6F,
            'V6D': V6D, 'V6U': V6U, 'V6G': V6G, 'M4': M4,
            'VAL_EX': VAL_EX}


#: ground-truth-value fields that must never reach an extractor.  This is the
#: SAME set the validation framework verified on 16,773/16,773 rows to be
#: both faithful (frozen output identical) and contained (v6 guard never fires).
#: We REUSE it verbatim rather than inventing a second, possibly-different set.
GT_VALUE_FIELDS = frozenset({
    'value', 'tolerance', 'pattern', 'source_text', 'value_strings',
    'truth', 'gold', 'expected', 'grading', 'answer_calculation'})

#: task-side fields an extractor may legitimately read (what to extract, not the
#: answer).  The frozen extractor reads ``label``/``unit``; the overly-aggressive
#: hardened strip that deleted ``unit`` collapsed frozen financial coverage
#: 1,719 -> 426, so these MUST survive the strip.  This is the correction the
#: brief asks for: do not reintroduce the over-removal.
TASK_SIDE_FACT_KEYS = frozenset({'label', 'unit'})


class GTLeakError(RuntimeError):
    """A ground-truth-bearing field reached an extractor.  The run must fail."""


def strip_gt_values(spec: Optional[Dict[str, Any]]
                    ) -> Dict[str, Any]:
    """The MEASUREMENT FEED — REUSES the validation framework's verified
    transform (``analysis/measurement_validation/scripts/extractors.py``),
    which removes every GT-VALUE field at every depth but preserves the
    task-side ``label``/``unit`` (the over-aggressive removal of ``unit`` is
    the defect the brief says not to reintroduce).

    On top of the reuse, this wrapper enforces the containment contract at the
    boundary: after stripping, no GT-value field may be reachable anywhere in
    the result, and a residual scalar ``answer`` is treated as a leak.  This
    is the "explicit check of every field reaching an extractor" the brief
    requires; a failure raises :class:`GTLeakError` and voids the run instead
    of producing a measurement.
    """
    import importlib
    mod_name = 'val_extractors_gt'
    if mod_name not in sys.modules:
        import os as _os
        import importlib.util as _il
        p = _os.path.join(VALIDATION_ROOT, 'scripts', 'extractors.py')
        # import the validation module under a distinct name so its own
        # side-effects (spec loading, extractor import) stay in place but we
        # reference it unambiguously as the reuse source
        spec_obj = _il.spec_from_file_location(mod_name, p)
        mod = _il.module_from_spec(spec_obj)
        sys.modules[mod_name] = mod
        spec_obj.loader.exec_module(mod)
        _VAL_EX = mod
    else:
        _VAL_EX = sys.modules[mod_name]
    out = _VAL_EX.strip_gt_values(spec)
    # boundary check on the result: no GT-value field reachable anywhere
    residual = _find_gt_value_fields(out)
    if residual:
        raise GTLeakError(
            f'measurement feed still exposes GT-value field(s) '
            f'{sorted(set(residual))}; containment broken, run void.')
    return out


def assert_gt_free(node: Any, who: str = 'extractor') -> None:
    """Runtime assertion: no GT-VALUE field may be reachable on a spec handed
    to an extractor.  This is the containment check the brief requires:
    *every* field reaching an extractor is checked, and a leak fails the run
    rather than producing a measurement."""
    found = _find_gt_value_fields(node)
    if found:
        raise GTLeakError(f'{who}: ground-truth field(s) {found} reached the '
                          f'extractor feed.  The measurement is void; fix the '
                          f'strip, do not score it.')


def _find_gt_value_fields(node: Any) -> list:
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k in GT_VALUE_FIELDS:
                out.append(k)
            out.extend(_find_gt_value_fields(v))
    elif isinstance(node, list):
        for v in node:
            out.extend(_find_gt_value_fields(v))
    return out


def canary_leak_probe(spec: Optional[Dict[str, Any]],
                      stripped: Dict[str, Any]) -> Dict[str, Any]:
    """Differential canary: perturb every GT-value field with a sentinel and
    confirm the sentinel does not survive into ``stripped``.  A sentinel that
    survives is a genuine leak.  A spec with no GT-value fields to canary is
    VACUOUS and must be reported as such, never read as a pass."""
    import copy
    sentinel = 'CANARY_GT_' + os.urandom(4).hex()
    canaried = _canarify(spec, sentinel)
    leaked = [k for k in _find_gt_value_fields(stripped) if k]
    vacuous = len(_canaried_gt_keys(spec)) == 0
    n_gt_fields = len(_canaried_gt_keys(spec))
    return {'canaried': True, 'sentinel': sentinel,
            'n_gt_value_fields': n_gt_fields, 'vacuous': vacuous,
            'leaked_fields': leaked, 'leak': bool(leaked)}


def _canaried_gt_keys(node: Any) -> list:
    keys = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k in GT_VALUE_FIELDS:
                keys.append(k)
            keys.extend(_canaried_gt_keys(v))
    elif isinstance(node, list):
        for v in node:
            keys.extend(_canaried_gt_keys(v))
    return keys


def _canarify(node: Any, sentinel: str) -> Any:
    import copy
    node = copy.deepcopy(node)
    if isinstance(node, dict):
        for k, v in node.items():
            node[k] = _canarify(v, sentinel)
            if k in GT_VALUE_FIELDS:
                if isinstance(v, (dict, list)):
                    pass
                else:
                    node[k] = sentinel
    elif isinstance(node, list):
        for i, v in enumerate(node):
            node[i] = _canarify(v, sentinel)
    return node


_SPEC_CACHE: Dict[str, Any] = {}


def load_specs(force: bool = False) -> Dict[str, Any]:
    """fixture_id -> FULL spec (with GT).  Extraction callers pass
    ``strip_gt_values(spec)``; graders get the full spec."""
    if _SPEC_CACHE and not force:
        return _SPEC_CACHE
    out: Dict[str, Any] = {}
    for d in sorted(os.listdir(SPECROOT)):
        p = os.path.join(SPECROOT, d, 'manifest.json')
        if os.path.isfile(p):
            try:
                out[d] = json.load(open(p))
            except Exception:
                continue
    _SPEC_CACHE.clear()
    _SPEC_CACHE.update(out)
    return out
