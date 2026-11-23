"""
v7 ACTION DIAGNOSTICS — separate measurements, never a composite.

The historical total-variation kernel (re-exported from v6 ``distances``)
remains the primary ACTIONS distance.  The known limitations documented in
the audit are that it is:

  * scale-invariant:  {x:1} vs {x:20} -> 0.0  (a retry storm is invisible)
  * order-blind:      the call SEQUENCE is discarded
  * failure-blind:    ``tool_error`` is never populated, so a failed call is
                      counted identically to a successful one

This module implements the separate DIAGNOSTIC measurements the brief asks
for.  Each is an independent quantity; none is combined into a weighted
score and none replaces the TV kernel.  They are reported individually so a
reader can see which diagnostic, if any, explains a divergence that the TV
kernel is blind to.

NO GROUND TRUTH is read.  NO tool error is fabricated: where the record does
not populate ``tool_error`` the failure diagnostic reports ``not_recorded``
explicitly rather than inferring a failure.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

# NOTE: the target-key reconstruction below mirrors
# features_v6.action_counter EXACTLY (path/report_path/target_agent/pattern,
# then |sorted(source_paths)|).  It is a single source because the key
# definition is the only contract the diagnostics share with the TV kernel;
# a change to the key there must be mirrored here (pinned by test).


def action_sequence(trace: Dict[str, Any]) -> List[Tuple[str, str, str]]:
    """Chronological (role, tool, target) sequence of tool calls."""
    seq: List[Tuple[str, str, str]] = []
    for e in trace.get('events') or []:
        if e.get('event_type') != 'tool_call':
            continue
        role = e.get('agent_role') or ''
        name = e.get('tool_name') or ''
        args = e.get('tool_arguments') or {}
        target = ''
        if isinstance(args, dict):
            for k in ('path', 'report_path', 'target_agent', 'pattern'):
                v = args.get(k)
                if isinstance(v, str) and v:
                    target = v
                    break
            if not target:
                sp = args.get('source_paths')
                if isinstance(sp, list) and sp:
                    target = '|'.join(sorted(str(x) for x in sp))
        seq.append((role, name, target))
    return seq


def action_count(trace: Dict[str, Any]) -> int:
    return len(action_sequence(trace))


# ── DIAGNOSTIC 1: absolute tool-call count difference ───────────────────────
def count_diff(a: Dict[str, Any], b: Dict[str, Any]) -> int:
    """Absolute difference in total tool-call count.  Scale-sensitive in the
    way the TV kernel is not: {x:1} vs {x:20} -> 19 here, 0.0 in TV."""
    return abs(action_count(a) - action_count(b))


# ── DIAGNOSTIC 2: retry multiplicity ────────────────────────────────────────
def retry_multiplicity(trace: Dict[str, Any]) -> int:
    """Max repeat count of any single (role, tool, target) within one trace.
    1 = no retry.  This is the quantity TV cannot see."""
    c = Counter(action_sequence(trace))
    return max(c.values()) if c else 0


def retry_diff(a: Dict[str, Any], b: Dict[str, Any]) -> int:
    return abs(retry_multiplicity(a) - retry_multiplicity(b))


# ── DIAGNOSTIC 3: tool-call ordering distance ───────────────────────────────
def order_distance(a: Dict[str, Any], b: Dict[str, Any]) -> Optional[float]:
    """Sequence (ordering) distance in [0,1] via longest-common-subsequence
    similarity over the (role, tool, target) call sequences.  None when
    neither side has calls (not evaluable, never 0)."""
    sa, sb = action_sequence(a), action_sequence(b)
    if not sa and not sb:
        return None
    if not sa or not sb:
        return 1.0
    n, m = len(sa), len(sb)
    prev = [0] * (m + 1)
    for i in range(1, n + 1):
        cur = [0] * (m + 1)
        for j in range(1, m + 1):
            if sa[i - 1] == sb[j - 1]:
                cur[j] = prev[j - 1] + 1
            else:
                cur[j] = max(prev[j], cur[j - 1])
        prev = cur
    common = prev[m]
    return 1.0 - (2.0 * common / (n + m))


# ── DIAGNOSTIC 4: observable failures, only where recorded ─────────────────
def failure_evidence(trace: Dict[str, Any]) -> Dict[str, Any]:
    """Recorded tool failures.  ``tool_error`` is never populated in this
    corpus, so ``n_recorded_failures`` is 0 and ``recorded`` is False; the
    diagnostic says so explicitly instead of fabricating a failure count.
    A future schema that populates ``tool_error`` is picked up for free."""
    n_calls = 0
    n_err = 0
    for e in trace.get('events') or []:
        if e.get('event_type') != 'tool_call':
            continue
        n_calls += 1
        if e.get('tool_error') not in (None, '', False):
            n_err += 1
    return {'n_calls': n_calls, 'n_recorded_failures': n_err,
            'recorded': n_err > 0,
            'note': ('tool_error not populated in this corpus; a 0 count '
                     'means NOT-RECORDED, not "no failures"')}


def pair(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """All four diagnostics for a trace pair, reported individually."""
    return {
        'count': {'a': action_count(a), 'b': action_count(b),
                  'diff': count_diff(a, b)},
        'retry': {'a': retry_multiplicity(a), 'b': retry_multiplicity(b),
                  'diff': retry_diff(a, b)},
        'order_distance': order_distance(a, b),
        'failure': {'a': failure_evidence(a), 'b': failure_evidence(b)},
    }
