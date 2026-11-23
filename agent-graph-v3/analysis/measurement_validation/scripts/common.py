"""Shared utilities for the independent BND measurement-validation framework.

This package is READ-ONLY with respect to every frozen artifact: the frozen
V4.1 module, the consolidated traces, the specroot manifests, the frozen
ledgers, the v6 correction layer and the v6 outputs.

Every writer routes through :func:`write_json` / :func:`write_jsonl` /
:func:`write_text`, all of which refuse to touch a path outside
``analysis/measurement_validation/``.  That guard is the mechanical expression
of the task's non-destructiveness requirement: a bug in a call site cannot
overwrite a historical result, because the write is rejected before it opens
a file handle.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence

# ── repository layout (verified against the live tree) ───────────────────────
REPO = '/u/kdhande/agent-graph/agent-graph-v3'
ANALYSIS = os.path.join(REPO, 'analysis')
VALIDATION_ROOT = os.path.join(ANALYSIS, 'measurement_validation')
SCRIPTS_DIR = os.path.join(VALIDATION_ROOT, 'scripts')
TEMPLATES_DIR = os.path.join(VALIDATION_ROOT, 'annotation_templates')
REPORTS_DIR = os.path.join(VALIDATION_ROOT, 'reports')
PHASE_C_DIR = os.path.join(VALIDATION_ROOT, 'phase_c')
CACHE_DIR = os.path.join(VALIDATION_ROOT, '.cache')

# ── frozen inputs ────────────────────────────────────────────────────────────
CONS = os.path.join(ANALYSIS, 'consolidated_20261121_bnd_v41')
CONS_TRACES = os.path.join(CONS, 'staged', 'merged', 'traces')
CONS_LEDGER = os.path.join(CONS, 'bnd_v41_run', 'bnd_v41_per_trace.jsonl')
CONS_LEDGER_SUMMARY = os.path.join(CONS, 'bnd_v41_run', 'bnd_v41_summary.json')
CONS_DOWNSTREAM = os.path.join(CONS, 'downstream_results', 'per_trace.jsonl')
CONS_DOWNSTREAM_SUMMARY = os.path.join(CONS, 'downstream_results', 'summary.json')
CONS_PROVENANCE = os.path.join(CONS, 'provenance.jsonl')
CONS_MANIFEST = os.path.join(CONS, 'bnd_v41_run', 'run_manifest.json')

CW_SLICE = os.path.join(ANALYSIS, 'bnd_v41_cw_swe200_provisional')
SPECROOT = os.path.join(CW_SLICE, 'specroot')
FROZEN_MODULE = os.path.join(REPO, 'benchmark', 'baseline_deviation_v41.py')
FROZEN_BASE_MODULE = os.path.join(REPO, 'benchmark', 'baseline_deviation.py')

V6_DIR = os.path.join(ANALYSIS, 'measurement_v6')
V6_PER_TRACE = os.path.join(V6_DIR, 'out', 'per_trace_v6.jsonl')
V6_SUMMARY = os.path.join(V6_DIR, 'out', 'summary_v6.json')

DOWNSTREAM_CORRECTED = os.path.join(ANALYSIS, 'downstream_propagation_corrected')

# ── frozen module identity, pinned at design time ────────────────────────────
#: The consolidated ledger was produced by this exact module revision.  Any
#: deviation means the joined artifacts describe different code, so the join
#: would be invalid rather than merely stale.
CONS_EXPECTED_MODULE_SHA256 = \
    '75f1e77f80d7521a1b094afd087e6c4231cd4bc52d80de62e9929faf1be584ce'
#: The cw_swe200 provisional slice was produced by this revision.  The two
#: copies differ (see FROZEN_MODULE_NOTE); they are different populations and
#: are never joined.
CW_EXPECTED_MODULE_SHA256 = \
    '6f0f7ff3ff88ee745d2c77cdb07f7c882b11e4e658520e85f2db710c178f727d'
FROZEN_MODULE_NOTE = (
    'benchmark/baseline_deviation_v41.py (75f1e77f, the consolidated run) and '
    'analysis/bnd_v41_cw_swe200_provisional/frozen_run/benchmark/'
    'baseline_deviation_v41.py (6f0f7ff3, the cw/swe200 run) are two '
    'different revisions. They differ in one code-review anchor rule '
    '(_issue_specific_anchors basename filtering). The two runs cover '
    'different populations and are never joined; this framework reads the '
    'consolidated revision as "frozen".'
)

# ── design constants (recorded verbatim in every manifest) ───────────────────
DESIGN = {
    'framework_version': 'measurement-validation-v1',
    'dataset': 'analysis/consolidated_20261121_bnd_v41',
    'frozen_module': 'benchmark/baseline_deviation_v41.py',
    'frozen_module_sha256': CONS_EXPECTED_MODULE_SHA256,
    'v6_module_dir': 'analysis/measurement_v6',
    'seed': 20261122,
    'seed_rep_leps': 20261122,
    'seed_rep_benign': 20261123,
    'seed_targeted': 20261124,
    'seed_pairs': 20261125,
    'seed_blind_order_A': 20261126,
    'seed_blind_order_B': 20261127,
    'seed_bootstrap': 20261128,
    'n_traces_total': 300,
    'n_traces_per_family': 100,
    'n_lep_traces_total': 180,
    'n_benign_traces_total': 120,
    'n_rep_lep_per_family': 36,
    'n_rep_benign_per_family': 24,
    'n_targeted_lep_per_family': 24,
    'n_targeted_benign_per_family': 16,
    'n_pairs_total': 150,
    'n_pairs_per_family': 50,
    'families': ['code_review', 'financial_analysis', 'research_synthesis'],
    'leps': ['LEP_HANDOFF_CORRUPTION', 'LEP_INDIRECT_PROMPT_INJECTION',
             'LEP_INPUT_DISREGARD', 'LEP_TOOL_RESULT_CORRUPTION'],
    'topologies': ['branch_and_verify', 'coordinator_workers', 'review_loop'],
    'dimensions': ['assertions', 'decisions', 'actions', 'outcomes'],
    'excluded_lep_code': 'LEP_MEMORY_POISONING',
    'excluded_variants': ['memory_enabled'],
}

#: Trace ids that appear twice, non-identically, in the frozen downstream
#: artifact.  Excluded from every sample and reported as an unresolved
#: dupflicate rather than silently resolved.
KNOWN_DUPLICATE_ROW_IDS = (
    'code_review_swe_042_scikit_learn_scikit_learn_14087.rl.'
    'LEP_TOOL_RESULT_CORRUPTION.single_origin.00.std__b',
    'code_review_swe_059_matplotlib_matplotlib_25311.rl.'
    'LEP_INDIRECT_PROMPT_INJECTION.single_origin.00.std__b',
    'code_review_swe_064_astropy_astropy_14539.rl.'
    'LEP_HANDOFF_CORRUPTION.single_origin.00.std__b',
    'fin_finqa_020_ilmn_2007.bv.LEP_TOOL_RESULT_CORRUPTION.'
    'many_to_one.00.std__b',
    'fin_finqa_130_awk_2017.bv.LEP_INDIRECT_PROMPT_INJECTION.'
    'many_to_one.00.std__b',
    'fin_finqa_280_lly_2008.rl.LEP_INDIRECT_PROMPT_INJECTION.'
    'single_origin.00.std__b',
)


# ── hashing / canonical serialisation ────────────────────────────────────────
def sha256_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def canonical_json(obj: Any) -> str:
    """Byte-stable JSON: sorted keys, no incidental whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(',', ':'),
                      default=str)


def digest(obj: Any, n: int = 16) -> str:
    """Short content hash used for manifests and evidence anchors."""
    return hashlib.sha256(canonical_json(obj).encode()).hexdigest()[:n]


def short_hash(text: str, n: int = 12) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:n]


def mix_seed(base: int, *keys: Any) -> int:
    """Derive a stable RNG seed from a base seed and string keys.

    Replaces the built-in ``hash()`` (randomized per process by
    ``PYTHONHASHSEED`` — which silently breaks reproducibility) with a
    SHA-256 fold.  The result is identical on every machine, Python
    version, and interpreter invocation, and different keys yield
    different seeds.
    """
    h = hashlib.sha256(
        str(base).encode() + b'|' + b'|'.join(str(k).encode() for k in keys)
    ).hexdigest()
    return int(h[:12], 16)


# ── deterministic randomness (version-stable) ────────────────────────────────
class Rng:
    """Thin wrapper over ``random.Random`` exposing only stable primitives.

    ``random.Random.random()`` is a Mersenne-Twister draw whose output stream
    is fixed by the seed across CPython versions and platforms, so the samples
    and bootstrap replicates this framework produces are byte-reproducible.
    ``random.sample`` / ``random.shuffle`` are deliberately NOT used: their
    implementations have changed between Python releases, which would make the
    published sample unreproducible.
    """

    def __init__(self, seed: int):
        self._r = random.Random(seed)
        self.seed = seed

    def uniform(self) -> float:
        return self._r.random()

    def order(self, items: Sequence[Any]) -> List[Any]:
        """A uniformly random permutation, stable across Python versions."""
        idx = list(range(len(items)))
        keys = [(self._r.random(), i) for i in idx]
        keys.sort()
        return [items[k[1]] for k in keys]

    def srs_wor(self, items: Sequence[Any], k: int) -> List[Any]:
        """Simple random sample without replacement of size ``k``."""
        if k > len(items):
            raise ValueError(f'SRSWOR size {k} exceeds population {len(items)}')
        return sorted(self.order(list(items))[:k])

    def choice(self, items: Sequence[Any]) -> Any:
        return items[int(self._r.random() * len(items))]

    def integer(self, lo: int, hi: int) -> int:
        """Inclusive integer draw in [lo, hi]."""
        return lo + int(self._r.random() * (hi - lo + 1))


def bootstrap_index_sets(n_units: int, n_rep: int, seed: int
                         ) -> Iterator[List[int]]:
    """Cluster-bootstrap resample indices (with replacement), reproducible."""
    rng = Rng(seed)
    for _ in range(n_rep):
        yield [int(rng.uniform() * n_units) for _ in range(n_units)]


# ── guarded IO ───────────────────────────────────────────────────────────────
class WriteOutsideValidationRoot(RuntimeError):
    """Raised when a writer is pointed outside the isolated work directory."""


def _assert_isolated(path: str) -> str:
    ap = os.path.realpath(os.path.abspath(path))
    root = os.path.realpath(VALIDATION_ROOT)
    if ap != root and not ap.startswith(root + os.sep):
        raise WriteOutsideValidationRoot(
            f'refusing to write {ap!r}: all framework output must live under '
            f'{root!r}')
    return ap


def ensure_dir(path: str) -> str:
    ap = _assert_isolated(path)
    os.makedirs(ap, exist_ok=True)
    return ap


def write_text(path: str, text: str) -> str:
    ap = _assert_isolated(path)
    ensure_dir(os.path.dirname(ap))
    with open(ap, 'w', encoding='utf-8') as fh:
        fh.write(text)
    return ap


def write_json(path: str, obj: Any, indent: int = 2) -> str:
    return write_text(path, json.dumps(obj, indent=indent, sort_keys=True,
                                       default=str) + '\n')


def write_jsonl(path: str, rows: Iterable[Dict[str, Any]]) -> str:
    ap = _assert_isolated(path)
    ensure_dir(os.path.dirname(ap))
    with open(ap, 'w', encoding='utf-8') as fh:
        for r in rows:
            fh.write(canonical_json(r) + '\n')
    return ap


def read_json(path: str) -> Any:
    with open(path, 'r', encoding='utf-8') as fh:
        return json.load(fh)


def read_jsonl(path: str) -> List[Dict[str, Any]]:
    out = []
    with open(path, 'r', encoding='utf-8') as fh:
        for line in fh:
            if line.strip():
                out.append(json.loads(line))
    return out


def write_csv(path: str, header: Sequence[str],
              rows: Iterable[Sequence[Any]]) -> str:
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator='\n')
    w.writerow(list(header))
    for r in rows:
        w.writerow(list(r))
    return write_text(path, buf.getvalue())


# ── small numeric helpers (pure python: no numpy/scipy dependency) ───────────
def safe_div(a: float, b: float) -> Optional[float]:
    return (a / b) if b else None


def mean(xs: Sequence[float]) -> Optional[float]:
    v = [x for x in xs if x is not None]
    return (sum(v) / len(v)) if v else None


def is_memory_poisoning(code: Optional[str]) -> bool:
    return bool(code) and 'MEMORY_POISONING' in str(code)
