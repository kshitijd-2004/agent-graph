"""
Adjudication workflow (requirement 9).

For every item on which Judge A and Judge B disagree (or where either
abstained, so the coverage record is complete), an adjudicator resolves the
item.  Two adjudicator classes are supported, and the class is recorded on
the verdict so it can NEVER be read as the other:

  * ``human``     — a human adjudicator; the verdict is the reference.
  * ``ai_model``  — a separately configured third model; the verdict is
                    labelled ``AI adjudication, NOT human ground truth``.

The adjudicator sees: the original evidence (the execution excerpt), the
annotation rubric (the annotation task block), and BOTH candidate labels —
WITHOUT model identity.  An evidence-based explanation is mandatory.  A
genuinely ambiguous item is left ``unresolved`` — the verdict is not forced.

Outputs (written only under measurement_v7/out/):
  * judge_a labels, judge_b labels (preserved verbatim from the judge runs)
  * disagreement manifest
  * adjudicated AI labels   (``adjudicated_ai.jsonl``)
  * unresolved cases        (``adjudicated_unresolved.jsonl``)

The existing human annotation response files and the human
``ADJUDICATED.jsonl`` are NEVER written by this module.
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, os.path.join(REPO, 'analysis', 'measurement_v7',
                                'judges'))
from providers import JudgeConfig          # noqa: E402
from runner import JudgeClient            # noqa: E402
from prompts import PROMPT_VERSION        # noqa: E402
import hashlib                              # noqa: E402

OUT = os.path.join(REPO, 'analysis', 'measurement_v7', 'out')


def _disagreement(a: Dict[str, Any], b: Dict[str, Any],
                  fields: List[str]) -> List[str]:
    """The fields on which two annotation records disagree (or one abstained
    while the other labelled).  Both-unavailable is NOT a disagreement — it
    is an abstention, recorded for coverage, resolved by rule, not by a
    verdict."""
    out: List[str] = []
    for f in fields:
        va = (a.get('labels') or {}).get(f)
        vb = (b.get('labels') or {}).get(f)
        sa = va.get('state') if isinstance(va, dict) else va
        sb = vb.get('state') if isinstance(vb, dict) else vb
        if sa in ('ambiguous', 'unavailable') and sb in (
                'ambiguous', 'unavailable'):
            continue
        if (sa, sb) != (sb, sa):
            out.append(f)
        elif sa == sb:
            eva = va.get('value') if isinstance(va, dict) else None
            evb = vb.get('value') if isinstance(vb, dict) else None
            if json.dumps(eva, sort_keys=True) != \
                    json.dumps(evb, sort_keys=True):
                out.append(f)
    return out


def build_disagreement_manifest(judge_a: List[Dict[str, Any]],
                                judge_b: List[Dict[str, Any]],
                                fields: List[str]
                                ) -> List[Dict[str, Any]]:
    """Align the two judge runs on item_token and find disagreements +
    abstentions.  ``judge_a``/``judge_b`` are the validated judgment
    records from the orchestrator."""
    a_by = {r.get('item_token'): r for r in judge_a}
    b_by = {r.get('item_token'): r for r in judge_b}
    manifest: List[Dict[str, Any]] = []
    for tok in sorted(set(a_by) & set(b_by)):
        ra, rb = a_by[tok], b_by[tok]
        dis = _disagreement(ra, rb, fields)
        both_unavail = all(
            str((ra.get('labels') or {}).get(f, {}).get('state')
                if isinstance((ra.get('labels') or {}).get(f), dict)
                else None) in ('unavailable', 'ambiguous')
            and str((rb.get('labels') or {}).get(f, {}).get('state')
                    if isinstance((rb.get('labels') or {}).get(f), dict)
                    else None) in ('unavailable', 'ambiguous')
            for f in fields)
        manifest.append({
            'item_token': tok,
            'disagreed_fields': dis,
            'both_abstained': both_unavail and not dis,
            'a': ra, 'b': rb,
            'needs_adjudication': bool(dis),
        })
    return manifest


_ADJ_PROMPT = """You are an independent ADJUDICATOR resolving a disagreement
between two independent annotation judges.  You receive (1) the execution
record, (2) the annotation rubric, and (3) TWO candidate label-sets, given
anonymously as CANDIDATE-1 and CANDIDATE-2 — you are NOT told which model
or annotator produced each.

Produce the resolved labels for the disputed fields.  For every field you
resolve you MUST give an evidence-based explanation that quotes the record
(<= 200 characters verbatim).  If the evidence is genuinely ambiguous and
cannot support a definitive label, leave the field 'ambiguous' and say so —
do NOT force a verdict.

OUTPUT FORMAT IS MANDATORY: your ENTIRE response must be exactly ONE JSON
object and NOTHING else — no reasoning prose before or after it, no markdown
fences, no 'Let me check ...' preamble.  Your reasoning goes INSIDE the
JSON (in 'explanations' and 'unresolved_reasons').  Any other output is
discarded as malformed.

{"resolved_labels": {field_key: {"state":"observed|absent|ambiguous|unavailable",
   "value":..., "evidence":"verbatim quote <=200 chars",
   "explanation":"..."}},
 "explanations": {field_key: "..."},
 "unresolved_fields": ["field_key", ...],
 "unresolved_reasons": {field_key: "..."},
 "confidence": "high|medium|low"}
"""


def adjudicate(manifest: List[Dict[str, Any]], packets: Dict[str, Dict],
               adjudicator: JudgeConfig, out_prefix: str = 'adjudicated_ai'
               ) -> Dict[str, Any]:
    """Run an AI-model adjudicator over the disagreement manifest.

    ``packets`` maps item_token -> the packet record (so the original
    evidence is shown).  The verdicts are written under
    ``out/adjudicated_ai.jsonl`` and ``out/adjudicated_unresolved.jsonl``;
    the original judge_a/judge_b records are preserved verbatim inside each
    adjudicated row."""
    os.makedirs(OUT, exist_ok=True)
    client = JudgeClient(adjudicator)
    out_rows: List[Dict[str, Any]] = []
    unresolved: List[Dict[str, Any]] = []
    n_called = n_failed = 0
    for m in manifest:
        tok = m['item_token']
        if not m['needs_adjudication']:
            # both abstained: resolve by rule (both-unavailable stays
            # unavailable), no model call
            out_rows.append({
                'item_token': tok, 'adjudicated': True,
                'class': 'ai_adjudication',
                'rule': 'both_judges_abstained->unavailable',
                'resolution': None, 'unresolved_fields': [],
                'a': m['a'], 'b': m['b'],
                'note': ('AI adjudication, NOT human ground truth.  '
                         'Both judges abstained; left unavailable by rule.'),
            })
            continue
        packet = packets.get(tok, {})
        # build the anonymised candidate blocks (no model identity)
        cand1 = json.dumps(m['a'].get('labels') or {}, sort_keys=True)
        cand2 = json.dumps(m['b'].get('labels') or {}, sort_keys=True)
        user = ('EXECUTION RECORD:\n'
                + json.dumps({k: v for k, v in packet.items()
                               if k in ('task_prompt',
                                        'execution_excerpt', 'members')},
                              sort_keys=True)[:30000]
                + '\n\nRUBRIC:\n'
                + json.dumps(packet.get('annotation_task') or {},
                             sort_keys=True)[:4000]
                + '\n\nCANDIDATE-1:\n' + cand1
                + '\n\nCANDIDATE-2:\n' + cand2)
        prompt = {'system': _ADJ_PROMPT, 'user': user,
                  'version': 'v7-adjudication-prompt-1',
                  'prompt_sha256': hashlib.sha256(
                      (user + _ADJ_PROMPT).encode()).hexdigest()}
        ih = hashlib.sha256(tok.encode()).hexdigest()
        rec = client.annotate(prompt, 'adjudicator', tok, ih)
        if rec['status'] != 'ok':
            n_failed += 1
            out_rows.append({
                'item_token': tok, 'adjudicated': False,
                'class': 'ai_adjudication',
                'reason': f'adjudicator {rec["status"]}: '
                          f'{rec.get("error", "")}',
                'a': m['a'], 'b': m['b'],
                'note': 'Adjudication FAILED; not resolved, not coerced.'})
            unresolved.append({'item_token': tok,
                               'reason': 'adjudicator_failed'})
            continue
        n_called += 1
        try:
            parsed = json.loads(re.sub(
                r'^```(?:json)?\s*|\s*```$', '',
                rec.get('text', '').strip()))
        except json.JSONDecodeError:
            n_failed += 1
            out_rows.append({
                'item_token': tok, 'adjudicated': False,
                'class': 'ai_adjudication',
                'reason': 'adjudicator output not JSON; not coerced',
                'a': m['a'], 'b': m['b'], 'raw': rec.get('text')})
            unresolved.append({'item_token': tok,
                               'reason': 'adjudicator_not_json'})
            continue
        uf = parsed.get('unresolved_fields') or []
        out_rows.append({
            'item_token': tok, 'adjudicated': not uf,
            'class': 'ai_adjudication',
            'resolution': parsed.get('resolved_labels'),
            'explanations': parsed.get('explanations') or {},
            'unresolved_fields': uf,
            'confidence': parsed.get('confidence'),
            'a': m['a'], 'b': m['b'],
            'note': ('AI adjudication, NOT human ground truth.  Resolved by '
                     'a third model; model identity withheld from the '
                     'candidate labels.'),
        })
        if uf:
            unresolved.append({'item_token': tok,
                               'reason': 'genuinely_ambiguous',
                               'fields': uf})
    ai_path = os.path.join(OUT, f'{out_prefix}.jsonl')
    with open(ai_path, 'w') as f:
        for r in out_rows:
            f.write(json.dumps(r, sort_keys=True) + '\n')
    upath = os.path.join(OUT, f'{out_prefix}_unresolved.jsonl')
    with open(upath, 'w') as f:
        for r in unresolved:
            f.write(json.dumps(r, sort_keys=True) + '\n')
    return {'n_items': len(manifest), 'n_model_called': n_called,
            'n_failed': n_failed,
            'n_unresolved': len(unresolved),
            'ai_path': ai_path, 'unresolved_path': upath,
            'class': 'ai_adjudication',
            'caveat': 'These are AI-adjudicated labels, NOT human ground '
                      'truth.  Do not report them as such.'}
