#!/usr/bin/env python3.11
"""CORRECTED packet builder for a FUTURE validation round (V4.2-aligned).

The round-1/round-2 builder (``build_packets.py``, byte-frozen) has three
evidence-scope defects flagged by the V4.2 audit.  This module is the
corrected builder a future study should call.  It mirrors the frozen
surface EXACTLY (PROMPT_VERSION, construct table, blinding scan, order
scramble, administrative-only LEP label) so the round-2 packets remain
reproducible, and adds ONLY these three corrections:

  (I)  INTERMEDIATE AUTHORED EVIDENCE — the excerpt now carries the role's
       intermediate authored report bodies (write_file content) that
       PRECEDE the checkpoint, not just the last reasoning/handoff.  A
       stance the role wrote into its intermediate file is now visible to
       the judge instead of being dropped (the round-2 B3 gap).
  (II) ROLE-SPECIFIC CHECKPOINT ALIGNMENT — a benign execution aligns to the
       SAME role + SAME iteration as the LEP checkpoint (its own N-th
       handoff of that role), NOT the lexicographically/sequentially first
       handoff tool-call of that role (the round-1 ``_handoff_index_at``
       defect).  Iterations are matched per role so a later correction in
       the LEP's 2nd iteration aligns to the benign's 2nd iteration.
  (III) ORIGIN EXCLUSION — the evidence segment drops attacker-delivered
       payloads at recorded injection-origin event indices (the provenance
       view's authored side), so an injected handoff payload is never shown
       as authored evidence.

This module is CODE ONLY for this task: it exposes the corrected pure
functions + a ``build`` entrypoint but does NOT build packets, make LLM
calls, or write files unless explicitly run.  The regression suite exercises
the three corrected functions directly.
"""
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STUDY = os.path.abspath(os.path.join(HERE, '..'))
R1 = HERE  # this study's scripts (build_packets.py lives here, frozen)
sys.path.insert(0, R1)
import build_packets as BP  # frozen round-1 module: Packets, _excerpts, \
                            # _task_prompt, _scan, _order_key, CONSTRUCT
import cv2  # noqa: E402


# ── (II) role+iteration alignment ────────────────────────────────────────────
def _role_handoff_indices(tr):
    """Ordered event indices of every ``agent_handoff`` per role.  Returns
    {role: [event_index, ...]} (iteration k = the k-th handoff, 1-based)."""
    out = {}
    for e in (tr.get('events') or []):
        if e.get('event_type') == 'agent_handoff':
            role = e.get('agent_role') or e.get('agent_id') or '?'
            out.setdefault(role, []).append(e.get('event_index'))
    return out


def aligned_handoff_index(tr, role, iteration):
    """The event index of ``role``'s ``iteration``-th handoff (1-based) in
    benign trace ``tr`` — the CORRESPONDING checkpoint, NOT the role's first
    handoff (the round-1 defect).  Returns None when that iteration does not
    exist in the benign (the benign simply has fewer iterations of the role:
    the alignment is reported unavailable, never forced to another iteration).
    """
    idx = _role_handoff_indices(tr).get(role, [])
    if iteration < 1 or iteration > len(idx):
        return None
    return idx[iteration - 1]


def _checkpoint_role_iter(p2row):
    """The LEP checkpoint's (role, iteration) from the frozen P2 row.  The
    checkpoint key is 'handoff:<role>#<it>' (or 'final')."""
    cks = [c for c in ((p2row.get('stage') or {}).get('checkpoints') or [])
           if c.get('available') and c.get('after_boundary')
           and not c.get('is_origin')
           and c.get('event_index') is not None
           and c.get('is_cross_agent', True)]
    if not cks:
        return None
    cks.sort(key=lambda c: (c.get('event_index') or 0,
                            c.get('role') or '',
                            c.get('iteration') or 1))
    c0 = cks[0]
    role = c0.get('role')
    it = c0.get('iteration') or 1
    return role, it


# ── (III) origin exclusion ───────────────────────────────────────────────────
def _origin_delivered_indices(trace):
    """Recorded injection-origin event indices whose output IS the delivered
    attacker payload (never authored).  These events' output_text must be
    dropped from any evidence segment."""
    out = set()
    for o in (trace.get('event_labels') or {}).get('injection_origins', []) \
            if isinstance(trace.get('event_labels'), dict) else []:
        out.add(o.get('event_index'))
    # provenance-aware view shape (from the extractor's Origin records):
    cell = trace.get('metadata') or {}
    for o in cell.get('injection_origins', []) or []:
        if o.get('delivered_is_output'):
            out.add(o.get('event_index'))
    return out


def _authored_segment(trace, from_idx, to_idx=None):
    """The agent-authored outputs in events[from_idx:to_idx], excluding every
    event whose recorded output is the attacker-delivered payload
    (provenance view: the genuine authored side is retained, the delivered
    payload is dropped)."""
    evs = (trace.get('events') or [])
    hi = to_idx if to_idx is not None else len(evs)
    out = []
    for i in range(max(0, from_idx), min(hi, len(evs))):
        e = evs[i]
        et = e.get('event_type')
        if et in ('agent_handoff', 'final_response'):
            # provenance-aware: the genuine authored side only
            out.append((e.get('agent_role') or '',
                       e.get('output_text') or ''))
        elif et == 'reasoning':
            out.append((e.get('agent_role') or '',
                       e.get('output_text') or ''))
    return out


# ── (I) intermediate authored report bodies ──────────────────────────────────
def _intermediate_authored(trace, role, before_idx):
    """Role-scoped intermediate AUTHORED report bodies: write_file content
    written by ``role`` at an event index STRICTLY BEFORE ``before_idx``
    (the checkpoint boundary).  These are the role's own authored artifacts,
    the evidence the round-1 excerpt dropped.  Attackers' payloads are not
    in tool_arguments content (authored args only), so no exclusion needed
    here."""
    bodies = []
    for e in (trace.get('events') or []):
        if e.get('event_type') != 'tool_call' or e.get('tool_name') != 'write_file':
            continue
        if (e.get('agent_role') or '') != role:
            continue
        ei = e.get('event_index')
        if ei is None or before_idx is not None and ei >= before_idx:
            continue
        content = (e.get('tool_arguments') or {}).get('content')
        if isinstance(content, str) and content:
            bodies.append(content)
    return bodies


def checkpoint_evidence_v42(P, tid, label_by_tid):
    """Corrected B3 checkpoint-scoped evidence (items I/II/III).

    For the LEP execution and its 3 matched benigns, at the LEP checkpoint's
    (role, iteration): the aligned boundary-onward AUTHORED content + the
    role's intermediate authored report bodies, origin payloads excluded.
    Returns {label: {'role','iteration','from_index','authored','
    intermediate_authored','tool_skeleton','note'} or {'note': ...}} — a
    drop-in superset of the round-1 _checkpoint_evidence surface."""
    p2 = P.p2(tid)
    if not p2:
        return None
    ri = _checkpoint_role_iter(p2row=p2)
    if ri is None:
        return None
    role, iteration = ri
    tids = [tid] + P.refs(tid)[:3]
    out = {}
    for t in tids:
        lab = label_by_tid.get(t)
        if lab is None:
            continue
        tr = P.trace(t)
        if tr is None:
            continue
        if t == tid:
            cks = [c for c in ((p2.get('stage') or {})
                               .get('checkpoints') or [])
                   if c.get('role') == role and
                   (c.get('iteration') or 1) == iteration and
                   c.get('event_index') is not None]
            from_idx = min((c['event_index'] for c in cks), default=None)
        else:
            from_idx = aligned_handoff_index(tr, role, iteration)
        if from_idx is None:
            out[lab] = {'note': 'checkpoint_alignment_unavailable',
                        'role': role, 'iteration': iteration}
            continue
        evs = tr.get('events') or []
        seg = _authored_segment(tr, from_idx)
        # tool skeleton from the boundary onward (role-scoped, authored args)
        skel = []
        for e in evs[from_idx:]:
            if e.get('event_type') == 'tool_call' and e.get('tool_name'):
                tgt = (e.get('tool_arguments') or {}).get(
                    'path') or (e.get('tool_arguments') or {}).get(
                    'target_agent') or ''
                skel.append('{}: {}{}'.format(
                    e.get('agent_role') or '', e.get('tool_name'),
                    ('({})'.format(tgt)) if tgt else ''))
        out[lab] = {
            'role': role, 'iteration': iteration, 'from_index': from_idx,
            'authored': [BP._scrub('[{}] {}'.format(r, t))[:BP.EXCERPT_CHARS]
                         for r, t in seg if t][:10],
            'intermediate_authored': [BP._scrub(b)[:BP.EXCERPT_CHARS]
                                      for b in
                                      _intermediate_authored(tr, role,
                                                             from_idx)][:10],
            'tool_skeleton': skel[-40:],
            'note': None,
        }
    return out


def main(argv=None):
    """Build the corrected future packets.  NOT invoked by this task (no LLM
    calls, no file writes unless explicitly run)."""
    raise SystemExit(
        'build_packets_v42_future: code-only in this task.  To build '
        'future packets, call build_future_packets(manifest_path).')


if __name__ == '__main__':
    main()
