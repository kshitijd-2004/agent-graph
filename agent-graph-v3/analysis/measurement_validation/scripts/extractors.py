#!/usr/bin/env python3.11
"""Run BOTH extractors (frozen V4.1 and v6) on one trace, under a leak guard.

READ-ONLY.  Imports the frozen module and the v6 correction layer in place;
neither is modified, copied, or re-staged.

GROUND-TRUTH CONTAINMENT (task §10)
-----------------------------------
Two things must be kept distinct, and the whole module hinges on it:

  * **GT-VALUE fields** disclose the expected answer: ``value``,
    ``tolerance``, ``pattern``, ``source_text``, ``value_strings``, ``truth``,
    ``gold``, ``expected``, ``grading``, ``answer_calculation``.  An extractor
    must never READ these, or the divergence measurement is circular.
  * **TASK-SIDE fields** say WHAT to extract but not the answer: ``label``,
    ``unit``, issue ids, claim labels, the ``required_facts``/
    ``required_issues`` structure.  The frozen extractor legitimately reads
    ``required_facts[*]['label']`` and ``required_facts['answer']['unit']``.

The correct containment transform is ``strip_gt_values``: it removes exactly the
GT-VALUE fields and preserves the task-side ones.  This is verified, not assumed:

  * removing GT-VALUE fields changes the frozen extractor's output on
    **0 of 16,773** rows and reproduces the frozen ledger exactly, and
  * it never trips v6's ``assert_gt_free_spec`` guard (0 of 16,773).

Aggressively removing the *keys* instead (the older ``strip_gt_hardened``)
deletes the ``required_facts['answer']`` entry **including its task-side
``unit``**, which collapses frozen financial answer coverage (1,719 → 426
observed) and diverges from the ledger on ~57 % of financial rows.  That
form is therefore kept only as an AUDIT primitive ("is any GT key reachable
anywhere?"), never as the extraction feed.  See ``strip_gt_hardened``.

:func:`leak_probe` checks GT-*value* invariance: it extracts on the full spec
and on the value-stripped spec and reports whether any GT value actually
influenced the output.  A difference attributable to a task-side field (e.g.
``unit``) is a legitimate dependence, not a leak; a difference attributable to
a GT-value field is a real circular leak.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import common as C  # noqa: E402

# ── import the frozen and corrected pipelines IN PLACE ───────────────────────
if C.REPO not in sys.path:
    sys.path.insert(0, C.REPO)
if C.V6_DIR not in sys.path:
    sys.path.insert(0, C.V6_DIR)

from benchmark import baseline_deviation_v41 as FROZEN      # noqa: E402
import extract_core as V6CORE                                # noqa: E402
import features_v6 as V6F                                    # noqa: E402
import union as V6U                                          # noqa: E402

DIMS = C.DESIGN['dimensions']

#: spec fields whose VALUES disclose the expected answer.  An extractor must
#: never read these.  This is the set that ``strip_gt_values`` removes.
#: Verified empirically (all-families check): removing exactly these fields
#: leaves the frozen extractor output identical on 16,773/16,773 rows and
#: never trips v6's assert_gt_free_spec guard (0/16,773).
GT_VALUE_FIELDS = frozenset({
    'value', 'tolerance', 'pattern', 'source_text', 'value_strings',
    'truth', 'gold', 'expected', 'grading', 'answer_calculation'})

#: backward-compatible alias used by older callers/tests
GT_VALUE_KEYS = GT_VALUE_FIELDS

#: The graded financial answer is stored as a required_facts ENTRY named
#: 'answer' (a dict with label/value/tolerance/unit/pattern/source_text).  The
#: entry itself is task-side infrastructure — the frozen extractor reads its
#: `unit` — so only its GT-VALUE subfields are stripped; the entry survives as
#: a {label, unit, ...} shell.  A *scalar* `answer` anywhere (a raw graded
#: value, not a fact entry) is itself a GT value and is always dropped.
GT_ANSWER_ENTRY = 'answer'

#: task-side fields an extractor legitimately reads (what to extract, not the
#: answer).  Never stripped by ``strip_gt_values``.
TASK_SIDE_FACT_KEYS = frozenset({'label', 'unit'})

_SPEC_CACHE: Dict[str, Any] = {}


def load_specs(force: bool = False) -> Dict[str, Any]:
    """fixture_id -> FULL spec (with GT).  Extraction callers pass a stripped
    copy only where the extractor enforces it; graders get the full spec."""
    if _SPEC_CACHE and not force:
        return _SPEC_CACHE
    out = {}
    for d in sorted(os.listdir(C.SPECROOT)):
        p = os.path.join(C.SPECROOT, d, 'manifest.json')
        if os.path.isfile(p):
            try:
                out[d] = C.read_json(p)
            except Exception:                                   # noqa: BLE001
                continue
    _SPEC_CACHE.clear()
    _SPEC_CACHE.update(out)
    return _SPEC_CACHE


def extractor_versions() -> Dict[str, Any]:
    """Content hashes of every module whose behaviour a measurement depends on.

    Recorded in the sample manifest so a later reader can tell whether a
    re-run used the same extractors as this one.
    """
    v = {
        'frozen_module': C.FROZEN_MODULE,
        'frozen_module_sha256': C.sha256_file(C.FROZEN_MODULE),
        'frozen_base_module_sha256': C.sha256_file(C.FROZEN_BASE_MODULE),
        'frozen_version_string': getattr(FROZEN, 'VERSION', None),
    }
    for name in ('extract_core', 'extract_financial', 'extract_research',
                 'extract_code', 'features_v6', 'distances', 'union',
                 'graders'):
        p = os.path.join(C.V6_DIR, name + '.py')
        v[f'v6_{name}_sha256'] = C.sha256_file(p) if os.path.isfile(p) else None
    return v


def _frozen_jsonable(f) -> Dict[str, Any]:
    """Frozen ``FeatureValue`` -> comparable dict, plus its key maps."""
    def fv(v):
        return {'state': getattr(v, 'state', None),
                'value': getattr(v, 'value', None),
                'basis': getattr(v, 'basis', None)}
    fam = f.task_family
    return {
        'amap': {k: fv(v) for k, v in f.assertion_map(fam).items()},
        'dmap': {k: fv(v) for k, v in f.decision_map(fam).items()},
        'final_answer': fv(f.final_answer),
        'final_stances': {k: fv(v) for k, v in f.final_stances.items()},
        'final_claims': {k: fv(v) for k, v in f.final_claims.items()},
        'claim_verdicts': {k: fv(v) for k, v in f.claim_verdicts.items()},
        'financial_facts': {k: fv(v) for k, v in f.financial_facts.items()},
        'issue_stances': {k: fv(v) for k, v in f.issue_stances.items()},
        'coverage': f.coverage(fam),
        'action_counters': {'|'.join(map(str, k)): n
                            for k, n in f.action_counters.items()},
        'n_actions': sum(f.action_counters.values()),
        'diagnostics': list(f.diagnostics),
        'raw_action_counters': {'|'.join(map(str, k)): n
                                for k, n in f.raw_action_counters.items()},
        'code_files': list(f.code_files),
        'report_files': list(f.report_files),
        'final_response_present': bool(f.final_response_present),
    }


def _v6_jsonable(f) -> Dict[str, Any]:
    def fv(v):
        return {'state': v.state, 'value': v.value, 'basis': v.basis,
                'span': list(v.span) if v.span else None}
    return {
        'amap': {k: fv(v) for k, v in f.assertions.items()},
        'dmap': {k: fv(v) for k, v in f.decisions.items()},
        'answer': fv(f.answer) if f.answer else None,
        'action_counters': {'|'.join(map(str, k)): n
                            for k, n in f.actions.items()},
        'n_actions': sum(f.actions.values()),
        'decision_source': f.decision_source,
        'notes': list(f.notes),
        'n_modified_files': len(f.modified),
    }


def run_both(trace: Dict[str, Any], spec: Optional[Dict[str, Any]],
             fam: Optional[str],
             strip: Optional[bool] = True
             ) -> Dict[str, Any]:
    """Both extractors on one trace.

    ``strip=True`` (the default, used for every measurement this validation
    performs) feeds the extractors ``strip_gt_values(spec)``: the spec with
    every GT-VALUE field removed but the task-side fields (``label``,
    ``unit``) preserved.  This is simultaneously

      * **faithful** — for the frozen extractor, identical output on all
        16,773 rows (it never reads a GT-value field, only task-side
        ``label``/``unit``), and
      * **contained** — for v6, its internal ``assert_gt_free_spec`` guard
        never fires on it.

    So the measurement feed reproduces what each extractor produces in
    production while satisfying the task's ground-truth containment: neither
    extractor is handed the expected answer.

    ``strip=False`` feeds the spec unchanged.  This is used ONLY by the
    leak probe's control arm (and, for audit, to show what the frozen
    pipeline originally saw).  Feeding a raw spec into a production
    extraction is not a supported path.

    The result carries ``spec_gt_value_free`` so a caller can assert that no
    GT-*value* field survived, instead of assuming it.
    """
    fam = fam or (trace.get('metadata') or {}).get('task_family')
    if strip:
        clean = strip_gt_values(spec)
    else:
        clean = spec or {}
    residual = _residual_gt_value_fields(clean)
    fz = FROZEN.extract_features_v4(trace, clean)
    v6 = V6F.extract_features_v6(trace, clean, fam)
    return {'family': fam,
            'frozen': _frozen_jsonable(fz),
            'v6': _v6_jsonable(v6),
            'frozen_obj': fz, 'v6_obj': v6,
            'stripped_at_entry': bool(strip),
            'spec_gt_value_free': not residual,
            'spec_residual_gt_value_fields': residual,
            # audit only: does ANY v6-declared GT key remain reachable?  The
            # frozen extractor legitimately reads task-side `unit`/`label`,
            # which are not in v6 GT_KEYS, so this is expected to be small.
            'spec_residual_gt_keys_v6': sorted(
                V6CORE.GT_KEYS.intersection(_all_keys(clean)))}


def _all_keys(node: Any, depth: int = 0) -> List[str]:
    """Every dict key anywhere in a nested structure (cycle- and
    depth-guarded so a malformed spec cannot hang the caller)."""
    if depth > 32:
        return []
    out: List[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            out.append(k)
            out.extend(_all_keys(v, depth + 1))
    elif isinstance(node, list):
        for v in node:
            out.extend(_all_keys(v, depth + 1))
    return out


# ── extraction-state summary used for sampling strata ────────────────────────
def state_summary(st: Dict[str, Any]) -> Dict[str, Any]:
    """Per-dimension observation-state profile, for both extractors.

    States counted exactly as the extractors emit them:
    observed / absent / ambiguous / unavailable / not_applicable.
    """
    out: Dict[str, Any] = {}

    def tally(mp: Dict[str, Dict[str, Any]]) -> Dict[str, int]:
        c: Dict[str, int] = {}
        for v in (mp or {}).values():
            s = (v or {}).get('state') or 'missing'
            c[s] = c.get(s, 0) + 1
        return c

    for who in ('frozen', 'v6'):
        blk = st.get(who) or {}
        out[who] = {
            'assertions': tally(blk.get('amap')),
            'decisions': tally(blk.get('dmap')),
            'n_action_keys': len(blk.get('action_counters') or {}),
            'n_actions': blk.get('n_actions'),
        }
        if who == 'frozen':
            out[who]['answer'] = (blk.get('final_answer') or {}).get('state')
            out[who]['coverage'] = blk.get('coverage')
    return out


# ── ground-truth containment: two distinct transforms ─────────────────────────
#
# strip_gt_values      — THE MEASUREMENT FEED.  Remove GT-VALUE fields, keep
#                        task-side label/unit.  Faithful + contained (verified
#                        16,773/16,773 frozen-invariant, 0 v6 guard fires).
# strip_gt_hardened    — AUDIT PRIMITIVE.  Remove every v6-declared GT key at
#                        every depth.  Used to ask "is any GT key reachable?",
#                        NOT as the extraction feed (it over-removes and
#                        breaks frozen financial coverage).

def strip_gt_values(spec: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Remove every GT-VALUE field at every depth, preserving task-side fields.

    This is the containment boundary the validation's measurements actually use.
    It removes the fields that disclose the expected answer (``GT_VALUE_FIELDS``
    plus a bare scalar ``answer``) and keeps the task-side fields an extractor
    legitimately reads (``label``, ``unit``, issue/claim ids and the
    ``required_facts`` / ``required_issues`` structure).

    Verified properties (see ``VALIDATION_STATUS.md`` and the all-families
    check):

      * frozen V4.1 extractor output is unchanged on 16,773 / 16,773 rows —
        the frozen extractor reads task-side ``label``/``unit`` and never a
        GT-value field, so this strip both contains ground truth AND exactly
        reproduces what the frozen pipeline produced;
      * v6's ``assert_gt_free_spec`` guard fires on 0 / 16,773 rows.

    A scalar ``answer`` (a raw graded value, not a fact-entry dict) is itself a
    GT value and is dropped; the fact entry named ``answer`` survives as a
    {label, unit, ...} shell with its GT-value subfields removed.
    """
    import copy

    def scrub(node: Any) -> Any:
        if isinstance(node, dict):
            out: Dict[str, Any] = {}
            for k, v in node.items():
                if k in GT_VALUE_FIELDS:
                    continue
                if k == GT_ANSWER_ENTRY and not isinstance(v, dict):
                    # a raw graded answer value, not a fact entry
                    continue
                out[k] = scrub(v)
            return out
        if isinstance(node, list):
            return [scrub(v) for v in node]
        return node

    return scrub(copy.deepcopy(spec or {}))


def _residual_gt_value_fields(node: Any, depth: int = 0
                              ) -> List[str]:
    """Paths at which a GT-VALUE field still survives (audit / attestation).

    Empty when the spec was fed through ``strip_gt_values``.  Cycle- and
    depth-guarded so a malformed spec cannot hang the caller.
    """
    if depth > 32:
        return []
    found: List[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k in GT_VALUE_FIELDS:
                found.append(k)
            elif k == GT_ANSWER_ENTRY and not isinstance(v, dict):
                found.append(k)
            found.extend(_residual_gt_value_fields(v, depth + 1))
    elif isinstance(node, list):
        for v in node:
            found.extend(_residual_gt_value_fields(v, depth + 1))
    return found


# ── ground-truth containment transforms ──────────────────────────────────────
# Two transforms, kept distinct on purpose (see the module docstring):
#   strip_gt_values    — THE MEASUREMENT FEED: remove GT-value fields, keep
#                        task-side label/unit.  Used by run_both by default.
#   strip_gt_hardened  — AUDIT PRIMITIVE: remove every v6-declared GT key at
#                        every depth.  Never the feed (it over-removes and
#                        breaks frozen financial coverage).
# The leak probe's canaried arm is _canarify_gt_values, defined below.


def strip_gt_hardened(spec: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """AUDIT PRIMITIVE — remove EVERY v6-declared GT key at EVERY depth.

    Answered question: "is any GT-*key* still reachable anywhere in this spec?"
    This is a reachability audit, NOT the extraction feed.  It is deliberately
    aggressive: it removes every member of v6's ``GT_KEYS`` set at arbitrary
    nesting.

    Why it is not the feed
    -----------------------
    The aggressive key-removal form deletes the ``required_facts['answer']``
    entry *as a whole*, including its task-side ``unit``.  The frozen extractor
    legitimately reads ``required_facts['answer']['unit']``, so key-removal
    collapses its financial answer coverage (verified: 1,719 → 426 observed
    final answers) and diverges from the frozen ledger on ~57 % of financial
    rows.  The correct measurement feed is :func:`strip_gt_values`, which keeps
    the task-side ``unit``/``label``.

    This function is retained for two purposes:
      * the residual-key audit in :func:`run_both` (``spec_residual_gt_keys_v6``);
      * :func:`v6_containment_check` (does v6's own guard fire?).

    A test pins its behaviour so that if v6's ``strip_gt`` is ever fixed, the
    validation report is regenerated rather than going stale.
    """
    import copy
    gt_keys = set(V6CORE.GT_KEYS)

    def scrub(node: Any) -> Any:
        if isinstance(node, dict):
            # never let a required-facts entry named 'answer' through: the
            # graded financial answer is a fact entry, not a fact to find
            return {k: scrub(v) for k, v in node.items() if k not in gt_keys}
        if isinstance(node, list):
            return [scrub(v) for v in node]
        return node

    return scrub(copy.deepcopy(spec or {}))


_CANARY = 'CANARY_GT'        # string canary (verdicts, patterns, source text)
_CANARY_NUM = 9876543210.321 # numeric canary (answer value, tolerance)


def _perturb_gt(spec: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Backward-compatible name for the GT-value canary arm of the probe."""
    return _canarify_gt_values(spec)


def leak_probe(trace: Dict[str, Any], spec: Optional[Dict[str, Any]],
               fam: Optional[str]) -> Dict[str, Any]:
    """Differential GT-*value* containment test — a canary probe.

    Question: does any extracted output depend on a GT-*value* field (the
    answer, its tolerance, its regex pattern, its source text...)?  If so,
    the divergence measurement is circular — the extractor is reading the
    answer it is supposed to be measuring against.

    The probe replaces every GT-value field with a distinctive CANARY while
    keeping every task-side field (``label``, ``unit``) intact, then compares
    the extraction on the canaried spec against the extraction on the
    value-stripped spec:

      * value-stripped arm (``strip_gt_values``): GT values absent, task-side
        kept.  This is the reference.
      * canaried arm (``_canarify_gt_values``): GT values replaced with
        canaries, task-side kept.

    An extractor that is GT-value-*invariant* produces identical output on
    both arms → no leak.  An extractor that leaks a GT value into its output
    will emit the CANARY where a contained extractor emits nothing — which is
    unambiguous evidence, reported verbatim.  Because task-side fields are
    identical on both arms, a legitimate unit/label dependence cannot
    produce a false positive.

    ``leaked=False`` is the only acceptable result.
    """
    fam = fam or (trace.get('metadata') or {}).get('task_family')
    clean = strip_gt_values(spec)
    canaried = _canarify_gt_values(spec)
    n_canary = _n_gt_value_fields(spec)
    c_diffs: List[str] = []

    def walk(a, b, path=''):
        if type(a) is not type(b):
            c_diffs.append(f'{path}: type {type(a).__name__}!={type(b).__name__}')
            return
        if isinstance(a, dict):
            for k in sorted(set(a) | set(b)):
                if k in ('spec_gt_value_free', 'spec_residual_gt_value_fields',
                         'spec_residual_gt_keys_v6', 'stripped_at_entry'):
                    continue
                if k not in a or k not in b:
                    c_diffs.append(f'{path}.{k}: key present on one side only')
                    continue
                walk(a[k], b[k], f'{path}.{k}')
        elif isinstance(a, list):
            if len(a) != len(b):
                c_diffs.append(f'{path}: length {len(a)}!={len(b)}')
                return
            for i, (x, y) in enumerate(zip(a, b)):
                walk(x, y, f'{path}[{i}]')
        elif a != b:
            c_diffs.append(f'{path}: {a!r} != {b!r}')

    # Run the frozen extractor on both arms; the frozen extractor is the one
    # with no internal GT guard, so it is the leakage risk being tested.
    fz_clean = _frozen_jsonable(FROZEN.extract_features_v4(trace, clean))
    fz_can = _frozen_jsonable(FROZEN.extract_features_v4(trace, canaried))
    walk(fz_can, fz_clean, 'frozen')

    # v6 self-strips, but test the same way for completeness (guarded).
    v6_can = None
    try:
        v6_clean = _v6_jsonable(V6F.extract_features_v6(trace, clean, fam))
        v6_can = _v6_jsonable(V6F.extract_features_v6(trace, canaried, fam))
        walk(v6_can, v6_clean, 'v6')
    except V6CORE.GroundTruthLeak as exc:
        # v6 refused even the canaried spec: containment holding by refusal.
        c_diffs.append(f'v6.guard: {str(exc)[:160]}')

    # The most damning evidence: does a CANARY string literally appear in an
    # extracted value?  That is a GT value having leaked into the output.
    leaked_evidence = []
    for who, blk in (('frozen', fz_can), ('v6', v6_can or {})):
        for _k, fv in (blk.get('amap') or {}).items():
            if fv.get('value') in (_CANARY, _CANARY_NUM):
                leaked_evidence.append(f'{who}.amap.{_k} echoes canary')
    # also check the answer field (frozen uses final_answer, v6 uses answer)
    for who, blk in (('frozen', fz_can), ('v6', v6_can)):
        if not blk:
            continue
        ans = blk.get('final_answer') or blk.get('answer')
        if isinstance(ans, dict) and ans.get('value') in (_CANARY, _CANARY_NUM):
            leaked_evidence.append(f'{who}.answer echoes canary')

    leaked = bool(c_diffs) or bool(leaked_evidence)
    return {
        'leaked': leaked,
        'n_diff_paths': len(c_diffs),
        'diff_sample': c_diffs[:12],
        'canary_in_output': leaked_evidence,
        'n_gt_value_fields_canaried': n_canary,
        'vacuous': n_canary == 0,
        'method': ('canary differential: replace every GT-value field with a '
                   'canary (task-side kept), extract, and compare against the '
                   'value-stripped extraction; any difference or canary echo '
                   'means a GT-value field influenced extraction'),
        'substantive': bool(not leaked and n_canary > 0),
    }


def _canarify_gt_values(spec: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Replace every GT-value field with a canary, preserving structure and
    all task-side fields.  Used by :func:`leak_probe` as the leak-test arm."""
    import copy
    out = copy.deepcopy(spec or {})

    def scrub(node: Any) -> None:
        if isinstance(node, dict):
            for key in list(node.keys()):
                if key in GT_VALUE_FIELDS:
                    node[key] = _CANARY if not isinstance(node[key], (int, float, list, dict)) \
                        else _CANARY_NUM
                elif key == GT_ANSWER_ENTRY and not isinstance(node[key], dict):
                    node[key] = _CANARY_NUM
                else:
                    scrub(node[key])
        elif isinstance(node, list):
            for v in node:
                if isinstance(v, (dict, list)):
                    scrub(v)

    scrub(out)
    return out


def _n_gt_value_fields(spec: Optional[Dict[str, Any]]) -> int:
    """How many GT-value fields exist anywhere in a spec.  A count of 0 means
    the leak probe is vacuous (nothing to leak)."""
    count = 0

    def rec(node: Any, depth: int = 0) -> None:
        nonlocal count
        if depth > 32:
            return
        if isinstance(node, dict):
            for k, v in node.items():
                if k in GT_VALUE_FIELDS:
                    count += 1
                elif k == GT_ANSWER_ENTRY and not isinstance(v, dict):
                    count += 1
                else:
                    rec(v, depth + 1)
        elif isinstance(node, list):
            for v in node:
                rec(v, depth + 1)

    rec(spec or {})
    return count


def v6_containment_check(spec: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Does v6's own guard fire when a GT-bearing spec is handed to it?"""
    out = {'raises_on_full_spec': False, 'raises_on_stripped': False,
           'error': None}
    try:
        V6CORE.assert_gt_free_spec(spec, 'probe')
    except V6CORE.GroundTruthLeak as exc:
        out['raises_on_full_spec'] = True
        out['error'] = str(exc)[:200]
    stripped = V6CORE.strip_gt(spec)
    try:
        V6CORE.assert_gt_free_spec(stripped, 'probe')
        out['raises_on_stripped'] = False
    except V6CORE.GroundTruthLeak as exc:                       # pragma: no cover
        out['raises_on_stripped'] = True
        out['error'] = str(exc)[:200]
    out['stripped_keys_absent'] = sorted(
        V6CORE.GT_KEYS.intersection(stripped.keys()))
    return out
