"""
Judge output schemas: structured JSON conforming to the existing annotation
schemas, with evidence-span requirements.

Conformance targets (the existing annotation templates):
  extraction -> responses_extraction_annotator_{A,B}.jsonl
                 {item_token, labels: {field_key: {state, value, evidence}},
                  annotator_confidence, notes}
  pair       -> responses_pairs_annotator_{A,B}.jsonl
                 {pair_token, reference_set_usable,
                  member_judgments: [{member_index, assertions_differs,
                    decisions_differs, actions_differs, outcomes_differs,
                    evidence}], any_difference_member_index,
                  annotator_confidence, notes}

The validators here are the gate between a raw judge completion and the
annotation record: a completion that fails validation is a JUDGE FAILURE
(recorded, retried once, then marked failed) — it is NEVER silently coerced
into a label.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

STATES = ('observed', 'absent', 'ambiguous', 'unavailable')
PAIR_STATES = ('yes', 'no', 'ambiguous', 'unavailable')


class JudgeOutputError(ValueError):
    """A judge completion does not conform to the annotation schema."""


def parse_json_completion(text: str) -> Dict[str, Any]:
    """Parse a strict-JSON completion.

    Lenient on FENCING, strict on CONTENT: a single ```json fence is
    stripped, and a balanced leading object is extracted even when the model
    appended trailing prose (``raw_decode`` stops at the object's end, which
    is the standard way to accept "JSON followed by commentary").  A
    completion with NO complete leading object is still a schema failure —
    we never parse a partial object into a label.
    """
    t = (text or '').strip()
    m = re.match(r'^```(?:json)?\s*(\{.*\})\s*```$', t, re.S)
    if m:
        t = m.group(1)
    # locate the first '{' and raw-decode the balanced object from there
    i = t.find('{')
    if i < 0:
        raise JudgeOutputError('completion has no JSON object')
    try:
        d, _end = json.JSONDecoder().raw_decode(t[i:])
    except json.JSONDecodeError as e:
        raise JudgeOutputError(f'completion is not JSON: {e}')
    if not isinstance(d, dict):
        raise JudgeOutputError('completion JSON must be an object')
    return d


def _check_state(v: Any, field: str, allowed: tuple, out: List[str]) -> None:
    if v not in allowed:
        out.append(f'{field}: state {v!r} not in {allowed}')


def _evidence_present(ev: Any) -> bool:
    """Evidence may be a non-empty string, or a per-item dict of spans."""
    if isinstance(ev, str) and ev.strip():
        return True
    if isinstance(ev, dict) and any(
            (isinstance(v, str) and v.strip())
            or (isinstance(v, dict) and any(
                (isinstance(x, str) and x.strip())
                for x in v.values()))
            for v in ev.values()):
        return True
    return False


#: verdict / stance words that a judge may put in the 'state' slot while
#: keeping the real state implicit (an observed verdict).  Recognising these
#: is information-preserving: value == state == the verdict, so the record
#: still says "the agent expressed X".  It is NOT a new verdict.
_VERDICT_WORDS = {
    'supported', 'contradicted', 'not enough info', 'not_enough',
    'asserted', 'repair_proposed', 'repair_performed', 'denied',
}


def _canon_state(ent: Dict[str, Any]) -> str:
    """The record's real state.  A judge that put the verdict in 'state'
    (with value == state) is still stating an OBSERVED verdict."""
    st = ent.get('state')
    if st in STATES:
        return st
    sval = ent.get('value')
    if str(st).lower() in _VERDICT_WORDS and \
            str(st).lower() == str(sval).lower():
        return 'observed'
    return str(st or 'unavailable').lower()


def _validate_state_record(ent: Dict[str, Any], where: str,
                           out: List[str],
                           evidence_required: bool,
                           evidence: Any = None,
                           top_level_evidence: Any = None) -> None:
    st = _canon_state(ent)
    _check_state(st, where, STATES, out)
    if st in ('observed', 'ambiguous') and evidence_required:
        # evidence may sit at the record, be gathered from value/siblings,
        # or be bundled at the top level of the label block.  Any one of
        # these satisfies the requirement; NONE of them does not.
        ev_found = evidence \
            or _evidence_present(ent.get('evidence')) \
            or bool(top_level_evidence)
        if not ev_found:
            out.append(f'{where}: state={st} requires an evidence span')


#: a field's per-item keys are recognisable by these patterns (the judge
#: may answer a field either under the field key itself or by naming each
#: item directly, e.g. claim_1_verdict / claim_2_verdict for the
#: claim_verdicts field; issue ids for issues_identified).
_PER_ITEM_HINTS = {
    'claim_verdicts': ('verdict', 'claim'),
    'issues_identified': ('issue',),
    'final_answer': ('final_answer',),
    'final_answer_span': ('final_answer_span', 'span'),
}


def _find_field_labels(labels: Dict[str, Any], key: str):
    """Return (mode, entries): 'field' = one {state,value,evidence} record
    under the field key; 'per_item' = a set of records each answering one
    item of the field; None = the field is genuinely absent.

    Normalisation (all information-preserving, none invents content):
      * a literal 'field_key' record (the model echoing the prompt
        placeholder) is treated as the answer to ``key`` when ``key`` is
        the only expected field it could be;
      * per-item answers may sit directly under the field key or be named
        by item (claim_1_verdict, <issue_id>, ...);
      * a stray top-level meta record (e.g. 'random_in_group_by') that is
        a {state,value} record and matches the field's hint is accepted as
        a single-item answer.
    """
    ent = labels.get(key)
    if ent is None:
        # literal placeholder echo: the model answered under a key named
        # 'field_key'.  Two layouts are seen:
        #   a) {'field_key': {'state',...}}  — a record nested under it
        #   b) {'field_key': '<field_name>', 'state': ..., 'value': ...}
        #      — the field name as a STRING marker with state/value
        #      siblings at the top of the label block.
        fk = labels.get('field_key')
        if isinstance(fk, dict):
            ent = fk
        elif (isinstance(fk, str) and fk == key
              and ('state' in labels or 'value' in labels)):
            ent = {'state': labels.get('state'),
                   'value': labels.get('value'),
                   'evidence': labels.get('evidence')}
            if ent.get('state') is None and ent.get('value') is None:
                ent = None
    if ent is not None:
        if not isinstance(ent, dict):
            return ('field', {key: {'state': str(ent), 'value': None}})
        if 'state' in ent or 'value' in ent:
            return ('field', {key: ent})
        if all(isinstance(v, dict) for v in ent.values()) and ent:
            return ('per_item', ent)
        # a record of non-dict per-item values (e.g. claim_1: "SUPPORTED")
        if ent:
            return ('per_item', {k: {'state': 'observed', 'value': v,
                                     'evidence': None}
                                 for k, v in ent.items()})
    # the judge answered with per-item keys directly (claim_1_verdict,
    # <issue_id>, ...).  A key is a per-item record when it is a dict with
    # state/value and its name matches a hint for this field.  A dict with
    # NO state/value keys (a stray meta record like 'evidence'/'notes')
    # does not count.
    hints = _PER_ITEM_HINTS.get(key, ())
    per = {}
    meta_keys = {'field_key', key, 'evidence', 'notes', 'confidence',
                 'annotator_confidence', 'pair_token', 'item_token',
                 'explanations', 'unresolved_fields', 'member_judgments',
                 'reference_set_usable'}
    for k, v in labels.items():
        if k in meta_keys or not isinstance(v, dict):
            continue
        if not ('state' in v or 'value' in v):
            continue
        if not hints or any(h in k.lower() for h in hints):
            per[k] = v
    if per:
        return ('per_item', per)
    # generic fallback: a SINGLE top-level {state,value} record that is not
    # a meta key is the field's answer (a one-issue / one-claim layout
    # named by the item, e.g. 'random_in_group_by').
    if hints:
        generic = {k: v for k, v in labels.items()
                   if k not in meta_keys and isinstance(v, dict)
                   and ('state' in v or 'value' in v)}
        if generic:
            return ('per_item', generic)
    # final_answer_span may be answered via final_answer's own evidence:
    # the span field IS the quoted text carrying the answer, and the
    # answer record's evidence is exactly that quote.  This rebinds
    # existing content; it invents nothing.
    if key == 'final_answer_span':
        fa = labels.get('final_answer')
        if isinstance(fa, dict) and fa.get('state') in (
                'observed', 'ambiguous'):
            ev = fa.get('evidence')
            if isinstance(ev, str) and ev.strip():
                # the answer record's evidence quote IS the span; rebind
                # the existing content under the span field, carrying the
                # source state through
                return ('field', {'final_answer_span': {
                    'state': fa.get('state'), 'value': ev,
                    'evidence': ev}})
    return None


def _ev_nonempty(v: Any) -> bool:
    """True when an evidence payload carries at least one non-empty
    citation (a string, a list of strings, or a dict of spans)."""
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, list):
        return any((isinstance(x, str) and x.strip()) for x in v)
    if isinstance(v, dict):
        return any(_ev_nonempty(x) for x in v.values())
    return False


def _evidence_like_keys(node: Dict[str, Any]) -> List[str]:
    """Keys whose NAME signals they hold an evidence citation.  This is
    deliberately broad on the KEY NAME (any 'evidence'-bearing key) but the
    VALUE must still be a real citation (_ev_nonempty)."""
    out: List[str] = []
    for k in node:
        if 'evidence' in str(k).lower():
            out.append(k)
    return out


def _gather_evidence(ent: Dict[str, Any],
                     labels: Dict[str, Any], key: str) -> Any:
    """Collect every evidence citation a field answer carries, wherever the
    judge put it.  Recognised placements (all information-preserving):

      * the record's own ``evidence`` (string / list / dict-of-spans);
      * any evidence-bearing key anywhere inside the answer's ``value``
        (per-item ``identified_evidence``, ``repair_evidence``, a nested
        ``evidence`` list, ...);
      * a sibling top-level ``evidence`` record (string OR a nested
        {state,value,evidence} record);
      * evidence bundled into the record's ``notes`` (commonly as a
        "Evidence: ..." run).

    A NON-empty collection means the substantive requirement is met; an
    empty collection means no span was cited and the caller fails the
    record.  Recognising more PLACEMENTS of a citation never invents one.
    """
    found: List[Any] = []

    def harvest(node: Any) -> None:
        if isinstance(node, dict):
            for k in _evidence_like_keys(node):
                if _ev_nonempty(node[k]):
                    found.append(node[k])
            # notes may carry a bundled citation
            notes = node.get('notes')
            if isinstance(notes, str) and 'evidence' in notes.lower():
                found.append(notes)
            for v in node.values():
                if isinstance(v, dict):
                    harvest(v)
        elif isinstance(node, list):
            for v in node:
                if isinstance(v, dict):
                    harvest(v)

    # 1 + 2: the record and its value subtree
    harvest(ent)
    # 3: sibling top-level evidence record (string, dict, or nested record)
    sib = labels.get('evidence')
    if _ev_nonempty(sib) or isinstance(sib, dict):
        if isinstance(sib, dict):
            for ek in _evidence_like_keys(sib):
                if _ev_nonempty(sib[ek]):
                    found.append(sib[ek])
            found.append(sib)
        else:
            found.append(sib)
    # 4: top-level notes that carry a citation (the judge's own words,
    #    bundled with the answer rather than attached to the record)
    notes = labels.get('notes')
    if isinstance(notes, str) and 'event' in notes.lower() and \
            ('evidence' in notes.lower() or ':' in notes):
        found.append(notes)
    return found


def validate_extraction(labels: Dict[str, Any],
                       expected_fields: List[str],
                       evidence_required: bool = True
                       ) -> Dict[str, Any]:
    """Validate one extraction item's label block.

    STRUCTURE-TOLERANT but evidence-strict.  A field may be answered

      * under its own key as one {state, value, evidence} record, or
      * as per-item records, either nested under the field key or named
        directly (claim_1_verdict, <issue_id> ...);

    and evidence may be a quoted span (string) or a per-item span map.
    What the validator enforces:
      * every expected field is answered in SOME form (not just the exact
        template shape — a judge that answers claim_1_verdict directly
        has still answered the claim_verdicts field);
      * states come from the canonical enum;
      * nontrivial labels (observed / ambiguous) carry evidence.
    Only a label that skips a field entirely, or states a value without
    any evidence, fails.
    """
    problems: List[str] = []
    answered_keys: set = set()
    # a top-level 'evidence' string in the label block applies to every
    # record it accompanies (a common judge layout for the issue task)
    top_ev = labels.get('evidence')
    top_ev_found = (isinstance(top_ev, str) and top_ev.strip())
    for key in expected_fields:
        found = _find_field_labels(labels, key)
        if found is None:
            problems.append(f'field {key} missing')
            continue
        mode, entries = found
        for item_k, ent in entries.items():
            answered_keys.add(item_k)
            if not isinstance(ent, dict):
                problems.append(f'{key}/{item_k}: must be an object')
                continue
            ev = _gather_evidence(ent, labels, key)
            _validate_state_record(
                ent, f'{key}[{item_k}]' if mode == 'per_item' else key,
                problems, evidence_required,
                evidence=bool(ev),
                top_level_evidence=top_ev_found)
    # stray top-level keys the judge invented are NOT a failure (they may
    # be a legitimate per-item layout); but record them for audit
    return {'ok': not problems, 'problems': problems,
            'n_fields': len(expected_fields),
            'answered_keys': sorted(answered_keys)}


def _pair_member_fields() -> List[str]:
    return ['assertions_differs', 'decisions_differs', 'actions_differs',
            'outcomes_differs']


def _normalize_pair(rec: Dict[str, Any], n_members: int) -> Dict[str, Any]:
    """Information-preserving normalisation of a pair record onto the
    canonical shape.  Accepts the layouts a judge model produces:
      * judgments under 'members' (a list of {member_index, *_differs,
        evidence}) as well as 'member_judgments';
      * 'pair_token' optional (the orchestrator owns the token, so its
        absence is NOT a defect);
      * 'reference_set_usable' optional — when the judge judged each member
        directly, usability is inferred (None, not invented).
    Returns the normalised record plus the problems list."""
    problems: List[str] = []
    mj = rec.get('member_judgments')
    if not isinstance(mj, list):
        cand = rec.get('members')
        if isinstance(cand, list) and cand and all(
                isinstance(m, dict) and (
                    'assertions_differs' in m or 'member_index' in m
                    or 'differs' in str(m).lower())
                for m in cand):
            mj = cand
    if not isinstance(mj, list) or len(mj) != n_members:
        problems.append(f'member_judgments must be a list of {n_members}')
        mj = []
    out = dict(rec)
    out['pair_token'] = rec.get('pair_token')  # kept when present

    # field-NAME normalisation (information-preserving renames): judges
    # answer the same four dimensions under 'assertions_differs' or
    # 'assertions', and index under 'member_index' or 'index'.
    _RENAME = {'assertions': 'assertions_differs',
               'decisions': 'decisions_differs',
               'actions': 'actions_differs',
               'outcomes': 'outcomes_differs',
               'index': 'member_index'}
    for m in mj:
        if isinstance(m, dict):
            for old, new in _RENAME.items():
                if old in m and new not in m:
                    m[new] = m[old]
    # reference_set_usable: accept yes/no/ambiguous/unavailable or None
    ru = rec.get('reference_set_usable')
    if ru is None and 'members' in rec and 'member_judgments' not in rec:
        # the judge omitted the usability answer but judged the members;
        # leave it None (NOT invented) — the evaluator treats None as
        # 'usability not answered' and excludes from precision
        out['reference_set_usable'] = None
    else:
        out['reference_set_usable'] = ru
        if ru not in PAIR_STATES:
            problems.append(
                f'reference_set_usable {ru!r} not in {PAIR_STATES}')
    # per-member validation
    seen = set()
    for m in mj:
        if not isinstance(m, dict):
            problems.append('member_judgments entry must be object')
            continue
        ix = m.get('member_index')
        if ix is None:
            # a members-layout may index positionally
            ix = mj.index(m)
            m['member_index'] = ix
        seen.add(ix)
        for fld in _pair_member_fields():
            _check_state(m.get(fld), f'member{ix}.{fld}',
                         PAIR_STATES, problems)
        diff = [f for f in _pair_member_fields() if m.get(f) == 'yes']
        if diff and not (isinstance(m.get('evidence'), str)
                         and m['evidence'].strip()):
            problems.append(
                f'member {ix}: a "yes" difference requires an evidence '
                f'span (got {m.get("evidence")!r})')
    if mj and seen != set(range(n_members)):
        problems.append(
            f'member_judgments indexes {sorted(i for i in seen if i is not None)} '
            f'!= {list(range(n_members))}')
    out['member_judgments'] = mj
    out['problems'] = problems
    return out


def validate_pair(rec: Dict[str, Any], n_members: int
                  ) -> Dict[str, Any]:
    """Validate one pair item's annotation record (normalised)."""
    norm = _normalize_pair(rec, n_members)
    admi = rec.get('any_difference_member_index')
    problems = list(norm.get('problems') or [])
    if admi is not None and (not isinstance(admi, int)
                             or not (0 <= admi < n_members)):
        problems.append(f'any_difference_member_index {admi!r} must be an '
                        f'int in [0,{n_members}) or null')
    return {'ok': not problems, 'problems': problems,
            'n_members': n_members, 'normalized': norm}


def extraction_expected_fields(task_block: Dict[str, Any]) -> List[str]:
    return [f['key'] for f in (task_block.get('fields') or [])]
