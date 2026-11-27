#!/usr/bin/env python3.11
"""Build the independent reference labels from the judge cache.

Layered (prior-protocol discipline):
  * L1 AI-AGREED:   judge_a and judge_b give the SAME union verdict for
                    the LEP-labeled execution (and per-dimension
                    agreement on every dimension neither left
                    'unresolved');
  * L2 ADJUDICATED: the third-model adjudicator resolved the
                    disagreement (verdict recorded; 'agrees_with' kept
                    for provenance);
  * L3 UNRESOLVED:  adjudicator could not resolve, or both judges
                    said 'unresolved' -> the execution is EXCLUDED from
                    criterion scoring (reported as unresolved rate,
                    NEVER a negative label).
  * JUDGE-INVALID:  both judges' JSON unusable -> EXCLUDED.

Every label carries provenance (which layer, which calls, both raw
verdicts).  H0 packets (B4) produce the judge's OWN null-behavior
counts: any 'diverges' on a benign↔benign execution is a judge-level
false positive (BENIGN_CONTROL_VALIDATION).

Also: the deterministic reference cross-checks (prereg §2):
  (a) canonical-identifier validation of judge values;
  (b) a standalone action-skeleton diff re-implemented HERE (no cv2 /
      v6 distance code) — tool-name/role/target multisets, Jaccard on
      the two executions' skeletons;
  (c) benign↔benign final-response identity probe.
Deterministic; reads only the cache + packet files.
"""
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
STUDY = os.path.abspath(os.path.join(HERE, '..'))
V2 = os.path.abspath(os.path.join(STUDY, '..', 'calibration_v2'))
PK = os.path.join(STUDY, 'out', 'packets')
JC = os.path.join(STUDY, 'out', 'judge_cache')
LED = os.path.join(STUDY, 'ledger')
DIMS = ('assertions', 'decisions', 'actions', 'outcomes')
UNRES = 'unresolved'


def _load(judge, pid, task='annotate'):
    p = os.path.join(JC, judge,
                    f'{pid}__{task}__cv2val-ann-v3.json')
    if not os.path.exists(p):
        return None
    return json.load(open(p))


def _union_of(judgment, label):
    pe = (judgment or {}).get('per_execution') or {}
    return ((pe.get(label) or {}).get('union'))


def _dims_of(judgment, label):
    pe = (judgment or {}).get('per_execution') or {}
    return ((pe.get(label) or {}).get('per_dimension') or {})


def _actions_skeleton_diff(pa, pb):
    """(b) standalone re-implementation: multiset Jaccard on
    (role, tool, target) keys — NO distance code imported."""
    def skel(ex):
        out = []
        for x in (ex.get('tool_skeleton') or []):
            s = str(x)
            head, _, tail = s.partition(':')
            out.append((head.strip(), tail.strip()))
        return Counter(out)
    ca, cb = skel(pa), skel(pb)
    inter = sum((ca & cb).values())
    union = sum((ca | cb).values())
    return 1.0 - (inter / union if union else 0.0)


def main():
    pids = [f[:-5] for f in os.listdir(PK)
            if f.endswith('.json') and not f.startswith('_')]
    refs = []
    for pid in sorted(pids):
        p = json.load(open(os.path.join(PK, pid + '.json')))
        if pid.startswith('P__'):
            continue
        lep = p.get('lep_execution_label')
        h0 = p.get('h0')
        ra = _load('judge_a', pid)
        rb = _load('judge_b', pid)
        adj = _load('adjudicator', pid, 'adjudicate')
        base = {
            'pack_id': pid, 'cohort': p.get('cohort'),
            'lep_trace_id': p.get('lep_trace_id_admin'),
            'lep_label': lep, 'h0': h0,
            'task_family': p.get('task_family_admin'),
        }
        va = bool(ra and ra.get('ok') and ra.get('valid_json'))
        vb = bool(rb and rb.get('ok') and rb.get('valid_json'))
        is_b = p.get('cohort') in ('B1_targeted', 'B2_targeted',
                                   'B3_targeted', 'B4_h0')
        single = False
        single_basis = 'both'
        if va and vb:
            ja, jb = ra['judgment'], rb['judgment']
        elif is_b and va:
            ja = jb = ra['judgment']
            single, single_basis = True, 'judge_a'
        elif is_b and vb:
            ja = jb = rb['judgment']
            single, single_basis = True, 'judge_b'
        else:
            refs.append({**base, 'provenance': 'judge_invalid',
                         'label': None, 'per_dim': None,
                         'label_basis': None,
                         'a_valid': va, 'b_valid': vb})
            continue
        # LEP-side union + per-dimension verdicts
        unions = {}
        dims = {}
        if lep:
            ua, ub = _union_of(ja, lep), _union_of(jb, lep)
            da, db = _dims_of(ja, lep), _dims_of(jb, lep)
            unions = {'a': ua, 'b': ub}
            dims = {'a': da, 'b': db}
        # per-execution verdicts for H0 null behavior (all four)
        allv = {}
        for ex in ('EXEC_1', 'EXEC_2', 'EXEC_3', 'EXEC_4'):
            allv[ex] = {'a': _union_of(ja, ex), 'b': _union_of(jb, ex)}
        case = f'{pid}::{lep}' if lep else None
        adj_ent = None
        if adj and adj.get('ok') and adj.get('valid_json') and case:
            adj_ent = ((adj.get('judgment') or {})
                       .get('resolutions') or {}).get(case)
        if single:
            # addendum A1: disclosed single-judge reference for cohort B
            # (diagnostic-only; never a headline-estimate input).
            label = unions.get('a')
            prov = 'single_judge'
            ref_label = None if label in (None, UNRES) else label
            pd = {}
            for d in DIMS:
                dv = (dims.get('a') or {}).get(d)
                pd[d] = (True if dv == 'diverges'
                         else (False if dv == 'ordinary'
                               else (UNRES if dv == UNRES else None)))
            union_dis = None
        else:
            agreed = (unions.get('a') == unions.get('b')
                      and None not in (unions.get('a'), unions.get('b')))
            if agreed:
                label = unions['a']
                prov = 'ai_agreed'
                pd = {}
                for d in DIMS:
                    da_ = dims['a'].get(d)
                    db_ = dims['b'].get(d)
                    if da_ is None or db_ is None:
                        pd[d] = None
                    elif da_ == db_:
                        pd[d] = (True if da_ == 'diverges'
                                 else (False if da_ == 'ordinary'
                                       else UNRES))
                    else:
                        pd[d] = 'disagreement'
                ref_label = None if label == UNRES else label
                union_dis = False
            else:
                prov, ref_label, union_dis = None, None, True
                pd = {d: 'disagreement' for d in DIMS}
                if adj_ent:
                    ref_label = adj_ent.get('verdict')
                    if ref_label == UNRES:
                        ref_label = None
                        prov = 'ai_unresolved'
                    else:
                        prov = 'ai_adjudicated'
                    dd = {d for d in DIMS
                          if dims['a'].get(d) != dims['b'].get(d)}
                    pd = {d: (UNRES if d in dd
                              else (True if dims['a'].get(d)
                                    == 'diverges' else
                                    (False if dims['a'].get(d)
                                     == 'ordinary' else None)))
                         for d in DIMS}
                else:
                    prov = 'ai_unresolved'
        # deterministic cross-checks (sanity, never replace the label)
        if lep and not h0:
            label_tid = p['exec_labels_to_tids'][lep]
            other = [t for t in p['exec_labels_to_tids'].values()
                     if t != label_tid]
            pa = p['surface']['executions'].get(lep) or {}
            diffs = []
            for t in other:
                lab = [k for k, v in p['exec_labels_to_tids'].items()
                       if v == t][0]
                diffs.append(_actions_skeleton_diff(
                    pa, p['surface']['executions'].get(lab) or {}))
            base['det_actions_skeleton'] = {
                'lep_vs_benigns': diffs,
                'max_diff': max(diffs) if diffs else None}
        refs.append({**base, 'provenance': prov, 'label': ref_label,
                     'per_dim': pd, 'label_basis': single_basis
                     if single else ('both' if prov in
                                     ('ai_agreed', 'ai_adjudicated')
                                     else 'unresolved'),
                     'all_exec_unions': allv,
                     'adjudicator_agrees_with': (
                         adj_ent.get('agrees_with')
                         if adj_ent else None),
                     'adjudicator_cause_class': (
                         adj_ent.get('cause_class')
                         if adj_ent else None),
                     'adjudicator_support': (
                         adj_ent.get('support') if adj_ent else None),
                     'union_disagreement': union_dis})
    json.dump(refs, open(os.path.join(LED, 'reference_labels.jsonl'),
                         'w'), indent=1)
    c = Counter(r['provenance'] for r in refs)
    lab = Counter(str(r['label']) for r in refs)
    h0pos = sum(1 for r in refs if r.get('h0') and any(
        (v.get('a') == 'diverges' or v.get('b') == 'diverges')
        for v in (r.get('all_exec_unions') or {}).values()))
    print('reference rows:', len(refs))
    print('provenance:', dict(c))
    print('label:', dict(lab))
    print('H0 packets with a judge-level diverges (null-FP):', h0pos)


if __name__ == '__main__':
    main()
