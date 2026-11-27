"""
candspec repair-target normalizers (RT-1 … RT-3) over the v7 feature bundle.

Each is an explicit, task-side, GT-free transformation of the *representation*
of a feature value, so two executions (or a judge and an extractor) that
encoded the same underlying fact under different surface forms now compare as
equal.  None of these moves an agent's numbers; they only canonicalize what
was observed, and every rekeying records its provenance on the basis.

  RT-1  SWE issue-identifier canonicalization -> positional `issue_<n>`.
        Makes SWE issue-identification judgeable (the v7 AI reference was
        ~99% unjudgeable on code_review because judges coded issue_1 vs
        ISSUE_001 vs semantic names inconsistently).  The alias table is
        built ONLY from the task-side required_issues list (ids/labels); it
        never reads reference answers.  Unresolvable identifiers are kept as
        `unresolved_identifier` (unavailable), never guessed.
  RT-2  FinQA numeric / unit / percentage / entity / operand normalization.
        Values are compared in the spec-declared unit; declared scale-word
        equivalences are honored; an off-by-10^k that the spec does NOT
        declare is a genuine difference.  Spec-side unit defects are carried
        in a machine-readable DEFECT MANIFEST that corrects the grader INPUT
        (the GT value to the spec's stated intent), recorded per fixture —
        never by rescaling the agent's numbers.
  RT-3  SciFact verdict canonicalization: every verdict surface form is run
        through `canonical_verdict` (SUPPORTED/CONTRADICTED/NOT_ENOUGH)
        before comparison.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import sys, os
# RT-3 reuses the validated canonical-verdict map (graders.py is the sole GT
# consumer; canonical_verdict itself reads no GT, it only maps surface forms).
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, '..', '..')))
try:
    from graders import canonical_verdict          # v6 graders on path
except Exception:                                   # pragma: no cover
    import importlib.util as _il
    _p = os.path.join(os.path.dirname(_HERE), '..', '..',
                      'analysis', 'measurement_v6', 'graders.py')
    _s = _il.spec_from_file_location('bf_graders', _p)
    _m = _il.module_from_spec(_s); _s.loader.exec_module(_m)
    canonical_verdict = _m.canonical_verdict


# ── RT-1: SWE issue-identifier canonicalization ─────────────────────────────
def swe_issue_alias_table(required_issues: Any) -> Dict[str, Dict[str, Any]]:
    """Positional alias table built ONLY from the task-side issue list.

    Returns ``{'issue_<n>': {'semantic_id': ..., 'function': ...,
    'location': ..., 'keywords': [...]}, ...}`` (n = 1-based position).  The
    table is the single source of truth for judging SWE issue-identification:
    any agent surface identifier is canonicalized to ``issue_<n>`` by (a) exact
    match on the semantic id, (b) a declared positional alias (`ISSUE_001`,
    `issue_1`, `#1`), or (c) keyword/function/location containment.
    """
    if isinstance(required_issues, dict):
        ents = [dict(v or {}, id=k) for k, v in required_issues.items()]
    elif isinstance(required_issues, list):
        ents = [dict(x) for x in required_issues]
    else:
        ents = []
    table: Dict[str, Dict[str, Any]] = {}
    for i, ent in enumerate(ents, start=1):
        key = f'issue_{i}'
        table[key] = {
            'semantic_id': str(ent.get('id') or ent.get('key') or ''),
            'function': ent.get('function') or '',
            'location': ent.get('location') or '',
            'keywords': list(ent.get('keywords') or []),
        }
    return table


_POS_ALIAS = re.compile(r'\bissue[_\s-]?0*(\d+)\b|issue\b\s*#?\s*(\d+)', re.I)


def canonical_swe_issue(surface: str, table: Dict[str, Dict[str, Any]]
                        ) -> Optional[str]:
    """Map an agent-authored issue identifier to its positional key.

    Resolution order (all task-side, no GT):
      1. explicit positional form `issue_3` / `ISSUE_003` / `issue #3` / `#3`
      2. exact semantic-id match
      3. function / location token containment
      4. keyword containment (>=1 declared keyword present)
    Returns None when unresolvable (recorded as unresolved, never guessed).
    """
    s = (surface or '').strip()
    if not s:
        return None
    if not table:
        return None
    # 1. positional
    m = _POS_ALIAS.search(s)
    if m:
        num = m.group(1) or m.group(2)
        if num:
            k = int(num)
            if f'issue_{k}' in table:
                return f'issue_{k}'
    low = s.lower()
    # 2. exact semantic id
    for key, ent in table.items():
        if ent['semantic_id'] and ent['semantic_id'].lower() == low:
            return key
    # 3. function / location containment
    for key, ent in table.items():
        if ent['function'] and ent['function'].lower() in low:
            return key
        if ent['location'] and ent['location'].lower() in low:
            return key
    # 4. keyword containment
    for key, ent in table.items():
        for kw in ent['keywords']:
            if kw and kw.lower() in low:
                return key
    return None


def rekey_stances(features: Dict[str, Any],
                  required_issues: Any) -> Tuple[Dict[str, Any], List[str]]:
    """Rekey a code_review stance feature dict to positional issue_<n> keys.

    ``features`` is keyed by semantic id; the output is keyed by positional
    form so judges and the reference share one identifier.  The semantic id is
    preserved on the feature basis for audit.  A feature whose semantic key is
    not in the table is carried through unchanged with an `unresolved` marker.
    """
    table = swe_issue_alias_table(required_issues)
    # invert: semantic id -> positional key
    sem2pos = {ent['semantic_id']: key for key, ent in table.items()
               if ent['semantic_id']}
    out: Dict[str, Any] = {}
    notes: List[str] = []
    for key, feat in (features or {}).items():
        pos = sem2pos.get(key) or sem2pos.get(str(key))
        if pos is None:
            pos = f'unresolved:{key}'
            notes.append(f'RT-1: {key} unresolved (kept, marked)')
        else:
            notes.append(f'RT-1: {key} -> {pos}')
        existing = out.get(pos)
        if existing is None:
            out[pos] = feat
        elif (str(getattr(existing, 'state', 'absent')) != 'observed'
              and str(getattr(feat, 'state', 'absent')) == 'observed'):
            # a more informative feature wins the same positional slot
            out[pos] = feat
    return out, notes


# ── RT-2: FinQA numeric / unit normalization ─────────────────────────────────
_SCALE_WORD = {'thousand': 1e3, 'k': 1e3, 'million': 1e6, 'm': 1e6,
               'billion': 1e9, 'b': 1e9, 'trillion': 1e12, 'bn': 1e9}


def declared_unit_factor(unit: str) -> float:
    """Numeric factor a spec-declared unit string implies (0.0 when the unit
    carries no numeric scale, e.g. a plain dimensionless ratio)."""
    if not unit:
        return 0.0
    u = unit.strip().lower()
    for word, fac in _SCALE_WORD.items():
        if re.search(rf'\b{re.escape(word)}s?\b', u):
            return fac
    return 0.0


def numeric_equivalent(a: Optional[float], b: Optional[float],
                       spec_factor: float = 0.0,
                       tol: float = 0.0) -> bool:
    """True when two extracted values are equivalent under the spec-declared
    unit scale and tolerance.  An off-by-10^k NOT declared by the spec is a
    genuine difference (returns False), which is the honest behavior."""
    if a is None or b is None:
        return a is None and b is None
    if abs(a - b) <= (tol or 0.0) + 1e-9:
        return True
    if spec_factor:
        if abs(a - b * spec_factor) <= (tol or 0.0) + 1e-6:
            return True
        if abs(b - a * spec_factor) <= (tol or 0.0) + 1e-6:
            return True
    return False


#: machine-readable spec-side defect manifest.  Each entry corrects the
#: GRADER INPUT (the GT value to the spec's stated intent) for a fixture whose
#: declared unit is defective.  This is a spec correction, recorded, never a
#: rescaling of the agent's answer.  Source of record: the fin_v5 revised
#: spec overlay (35 verified unit defects + 1 value bug; frozen specs
#: untouched).  The loader normalizes that schema to
#: {'unit_defects': {fixture: unit}, 'value_bugs': {fixture: note}}.
def load_defect_manifest(path: Optional[str] = None) -> Dict[str, Any]:
    default = os.path.abspath(os.path.join(_HERE, '..', '..', '..',
                                           'analysis',
                                           'fin_grader_correction_v5',
                                           'fin_spec_revised',
                                           'corrections.json'))
    p = path or default
    if not os.path.exists(p):
        return {'unit_defects': {}, 'value_bugs': {}, 'source': 'none'}
    import json
    raw = json.load(open(p))
    return {
        'unit_defects': dict(raw.get('confirmed_unit_defects') or {}),
        'value_bugs': dict(raw.get('value_bugs') or {}),
        'value_corrections': dict(raw.get('value_corrections') or {}),
        'review_queue': list(raw.get('new_findings_review_queue') or []),
        'ungradeable_by_design': list(raw.get('no_answer_key_excluded') or []),
        'source': p,
        'frozen_specs_untouched': bool(raw.get('frozen_specs_untouched')),
    }


# ── RT-3: SciFact verdict canonicalization ───────────────────────────────────
def canonical_research_features(features: Dict[str, Any]
                                ) -> Tuple[Dict[str, Any], List[str]]:
    """Run every observed research verdict through canonical_verdict so
    SUPPORT/CONTRADICT/NEI surface forms compare equal.  Unobserved states
    (absent/ambiguous/unavailable) are carried through untouched."""
    out: Dict[str, Any] = {}
    notes: List[str] = []
    for key, feat in (features or {}).items():
        st = getattr(feat, 'state', None)
        if st == 'observed':
            canon = canonical_verdict(getattr(feat, 'value', None))
            if canon is None:
                out[key] = feat
                notes.append(f'RT-3: {key} observed value {feat.value!r} unmappable')
            elif feat.value != canon:
                # ALWAYS normalize to the canonical enum form: the comparison
                # kernel is categorical, so 'supported' vs 'SUPPORTED' would
                # otherwise read as a difference (RT-3 defect found by the
                # S4 synthetic invariance test).
                out[key] = _with_value(feat, canon, f'RT-3:{feat.value}->{canon}')
                notes.append(f'RT-3: {key} {feat.value} -> {canon}')
            else:
                out[key] = feat
        else:
            out[key] = feat
    return out, notes


def _with_value(feat, value, basis_suffix: str):
    from extract_core import Feature
    return Feature(feat.state, value, (feat.basis or '') + '+' + basis_suffix,
                   feat.span)


def apply_all(features_by_dim: Dict[str, Any], fam: str,
              required_issues: Any, required_facts: Any
              ) -> Tuple[Dict[str, Any], List[str]]:
    """Apply the family's representation normalizations to a feature bundle."""
    notes: List[str] = []
    out = {k: v for k, v in features_by_dim.items()}
    if fam == 'code_review':
        for dim in ('assertions', 'decisions'):
            out[dim], n1 = rekey_stances(out.get(dim) or {}, required_issues)
            notes += n1
    if fam == 'research_synthesis':
        for dim in ('assertions', 'decisions'):
            out[dim], n3 = canonical_research_features(out.get(dim) or {})
            notes += n3
    # RT-2 is a grader-input concern; extraction is untouched but we record
    # the declared factors so the distance/grade layers can use them.
    if fam == 'financial_analysis':
        facts = required_facts if isinstance(required_facts, dict) else {}
        out['__unit_factors'] = {k: declared_unit_factor((v or {}).get('unit'))
                                 for k, v in facts.items()
                                 if isinstance(v, dict)}
    return out, notes
