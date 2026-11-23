"""Corrected financial specification set (specrev-1) — a VERSIONED OVERLAY.

The frozen specs under ``analysis/bnd_v41_cw_swe200_provisional/specroot``
are NEVER modified.  This module layers ``corrections.json`` over them:

  * 35 confirmed unit defects  -> ``answer.unit`` corrected (value unchanged);
  * 1 ground-truth value bug (fin_finqa_219_adi_2015) -> recorded, NOT
    corrected here; the fixture is EXCLUDED from scalar acceptance and the
    corrected headline grading unless a separate documented decision is made;
  * 2 no-answer fixtures (financial_clean, financial_version_conflict) ->
    classified ``ungradeable_by_design`` for scalar task-quality grading;
  * 20 negative-GT fixtures -> sign is intentional; extractor-side handling,
    no spec change.

Also records one NEW finding from development evaluation (NOT applied):
``fin_finqa_084_bdx_2018`` carries a suspected inverse-ratio value bug
(GT 0.6618 = interest/service while the question asks service/interest =
1.51).  It is flagged for the review queue, not silently corrected.

``load_corrected_spec(fixture_id)`` returns a deep copy of the frozen
manifest with the overlay applied; ``corrected_unit_for(fid)`` is what the
v5 extractor consumes (GT-free — units only, never values, in selection).
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, Optional

HERE = Path(__file__).parent
FROZEN_SPECROOT = Path('/u/kdhande/agent-graph/agent-graph-v3'
                       '/analysis/bnd_v41_cw_swe200_provisional/specroot')
CORR = HERE / 'corrections.json'


def load_corrections() -> Dict[str, Any]:
    return json.loads(CORR.read_text())


def corrected_unit_for(fixture_id: str) -> Optional[Dict[str, Any]]:
    """Return ``{'unit': corrected_unit, 'basis': ...}`` or None.

    GT-free: only the *unit* (task-side semantics) is returned; the answer
    value stays in the frozen spec and is used only in stage-2 grading.
    For excluded fixtures this returns ``{'unit': None, 'exclude': True}``.
    """
    c = load_corrections()
    if fixture_id in c['no_answer_key_excluded']:
        return {'unit': None, 'exclude': True, 'basis': 'no_answer_key'}
    if fixture_id in c['value_bugs']:
        return {'unit': c['value_bugs'][fixture_id]['correct_unit'],
                'exclude': True, 'basis': 'value_bug_excluded'}
    if fixture_id in c['confirmed_unit_defects']:
        return {'unit': c['confirmed_unit_defects'][fixture_id],
                'exclude': False, 'basis': 'unit_defect_corrected'}
    return None


def load_corrected_spec(fixture_id: str,
                        apply_unit_corrections: bool = True,
                        apply_value_corrections: bool = True
                        ) -> Dict[str, Any]:
    """Deep copy of the frozen manifest + overlay.  The frozen file on disk
    is never touched."""
    m = json.loads((FROZEN_SPECROOT / fixture_id / 'manifest.json').read_text())
    out = copy.deepcopy(m)
    c = load_corrections()
    rf = out.setdefault('required_facts', {})
    ans = rf.get('answer')
    if apply_unit_corrections and ans is not None \
            and fixture_id in c['confirmed_unit_defects']:
        orig = ans.get('unit')
        ans['unit'] = c['confirmed_unit_defects'][fixture_id]
        ans.setdefault('_correction_provenance', {})['unit'] = {
            'original': orig, 'corrected': ans['unit'],
            'record': 'specrev-1 (proposed, frozen spec untouched)',
        }
    if apply_value_corrections and ans is not None \
            and fixture_id in c.get('value_corrections', {}):
        vc = c['value_corrections'][fixture_id]
        orig_val = ans.get('value')
        ans['value'] = vc['corrected_gt']
        if vc.get('corrected_unit') and not ans.get('unit'):
            ans['unit'] = vc['corrected_unit']
        ans.setdefault('_correction_provenance', {})['value'] = {
            'original': orig_val, 'corrected': ans['value'],
            'operation': vc.get('operation', ''),
            'record': 'specrev-1 (inverse-ratio correction)',
        }
    return out


if __name__ == '__main__':
    c = load_corrections()
    print('confirmed unit defects:', len(c['confirmed_unit_defects']))
    print('value bugs:', list(c['value_bugs']))
    print('no-answer excluded:', c['no_answer_key_excluded'])
    print('negative GT (no change):', len(c['negative_gt_specs_sign_is_intended']))
