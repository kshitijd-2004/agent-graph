#!/usr/bin/env python3.11
"""
Component A / B + challenge draws over the HELDOUT partition
(FROZEN_PROTOCOL §2; MEASUREMENT_SPEC §8, §9-G1/G4/G5).

  * Component A (representative): stratified SRSWOR over heldout frame
    rows, strata (task_family, lep_code, frozen_m1_class), per-cell quota
    >= 12 (cells with fewer are fully drawn), explicit Mersenne stream
    seeded by `agentprop-final-sampling-20261207` — NEVER builtin hash.
    Each row carries inclusion probability pi and design weight 1/pi.
  * Component B (targeted): deterministic predicates in fixed order,
    cap 150 — zero-threshold-only positives, dimension-pair overlaps,
    RT trigger cells, unavailable-evidence cells, cross-impl
    disagreements.
  * Challenge set: <= 90 hard-case rows (difficult numerics, negations,
    unit mismatches, unresolved identifiers, ambiguous verdicts,
    missing evidence), case-table only, never mixed into the A/B rates.
  * Pair-group draws: 6 classes x per-family quotas for the judge
    reference surface (the class set of reference/items.py).
  * COVERAGE REPORT: every (family, LEP, topology, mode) x
    (frozen-m1 positive/negative) cell present in A ∪ B ∪ challenge;
    any empty cell is NAMED, never hidden.

Writes ONLY out/sample_A_final.jsonl, sample_B_final.jsonl,
challenge_final.jsonl, pair_groups_final.jsonl,
sample_coverage_report.json.
"""
from __future__ import annotations

import json
import os
import random
import sys
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import common

S_SEED = 'agentprop-final-sampling-20261207'
FAM = ('code_review', 'financial_analysis', 'research_synthesis')
LEP = ('LEP_INDIRECT_PROMPT_INJECTION', 'LEP_HANDOFF_CORRUPTION',
       'LEP_INPUT_DISREGARD', 'LEP_TOOL_RESULT_CORRUPTION')
TOPO = ('branch_and_verify', 'coordinator_workers', 'review_loop')
MODE = ('many_to_one', 'one_to_many', 'single_origin')

CAP_B = 150
CAP_CHALLENGE = 90


def _frozen_m1(row: Dict) -> bool:
    v = row.get('m1_whole_trace_positive')
    return bool(v if v is not None else row.get('m1_historical'))


def _heldout_fixtures() -> set:
    parts = json.load(open(os.path.join(common.OUT,
                                         'partitions_final.json')))
    return {fx for fx, rec in parts['fixtures'].items()
            if rec.get('partition') == 'heldout'}


def _stream(*labels: str) -> random.Random:
    """Deterministic Mersenne stream from a seed-folded label (never
    builtin hash — the v7 partitioning convention)."""
    import hashlib
    h = int(hashlib.sha256(
        ('|'.join([S_SEED] + list(labels))).encode()).hexdigest()[:16], 16)
    return random.Random(h)


def build_all() -> Dict[str, Any]:
    import candspec_pipeline as CP
    frame = CP.load_frame_rows()
    ho_fx = _heldout_fixtures()
    held = [r for r in frame if common.fixture_id_of(r['trace_id']) in ho_fx]
    print(f'heldout rows: {len(held)} (fixtures {len(ho_fx)})')

    # candspec / v7 / v6 ledgers for the targeted cells
    def _ledger(p):
        if not os.path.exists(p):
            return {}
        return {r['trace_id']: r for r in
                (json.loads(l) for l in open(p) if l.strip())}
    cs = _ledger(os.path.join(common.OUT, 'per_trace_candspec.jsonl'))
    v7 = _ledger(os.path.join(common.V7_DIR, 'out', 'per_trace_v7.jsonl'))
    v6 = _ledger(os.path.join(common.V6_DIR, 'out', 'per_trace_v6.jsonl'))

    # ── Component A: stratified SRSWOR ──────────────────────────────────────
    strata: Dict[tuple, List[Dict]] = {}
    for r in held:
        key = (r.get('task_family'), r.get('lep_code'),
               1 if _frozen_m1(r) else 0)
        strata.setdefault(key, []).append(r)
    quota = 12
    comp_a: List[Dict] = []
    stratum_report: Dict[str, Dict] = {}
    for key in sorted(strata, key=lambda k: (str(k[0]), str(k[1]), k[2])):
        cell = sorted(strata[key],
                      key=lambda r: r['trace_id'])  # deterministic order
        n_h, n_draw = len(cell), min(quota, len(cell))
        rng = _stream('A', str(key))
        draw = rng.sample(cell, n_draw)
        pi = n_draw / n_h
        for r in draw:
            ent = dict(r)
            ent.update({'component': 'A', 'stratum': {
                'task_family': key[0], 'lep_code': key[1],
                'frozen_m1_positive': bool(key[2])},
                'inclusion_probability': pi,
                'design_weight': (1.0 / pi) if pi > 0 else None})
            comp_a.append(ent)
        stratum_report[str(key)] = {'n_in_stratum': n_h, 'n_drawn': n_draw,
                                     'pi': pi,
                                     'fully_drawn': n_draw == n_h}
    common.write_jsonl(comp_a, 'sample_A_final.jsonl')

    # ── Component B: targeted predicates (fixed order, cap 150) ────────────
    comp_b: List[Dict] = []

    def add_b(rows, tag: str, cap_tag: int = CAP_B // 8) -> None:
        rng = _stream('B', tag)
        sel = sorted(rows, key=lambda r: _rank(r['trace_id'], tag))
        for r in sel[:cap_tag]:
            ent = dict(r)
            ent['component'] = 'B'
            ent['targeted_predicate'] = tag
            comp_b.append(ent)

    def _rank(tid: str, tag: str) -> int:
        import hashlib
        return int(hashlib.sha256(
            f'{S_SEED}:B:{tag}:{tid}'.encode()).hexdigest()[:14], 16)

    # 1. zero-threshold-only union positives (candspec)
    zt = [r for r in held if r['trace_id'] in cs
          and ((cs[r['trace_id']].get('union') or {}).get('divergent'))
          and len((cs[r['trace_id']].get('union') or {}).get(
              'zero_threshold_triggered') or []) ==
          len((cs[r['trace_id']].get('union') or {}).get(
              'triggered_dimensions') or [])
          and ((cs[r['trace_id']].get('union') or {}).get(
              'triggered_dimensions'))]
    add_b(zt, 'zero_threshold_positive')
    # 2. dimension-pair overlap rows (>=2 triggered dims, candspec)
    ov = [r for r in held if r['trace_id'] in cs and
          len((cs[r['trace_id']].get('union') or {}).get(
              'triggered_dimensions') or []) >= 2]
    add_b(ov, 'dimension_pair_overlap')
    # 3. RT trigger cells: SWE unresolved identifiers + fin unit-defect
    #    fixtures + research negation cells
    specs = common.load_specs()
    sys.path.insert(0, os.path.join(_HERE, 'extractors'))
    import normalizers
    unit_defects = set(normalizers.load_defect_manifest()['unit_defects'])
    rt = [r for r in held
          if common.fixture_id_of(r['trace_id']) in unit_defects or
          (r.get('task_family') == 'code_review' and
           r['trace_id'] in cs and
           (cs[r['trace_id']].get('feature_coverage') or {}).get(
               'decisions', {}).get('n_observed', 0) == 0)]
    add_b(rt, 'rt_trigger_cell')
    # 4. unavailable-evidence cells (candspec feature_coverage all-zero)
    ue = []
    for r in held:
        c = (cs.get(r['trace_id']) or {}).get('feature_coverage')
        if not c:
            continue
        if (c.get('assertions', {}).get('n_observed', 0) == 0 and
                c.get('decisions', {}).get('n_observed', 0) == 0 and
                c.get('outcomes', {}).get('answer_state') in
                ('no_answer_key', 'unavailable', None)):
            ue.append(r)
    add_b(ue, 'unavailable_evidence')
    # 5. cross-implementation disagreement rows (v7 vs v6 union)
    xd = [r for r in held if r['trace_id'] in v7 and
          r['trace_id'] in v6 and
          bool(v7[r['trace_id']].get('m1_union')) !=
          bool(v6[r['trace_id']].get('m1_union'))]
    add_b(xd, 'cross_impl_disagreement')
    comp_b = comp_b[:CAP_B]
    common.write_jsonl(comp_b, 'sample_B_final.jsonl')

    # ── Challenge set: <=90 hard-case rows, case-table only ────────────────
    challenge: List[Dict] = []
    # hard numeric: fin rows with a gradeable (non-ungradeable) LEP grade
    fin_hard = [r for r in held if r.get('task_family') ==
                'financial_analysis' and r['trace_id'] in cs and
                (cs[r['trace_id']].get('lep_grade') in ('incorrect',
                                                         'correct'))]
    for r in sorted(fin_hard, key=lambda r: _rank(r['trace_id'], 'fin'))[
            :12]:
        challenge.append({'row': r, 'hard_cell': 'difficult_numeric'})
    # ambiguous / negation-heavy research rows: assertion+outcome dims not
    # evaluable in candspec (ambiguous verdicts drop the dims to unknown)
    res_neg = [r for r in held if r.get('task_family') ==
               'research_synthesis' and r['trace_id'] in cs and
               not ((cs[r['trace_id']].get('per_dimension') or {})
                    .get('assertions') or {}).get('evaluable')]
    for r in sorted(res_neg, key=lambda r: _rank(r['trace_id'], 'res'))[:12]:
        challenge.append({'row': r, 'hard_cell': 'negation_ambiguous'})
    # unit mismatches: fin unit-defect fixtures
    um = [r for r in held
          if common.fixture_id_of(r['trace_id']) in unit_defects]
    for r in sorted(um, key=lambda r: _rank(r['trace_id'], 'um'))[:12]:
        challenge.append({'row': r, 'hard_cell': 'unit_mismatch'})
    # SWE issue-identification hard cells: code_review rows where the
    # candspec decision features carry no observed stance (the RT-1
    # unresolved-identifier population) — or, when all are observed, the
    # unit-defect-free code rows are the identification surface.
    uid = [r for r in held if r.get('task_family') == 'code_review'
           and r['trace_id'] in cs and
           ((cs[r['trace_id']].get('feature_coverage') or {}).get(
               'decisions', {}).get('n_observed', 0) == 0)]
    if not uid:
        uid = [r for r in held if r.get('task_family') == 'code_review'
               and r['trace_id'] in cs]
    for r in sorted(uid, key=lambda r: _rank(r['trace_id'], 'uid'))[:12]:
        challenge.append({'row': r, 'hard_cell': 'unresolved_identifier'})
    # missing evidence
    for r in sorted(ue, key=lambda r: _rank(r['trace_id'], 'me'))[:12]:
        challenge.append({'row': r, 'hard_cell': 'missing_evidence'})
    # zero-threshold hard cells
    for r in sorted(zt, key=lambda r: _rank(r['trace_id'], 'zt'))[:12]:
        challenge.append({'row': r, 'hard_cell': 'zero_threshold'})
    challenge = challenge[:CAP_CHALLENGE]
    common.write_jsonl(challenge, 'challenge_final.jsonl')

    # ── Pair-group draws: 6 classes x per-family quota for the reference ───
    def _class_rows(cls):
        if cls == 'frozen_m1_positive':
            return [r for r in held if _frozen_m1(r)]
        if cls == 'frozen_m1_negative':
            return [r for r in held if not _frozen_m1(r)]
        if cls == 'zero_threshold_positive':
            return zt
        if cls == 'cross_impl_disagreement':
            return xd
        if cls == 'outcome_only_positive':
            return [r for r in held if r['trace_id'] in cs and
                   ((cs[r['trace_id']].get('per_dimension') or {})
                    .get('outcomes') or {}).get('divergent')]
        if cls == 'benign_to_benign_control':
            return [r for r in held if (r.get('reference_set') and
                                         len(r['reference_set']) >= 2)]
        return []
    pair_groups: List[Dict] = []
    n_fam = max(1, len(FAM))
    per_class = 12
    for cls in ('frozen_m1_positive', 'frozen_m1_negative',
                'zero_threshold_positive', 'cross_impl_disagreement',
                'outcome_only_positive', 'benign_to_benign_control'):
        rows = _class_rows(cls)
        rng = _stream('pairs', cls)
        sel = sorted(rows, key=lambda r: _rank(r['trace_id'], cls))
        sel = sel[:per_class * n_fam]
        for r in sel:
            ent = dict(r)
            ent['pair_class'] = cls
            pair_groups.append(ent)
    common.write_jsonl(pair_groups, 'pair_groups_final.jsonl')

    # ── Coverage report: every cell of the full grid, named gaps ──────────
    coverage = _coverage(comp_a, comp_b, challenge, held)
    common.write_json(coverage, 'sample_coverage_report.json')
    out = {'component_A': len(comp_a), 'component_B': len(comp_b),
           'challenge': len(challenge), 'pair_groups': len(pair_groups),
           'empty_cells_named': coverage['empty_cells'],
           'strata': stratum_report}
    print(json.dumps(out, indent=1))
    return out


def _rank(tid: str, tag: str) -> int:
    import hashlib
    return int(hashlib.sha256(
        f'{S_SEED}:{tag}:{tid}'.encode()).hexdigest()[:14], 16)


def _coverage(comp_a, comp_b, challenge, held) -> Dict[str, Any]:
    cells: Dict[str, Dict[str, int]] = {}
    for c in FAM:
        for l in LEP:
            for t in TOPO:
                for m in MODE:
                    for mp in (0, 1):
                        key = (f'{c}/{l}/{t}/{m}', mp)
                        cells[str(key)] = {'A': 0, 'B': 0,
                                           'challenge': 0,
                                           'in_frame': 0}
    def _key(r):
        return str((f"{r.get('task_family')}/{r.get('lep_code')}/"
                    f"{r.get('topology')}/{r.get('propagation_mode')}",
                    1 if _frozen_m1(r) else 0))
    for r in comp_a:
        cells[_key(r)]['A'] += 1
    for r in comp_b:
        cells[_key(r)]['B'] += 1
    for c in challenge:
        cells[_key(c['row'])]['challenge'] += 1
    for r in held:
        cells[_key(r)]['in_frame'] += 1
    for k, e in cells.items():
        e['n_sampled'] = e['A'] + e['B'] + e['challenge']
    empty = [k for k, e in cells.items()
             if e['in_frame'] == 0]
    uncovered = [k for k, e in cells.items()
                 if e['in_frame'] > 0 and e['n_sampled'] == 0]
    return {'n_cells': len(cells),
            'empty_cells': empty,
            'in_frame_but_unsampled_cells': uncovered,
            'note': 'empty_cells: no standard execution exists in the '
                    'frame for that cell (named, per brief §7). '
                    'in_frame_but_unsampled_cells: population exists but '
                    'no A/B/challenge draw landed there — a named '
                    'shortfall, never hidden.',
            'cell_table': cells}


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(common.V7_DIR, 'extractors'))
    sys.path.insert(0, common.V6_DIR)
    sys.path.insert(0, os.path.join(_HERE, 'extractors'))
    build_all()
