"""
Judge annotation packet builder.

WHY A REBUILT EXCERPT AND NOT THE TEMPLATE'S
--------------------------------------------
The existing blinded packets
(``analysis/measurement_validation/annotation_templates/``) are REUSED for
their item tokens, annotation task block, field schemas, state glossary and
blinding structure — and are NEVER modified.  A completeness check, however,
found that their trace excerpts carry NO agent-authored text: the template's
scrubber (``make_annotation_materials._scrub_event``) preserves the keys
``arguments/result/content/message/text/...`` while this corpus' event
schema stores authored content in ``output_text`` — a key the scrubber does
not list.  Every template packet item therefore reduces to bare tool-call
metadata, which makes the extraction task ("what did the agents express")
UNANSWERABLE: any labeler, human or judge, must answer ``unavailable`` on
essentially every field.  That is a defect of the packet construction, not
of the underlying record (cf. the validation status limitation: the packets
"were not validated by a human" as answerable).

This module BUILDS the judge packets under
``analysis/measurement_v7/out/judge_packets/``:

  * the item/pair TOKENS and template TASK BLOCKS are the template's, so a
    judge output keyed on a token aligns with the human-response schema;
  * each execution excerpt is rebuilt from the full trace with the SAME
    evidence base the extractors use — agent-authored text
    (reasoning / agent_handoff / final_response) plus the observable
    tool-call skeleton (event order, role, tool name, arguments, recorded
    errors) — and NO tool- or system-authored content (tool_result,
    user_input, system_init), mirroring the extractor's authored view;
  * the blinding is the framework's OWN test list
    (``tests/test_blinding.py::FORBIDDEN``, checked case-insensitively,
    exactly as that test does), plus structural identifiers that a rebuilt
    excerpt could carry.  Cell-naming tokens that the agent's own text
    mentions are redacted, so the rebuilt packet is MORE blinded than the
    template's, never less.

A build fails (and no packet is written) on any residual forbidden token.
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, os.path.join(REPO, 'analysis', 'measurement_v7'))

TEMPLATES = os.path.join(REPO, 'analysis', 'measurement_validation',
                         'annotation_templates')
VALID_CACHE = os.path.join(REPO, 'analysis', 'measurement_validation',
                           '.cache')
OUT = os.path.join(REPO, 'analysis', 'measurement_v7', 'out')

#: the SAME event types the extractors read as agent-authored
AUTHORED_EVENTS = ('reasoning', 'agent_handoff', 'final_response')

#: framework's own blinding test list, checked case-insensitively as in
#: tests/test_blinding.py::test_no_packet_leaks_a_condition_or_lep_token
_FORBIDDEN_TEST = (
    'LEP_', 'HANDOFF_CORRUPTION', 'INDIRECT_PROMPT', 'INPUT_DISREGARD',
    'TOOL_RESULT_CORRUPTION', 'MEMORY_POISONING',
    'single_origin', 'many_to_one', 'one_to_many',
    'review_loop', 'branch_and_verify', 'coordinator_workers',
    'code_review', 'financial_analysis', 'research_synthesis',
    'finq', 'scifact', 'swe_',
)

#: structural identifiers a rebuilt excerpt must not carry
_FORBIDDEN_IDS = ('trace_id', 'execution_id', 'fixture_id', 'attempt_id',
                  'baseline_deviation', 'bnd_v4', 'measurement_v6',
                  'measurement_v7', 'per_trace', 'ground_truth',
                  'expected_answer', 'answer_calculation', 'specroot',
                  'scenario_id', 'workspace_fixtures', 'consolidated_')

#: cell-naming tokens that agent-authored text may legitimately mention
#: (topology/family words).  Redacted from excerpt text; the framework's
#: test checks the whole packet, so these must not survive the build.
_SCRUB_RE = re.compile(
    r'(?i)\b(single_origin|many_to_one|one_to_many|review_loop|'
    r'branch_and_verify|coordinator_workers|code_review|'
    r'financial_analysis|research_synthesis|finq\w*|scifact\w*|'
    r'swe_\w+)\b')


def _scrub_text(text: str) -> str:
    """Redact structural identifiers and cell-naming tokens from a text
    field.  Redaction markers keep the sentence readable but hide the cell."""
    for tok in _FORBIDDEN_IDS:
        text = text.replace(tok, '')
    text = _SCRUB_RE.sub('[cell-redacted]', text)
    return text


def _clip(text: str, n: int = 4000) -> str:
    return text[:n] + (' …[truncated]' if len(text) > n else '')


def _excerpt_from_trace(trace: Dict[str, Any],
                        role_map: Dict[str, str] = None
                        ) -> Dict[str, Any]:
    """A GT-free, identity-free excerpt built on the EXTRACTOR'S evidence
    base: agent-authored events with their output_text, and the
    tool-call skeleton.  tool_result / user_input / system_init content is
    EXCLUDED (tool- or system-authored text; the same exclusion the
    extractors make for assertions, and it is where cell-identifying
    strings live, e.g. the system_init topology line).

    ``role_map`` pseudonymises any agent-role name that contains a
    forbidden token (e.g. ``code_reviewer`` -> ``ROLE-1``) so the role
    structure stays observable without disclosing the task family.
    """
    role_map = role_map or {}
    events_out: List[Dict[str, Any]] = []
    for i, e in enumerate(trace.get('events') or []):
        et = e.get('event_type')
        raw_role = e.get('agent_role') or ''
        ev: Dict[str, Any] = {'event_index': i, 'event_type': et,
                              'agent_role': role_map.get(raw_role,
                                                          raw_role)}
        if et == 'tool_call':
            if e.get('tool_name'):
                ev['tool_name'] = _scrub_text(e['tool_name'])
            args = e.get('tool_arguments')
            if args:
                # the fixture's document names encode the task family
                # (e.g. 'documents/financial_analysis.md'); scrub the
                # family token so the target stays observable in shape
                # (a file was read/written) but the cell is not disclosed.
                ev['tool_arguments'] = _clip(
                    _scrub_text(json.dumps(
                        args, ensure_ascii=False, default=str)))
            if e.get('tool_error') not in (None, '', False):
                ev['tool_error'] = _clip(_scrub_text(str(e['tool_error'])))
        elif et in AUTHORED_EVENTS:
            txt = e.get('output_text') or ''
            if txt:
                ev['output_text'] = _clip(_scrub_text(txt), 4000)
        events_out.append(ev)
    out: Dict[str, Any] = {'events': events_out}
    fr = trace.get('final_response')
    content = fr.get('content') if isinstance(fr, dict) else (
        fr if isinstance(fr, str) else None)
    if content:
        out['final_response'] = _clip(_scrub_text(str(content)), 8000)
    return out


def _read_jsonl(path: str) -> List[Dict[str, Any]]:
    return [json.loads(l) for l in open(path) if l.strip()]


def _trace_index() -> Dict[str, str]:
    return {json.loads(l)['trace_id']: json.loads(l)['path']
            for l in open(os.path.join(VALID_CACHE, 'trace_index.jsonl'))}


def _check(text: str) -> List[str]:
    low = text.lower()
    hits = [s for s in _FORBIDDEN_TEST if s.lower() in low]
    hits += [s for s in _FORBIDDEN_IDS if s in text]
    return hits


def _leaking_roles(roles: List[str]) -> Dict[str, str]:
    """Pseudonymise agent-role names that contain a forbidden token.

    A role like ``code_reviewer`` discloses the task family; the annotation
    guide needs role STRUCTURE (which roles called what), so a leaking role
    name is replaced by a deterministic, within-packet-stable pseudonym
    (``ROLE-1``, ``ROLE-2`` ...) that preserves distinguishability without
    disclosing the name.  Role names that contain no forbidden token are
    kept verbatim.  Applied both to the structured ``agent_role`` field and
    to free text (tool arguments, handoff content) via word-boundary
    replacement so all mentions stay consistent within a packet.
    """
    order = sorted(set(roles))
    role_map: Dict[str, str] = {}
    n = 0
    for name in order:
        low = name.lower()
        if any(t.lower() in low for t in _FORBIDDEN_TEST):
            n += 1
            role_map[name] = f'ROLE-{n}'
    return role_map


def _collect_roles(traces: List[Dict[str, Any]]) -> List[str]:
    roles = set()
    for tr in traces:
        for e in tr.get('events') or []:
            r = e.get('agent_role')
            if r:
                roles.add(r)
    return sorted(roles)


def _apply_role_map(text: str, role_map: Dict[str, str]) -> str:
    for real, pseudo in role_map.items():
        text = re.sub(r'(?<![A-Za-z0-9_])' + re.escape(real) + r'(?![A-Za-z0-9_])',
                      pseudo, text)
    return text


def build_extraction_packets() -> Dict[str, Any]:
    """Rebuild the 300 extraction items (same tokens/task blocks/order)."""
    idx = _trace_index()
    template = _read_jsonl(os.path.join(
        TEMPLATES, 'extraction_packet_annotator_A.jsonl'))
    idmap = json.load(open(os.path.join(VALID_CACHE, 'blind_id_map.json')))
    token_to_unit = {v: k for k, v in idmap['maps']['A'].items()}
    out_dir = os.path.join(OUT, 'judge_packets')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, 'extraction_packet_judge.jsonl')
    n = 0
    problems: List[str] = []
    with open(out_path, 'w') as f:
        for item in template:
            tok = item['item_token']
            unit_id = token_to_unit.get(tok)
            if unit_id is None:
                problems.append(f'{tok}: token not in blinding map')
                continue
            tid = unit_id.split('#')[0]
            path = idx.get(tid)
            if not path or not os.path.exists(path):
                problems.append(f'{tok}: trace {tid} not in index')
                continue
            trace = json.load(open(path))
            item2 = dict(item)
            item2['execution_excerpt'] = _excerpt_from_trace(trace)
            item2['item_token'] = tok
            item2['excerpt_provenance'] = (
                'rebuilt: authored events + tool-call skeleton; '
                'tool_result/user_input/system_init content excluded '
                '(same authored-view the extractors use)')
            hits = _check(json.dumps(item2, ensure_ascii=False))
            if hits:
                problems.append(f'{tok}: forbidden {hits[:3]}')
                continue
            f.write(json.dumps(item2, ensure_ascii=False) + '\n')
            n += 1
    rep = {'n_items': n, 'problems': problems,
           'n_template_items': len(template),
           'provenance': ('template tokens + task blocks; excerpts rebuilt '
                           'on the extractor evidence base'),
           'out': out_path, 'ok': not problems and n == len(template)}
    json.dump(rep, open(os.path.join(out_dir,
                                     'extraction_build_report.json'), 'w'),
              indent=2)
    return rep


def build_pair_packets() -> Dict[str, Any]:
    """Rebuild the 150 pair groups (same tokens/order/member index)."""
    idx = _trace_index()
    template = _read_jsonl(os.path.join(
        TEMPLATES, 'pair_packet_annotator_A.jsonl'))
    roles = json.load(open(os.path.join(VALID_CACHE,
                                        'blind_pair_roles.json')))
    token_to_trace = {t: r['trace_id']
                      for a in roles['roles'].values()
                      for t, r in a.items()}
    out_dir = os.path.join(OUT, 'judge_packets')
    out_path = os.path.join(out_dir, 'pair_packet_judge.jsonl')
    n = 0
    problems: List[str] = []
    role_maps_by_pair: Dict[str, Dict[str, str]] = {}
    with open(out_path, 'w') as f:
        for item in template:
            item2 = dict(item)
            members2 = []
            for m in item.get('members') or []:
                tok = m.get('item_token')
                tid = token_to_trace.get(tok)
                if not tid or tid not in idx:
                    problems.append(
                        f'{item.get("pair_token")}: member {tok} '
                        f'unresolvable')
                    continue
                m2 = dict(m)
                m2['execution_excerpt'] = _excerpt_from_trace(
                    json.load(open(idx[tid])))
                m2['item_token'] = tok
                members2.append(m2)
            # whole-packet role pseudonymization: role names that leak the
            # task family (e.g. 'code_reviewer') are replaced by stable
            # pseudonyms in ALL free text of this packet, so members stay
            # mutually consistent and the family is not disclosed
            member_traces = [json.load(open(idx[t2t]))
                             for m in (item.get('members') or [])
                             for t2t in [token_to_trace.get(m.get('item_token'))]
                             if t2t in idx]
            role_map = _leaking_roles(_collect_roles(member_traces))
            packet = json.dumps(item2, ensure_ascii=False)
            for real, pseudo in role_map.items():
                packet = _apply_role_map(packet, {real: pseudo})
            item2 = json.loads(packet)
            # NOTE: the real->pseudonym role map is recorded in the build
            # report (keyed by pair_token), NOT in the judge-facing packet:
            # embedding the real names here would unblind the packet.
            hits = _check(packet)
            if hits:
                problems.append(
                    f'{item.get("pair_token")}: forbidden {hits[:3]}')
                continue
            if role_map:
                role_maps_by_pair[str(item.get('pair_token'))] = role_map
            f.write(packet + '\n')
            n += 1
    rep = {'n_items': n, 'problems': problems,
           'n_template_items': len(template),
           'out': out_path, 'ok': not problems and n == len(template),
           'role_pseudonym_maps': role_maps_by_pair,
           'note': ('role pseudonyms are in the report, NOT the judge-facing '
                    'packets; the report is the confidential side of the '
                    'blinding')}
    json.dump(rep, open(os.path.join(out_dir, 'pair_build_report.json'), 'w'),
              indent=2)
    return rep


if __name__ == '__main__':
    r1 = build_extraction_packets()
    print('extraction:', r1['n_items'], '/', r1['n_template_items'],
          'problems:', r1['problems'][:5])
    r2 = build_pair_packets()
    print('pair:', r2['n_items'], '/', r2['n_template_items'],
          'problems:', r2['problems'][:5])
