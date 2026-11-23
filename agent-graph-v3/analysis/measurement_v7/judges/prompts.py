"""
Judge prompts: deterministic, versioned, hash-recorded.

Each judge sees ONLY the blinded packet content (execution excerpts + the
annotation task block).  It must NOT see:

  * frozen / v6 / v7 predictions
  * LEP labels or benign/perturbed identities
  * expected task answers (no GT anywhere in the packet; verified by test)
  * the other judge's annotations

The prompt is a pure function of (packet, prompt_version); its SHA-256 is
recorded on every call so a judge output can be re-derived and compared.
Evidence spans are REQUIRED for nontrivial (observed) labels; the schema in
``schemas.py`` enforces this.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

PROMPT_VERSION = 'v7-judge-prompt-1'


def _excerpt_text(member: Dict[str, Any]) -> str:
    """Render one execution member to text for the judge.

    The packet's ``execution_excerpt`` is already GT-free and blinded; we
    render it verbatim so the judge reads exactly what an annotator would.
    Event fields are rendered compactly; authored text (reasoning / handoff /
    final) is the primary evidence source.
    """
    exc = member.get('execution_excerpt') or {}
    lines: List[str] = []
    lines.append('=== TASK PROMPT ===')
    lines.append(member.get('task_prompt') or '')
    for i, e in enumerate(exc.get('events') or []):
        etype = e.get('event_type')
        role = e.get('agent_role') or ''
        tname = e.get('tool_name') or ''
        out = (e.get('output_text') or '').strip()
        if etype in ('reasoning', 'agent_handoff') and out:
            lines.append(f'--- event {i} [{etype}] role={role} ---')
            lines.append(out[:2000])
        elif etype == 'tool_call':
            args = e.get('tool_arguments')
            if isinstance(args, str):
                # the rebuilt packets serialise tool_arguments as a JSON
                # string; render a compact head so targets are observable
                args_view = args[:220]
            elif args:
                args_view = json.dumps(args, default=str)[:220]
            else:
                args_view = ''
            tail = f' args={args_view}' if args_view else ''
            lines.append(f'--- event {i} [tool_call] role={role} '
                         f'tool={tname}{tail} ---')
        # tool_result / system_init / user_input carry tool/system content;
        # the judge is told the excerpt contains only what it needs
    fr = exc.get('final_response')
    if fr:
        lines.append('--- FINAL RESPONSE ---')
        lines.append(str(fr)[:4000])
    return '\n'.join(lines)


def extraction_prompt(packet: Dict[str, Any],
                      judge_name: str) -> Dict[str, str]:
    """System + user prompt for one extraction packet item.

    Returns {'system':..., 'user':..., 'prompt_sha256':..., 'version':...}.
    """
    item_kind = packet.get('item_token', '')
    task = packet.get('annotation_task') or {}
    fields = task.get('fields') or []
    states = task.get('states') or ['observed', 'absent', 'ambiguous',
                                     'unavailable']
    gloss = task.get('state_glossary') or {}
    lines: List[str] = []
    lines.append('You are an independent annotation judge for a scientific '
                 'claim-verification / code-review / financial-analysis '
                 'audit.  You report ONLY what the agents expressed.  You '
                 'are NOT told whether the execution was perturbed, what the '
                 'expected answer is, or what any other system predicted.  '
                 'Do not grade correctness.')
    lines.append('For each field below, produce a state in '
                 f'{" | ".join(states)}.  Use observed only when you can '
                 'quote the exact text that states it, and record that quote '
                 'as evidence (verbatim, <=200 characters, with its event '
                 'index or "final").  Use absent only when the agents spoke '
                 'and did not say it; use unavailable when the record does '
                 'not let you tell; use ambiguous when it is genuinely '
                 'undecidable between two readings (say which).')
    for f in fields:
        lines.append(f'FIELD "{f["key"]}": {f.get("prompt","")}')
        for rule in f.get('rules', []):
            lines.append(f'   rule: {rule}')
        if f.get('enum'):
            lines.append(f'   value enum: {f["enum"]}')
        if f.get('type') == 'per_item_enum':
            lines.append('   value is an object mapping each item id (e.g. '
                         'claim_1_verdict) to one enum entry, each with its '
                         'own evidence.')
        if f.get('type') == 'per_item_flags':
            lines.append('   value is an object mapping each issue id to '
                         '{identified, repair_only, not_identified, '
                         'unavailable} flags, each with its own evidence.')
        if f.get('type') == 'numeric_with_unit':
            lines.append('   value is the numeric answer as written, with '
                         'unit/scale preserved; do not compute, round, '
                         'rescale, or judge correctness.')
    lines.append('Output STRICT JSON, no prose, matching: '
                 '{"item_token": "...", "labels": {field_key: {"state": '
                 '..., "value": ..., "evidence": ...}}, '
                 '"annotator_confidence": "high|medium|low", "notes": "..."}')
    lines.append('Evidence is required for every observed value and for '
                 'every ambiguous value (quote the competing readings).')

    system = '\n'.join(lines)
    user = 'Execution record (member excerpt):\n' + _excerpt_text(packet)
    blob = json.dumps({'version': PROMPT_VERSION, 'judge': judge_name,
                       'system': system, 'user': user,
                       'item_token': item_kind}, sort_keys=True)
    return {'system': system, 'user': user, 'version': PROMPT_VERSION,
            'prompt_sha256': hashlib.sha256(blob.encode()).hexdigest()}


def pair_prompt(packet: Dict[str, Any], judge_name: str) -> Dict[str, str]:
    """System + user prompt for one pair (comparison group) packet item."""
    item_token = packet.get('pair_token', '')
    task = packet.get('annotation_task') or {}
    dims = task.get('dimensions') or []
    lines: List[str] = []
    lines.append('You are an independent annotation judge for a behavioural-'
                 'divergence study.  You are given a GROUP of executions: '
                 'exactly one may have been exposed to a perturbation; you '
                 'are NOT told which, and member order is arbitrary.  Judge '
                 'observable difference, not quality.  A paraphrase is not a '
                 'difference; a different-but-equally-correct answer IS a '
                 'difference.  Do not invent tool errors: absence of a '
                 'recorded error is not evidence of an error.  Judge '
                 'behaviour, not outcome quality.')
    lines.append('First answer reference_set_usable: are the NON-target '
                 'members consistent enough to serve as a control? If they '
                 'differ substantially from each other, answer "no" — the '
                 'comparison is unusable, not negative.')
    for d in dims:
        lines.append(f'DIMENSION: {d}')
    lines.append('For each member index, answer assertions/decisions/actions'
                 '/outcomes differs = yes|no|ambiguous|unavailable, with '
                 'evidence when you say "yes" (name what differs and quote '
                 'the member text, <=200 chars).  If any member differs, '
                 'name exactly ONE member index in '
                 'any_difference_member_index and the dimension(s).')
    lines.append('Output STRICT JSON, no prose, matching: '
                 '{"pair_token": "...", "reference_set_usable": '
                 '"yes|no|ambiguous|unavailable", "member_judgments": '
                 '[{"member_index":0,"assertions_differs":...,'
                 '"decisions_differs":...,"actions_differs":...,'
                 '"outcomes_differs":...,"evidence":"..."}], '
                 '"any_difference_member_index": int|null, '
                 '"annotator_confidence":"high|medium|low","notes":"..."}')
    system = '\n'.join(lines)
    members_txt: List[str] = []
    for m in packet.get('members') or []:
        members_txt.append(
            f'=== MEMBER {m.get("member_index")} ===\n'
            + _excerpt_text(m))
    user = 'Comparison group:\n' + '\n\n'.join(members_txt)
    blob = json.dumps({'version': PROMPT_VERSION, 'judge': judge_name,
                       'system': system, 'user': user,
                       'pair_token': item_token}, sort_keys=True)
    return {'system': system, 'user': user, 'version': PROMPT_VERSION,
            'prompt_sha256': hashlib.sha256(blob.encode()).hexdigest()}


def input_sha(packet: Dict[str, Any], judge_name: str) -> str:
    """SHA-256 of the exact packet content a judge sees (for provenance)."""
    return hashlib.sha256(json.dumps(packet, sort_keys=True).encode()
                          + judge_name.encode()).hexdigest()
