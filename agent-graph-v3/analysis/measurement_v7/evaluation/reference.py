"""
Reference-label loading and gating for the v7 evaluation.

The evaluator compares the frozen, v6, and v7 extractors (and the four
dimensions + union they feed) against a REFERENCE.  A reference is one of, in
preference order:

  1. a genuine HUMAN-adjudicated label set  -> a performance claim may be
     made (this does NOT yet exist for this corpus);
  2. an AI-adjudicated label set            -> an "AI-adjudicated reference
     performance" claim, explicitly NOT human-validated;
  3. no reference                            -> the evaluation is GATED.

The gate is mechanical: a missing or empty reference raises
``ReferenceMissing``.  The evaluator NEVER fabricates a reference from a v6
or a judge output, and NEVER treats an AI reference as human ground truth.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

REF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'out')


class ReferenceMissing(RuntimeError):
    """No reference labels are available for the requested scope.  The
    evaluation that needs them is gated, not run against invented labels."""


def _kind(path: str) -> str:
    base = os.path.basename(path).lower()
    if 'human' in base:
        return 'human'
    if 'adjudicated_ai' in base or 'ai' in base:
        return 'ai_adjudicated'
    return 'unknown'


def load_reference(path: str, scope: str) -> Dict[str, Any]:
    """Load a reference label set and verify it is non-empty for the scope.

    ``scope`` is one of ``dev`` / ``heldout`` / ``full``.  A reference that
    has no label rows for the requested scope raises :class:`ReferenceMissing`
    — the caller must not proceed with a partial or invented reference.
    """
    if not path or not os.path.isfile(path):
        raise ReferenceMissing(
            f'no reference file at {path!r}; the {scope} evaluation is gated '
            f'until real labels are supplied.  Not fabricating a reference.')
    rows = [json.loads(l) for l in open(path) if l.strip()]
    if not rows:
        raise ReferenceMissing(
            f'reference file {path} is empty; the {scope} evaluation is '
            f'gated.  A file with no usable labels is refused, not padded.')
    sel = [r for r in rows if (r.get('partition') in (scope, 'full')
                               or scope == 'full')]
    if not sel:
        raise ReferenceMissing(
            f'reference file {path} has no rows in the {scope!r} partition; '
            f'the evaluation is gated.')
    return {'path': path, 'kind': _kind(path), 'n_rows': len(rows),
            'rows': sel, 'n_in_scope': len(sel), 'scope': scope}


def is_human(ref: Dict[str, Any]) -> bool:
    return ref['kind'] == 'human'


def provenance_label(ref: Dict[str, Any]) -> str:
    """The EXACT label a report must attach to a number produced against
    this reference.  This is the requirement-17 claim classification:
    performance against an AI reference is NOT human-validated accuracy."""
    if ref['kind'] == 'human':
        return 'human-audited reference'
    if ref['kind'] == 'ai_adjudicated':
        return ('AI-adjudicated reference (NOT human-validated; performance '
                'against this reference measures extractor agreement with a '
                'judge, not extractor truth)')
    return 'unclassified reference'
