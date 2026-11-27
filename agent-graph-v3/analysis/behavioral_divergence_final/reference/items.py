#!/usr/bin/env python3.11
"""
Item manifest builder (CONTRACT 'Item schema').

Emits the judge-item streams the reference build consumes:
  * extraction items — one per selected LEP row (dev-side by default);
  * pair items — LEP-vs-benign comparison groups across 6 classes.

Blinding (FROZEN_PROTOCOL §3): the packet surface is task_prompt + blinded
execution excerpts ONLY.  No GT value, no expected answer, no fixture
implementation label, no condition.  A forbidden-token scan must be clean
before items are written (a hit aborts the build).

Dev-side restriction: by default only DEV-partition rows (plus the inspected
fixtures that are dev-side by construction, see partitioning.py) are emitted.
Heldout rows are emitted only under an explicit --partition heldout flag,
which the orchestrator runs ONLY after the seal + fingerprints are written.
"""
from __future__ import annotations

import json
import os
import re as _re
import sys
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
FINAL = os.path.abspath(os.path.join(_HERE, '..'))
sys.path.insert(0, FINAL)
import common
sys.path.insert(0, os.path.join(FINAL, 'extractors'))
sys.path.insert(0, os.path.join(FINAL, 'reference'))
import det_reference as DR
import normalizers

EXCERPT_CHARS = 1500


# ── blinded excerpt (agent-authored surface only; mirrors the extractor view) ─
def _trace_index(tids: List[str]) -> Dict[str, Optional[Dict]]:
    idx = {}
    for line in open(os.path.join(common.VALIDATION_CACHE,
                                  'trace_index.jsonl')):
        d = json.loads(line)
        if d['trace_id'] in set(tids):
            idx[d['trace_id']] = d['path']
    out: Dict[str, Optional[Dict]] = {}
    for tid, p in idx.items():
        out[tid] = json.load(open(p)) if os.path.exists(p) else None
    return out


# v7 packet_builder scrub discipline: structural identifiers + cell-naming
# tokens are redacted from excerpt text so the surface is MORE blinded than
# the raw trace, never less.
_SCRUB_RE = _re.compile(
    r'(?i)\b(single_origin|many_to_one|one_to_many|review_loop|'
    r'branch_and_verify|coordinator_workers|code_review|'
    r'financial_analysis|research_synthesis|finq\w*|scifact\w*|'
    r'swe_\w+|LEP_[A-Z_]+)\b')
_STRUCT_IDS = ('trace_id', 'execution_id', 'fixture_id', 'attempt_id',
               'baseline_deviation', 'bnd_v4', 'measurement_v6',
               'measurement_v7', 'per_trace', 'ground_truth',
               'expected_answer', 'answer_calculation', 'specroot',
               'scenario_id', 'workspace_fixtures', 'consolidated_')


def _scrub(text: Optional[str]) -> Optional[str]:
    if not text:
        return text
    for tok in _STRUCT_IDS:
        text = text.replace(tok, '')
    text = _SCRUB_RE.sub('[cell-redacted]', text)
    return text


def _excerpt(trace: Optional[Dict], n: int = EXCERPT_CHARS) -> Dict[str, Any]:
    """A blinded, GT-free excerpt.  KEYING NOTE: the excerpt dict is what
    reaches the prompt; it is keyed by a blinded execution label, never by
    trace_id.  trace_id stays in the item's administrative `trace_ids`
    field, which is NOT part of the prompt surface and is excluded from the
    blinding scan (join-back only)."""
    if not trace:
        return {'final_response': None, 'last_intermediate': None,
                'tool_skeleton': [], 'note': 'trace_unavailable'}
    final = None
    last_mid = None
    skeleton: List[str] = []
    for e in trace.get('events') or []:
        t = e.get('output_text') or ''
        if e.get('event_type') == 'final_response' and t:
            final = t
        elif e.get('event_type') in ('reasoning', 'agent_handoff') and t:
            last_mid = f"[{e.get('agent_role')}] {t}"
        elif e.get('event_type') == 'tool_call':
            a = e.get('tool_arguments') or {}
            tgt = a.get('path') or a.get('target_agent') or ''
            skeleton.append(f"{e.get('agent_role')}: {e.get('tool_name')}"
                            + (f"({tgt})" if tgt else ''))
    return {
        'final_response': _scrub((final or '')[:n]) or None,
        'last_intermediate': _scrub((last_mid or '')[:n]) or None,
        'tool_skeleton': skeleton[-40:],
        'n_events': len(trace.get('events') or []),
    }


# ── canonical tables (task-side; shared by judges + deterministic reference) ─
def _canonical_tables(fam: str, spec: Dict[str, Any]
                      ) -> Dict[str, Any]:
    out: Dict[str, Any] = {'verdict_enum': list(DR.VERDICT_ENUM)}
    if fam == 'code_review':
        out['issue_alias_map'] = normalizers.swe_issue_alias_table(
            spec.get('required_issues'))
    if fam == 'financial_analysis':
        rf = spec.get('required_facts') or {}
        out['fin_unit_factors'] = {k: normalizers.declared_unit_factor(
            (v or {}).get('unit')) for k, v in rf.items()
            if isinstance(v, dict)}
        out['fin_unit_defect'] = normalizers.load_defect_manifest(
        )['unit_defects'].get(spec.get('fixture_id'))
    return out


# ── blinding scan (v7 discipline: 0 hits or abort) ─────────────────────────
_FORBIDDEN_TEST = ('LEP_', 'HANDOFF_CORRUPTION', 'INDIRECT_PROMPT',
                   'INPUT_DISREGARD', 'TOOL_RESULT_CORRUPTION',
                   'MEMORY_POISONING', 'single_origin', 'many_to_one',
                   'one_to_many', 'review_loop', 'branch_and_verify',
                   'coordinator_workers', 'finq', 'scifact', 'swe_',
                   'ground_truth', 'expected_answer', 'specroot',
                   'per_trace', 'baseline_deviation')


def _gt_tokens(spec: Optional[Dict]) -> List[str]:
    """GT-ONLY literals (the answer entry + verdict truths).  Source-figure
    values are task content and are NOT forbidden — the agents read them."""
    out: List[str] = []
    rf = (spec or {}).get('required_facts') or {}
    ents = rf.items() if isinstance(rf, dict) else enumerate(rf)
    for k, ent in ents:
        if isinstance(ent, dict) and (str(k).lower() == 'answer' or
                                       'truth' in str(k).lower()):
            for fld in ('value', 'tolerance', 'value_strings', 'truth'):
                v = ent.get(fld)
                if isinstance(v, (list, tuple)):
                    out.extend(str(x) for x in v)
                elif isinstance(v, (str, int, float)):
                    out.append(str(v))
    for fld in ('expected', 'gold'):
        v = (spec or {}).get(fld)
        if isinstance(v, (str, int, float)):
            out.append(str(v))
    return sorted({t for t in out if len(str(t)) >= 3})


def scan(items: List[Dict], specs: Dict[str, Dict]) -> Dict[str, Any]:
    """Blinding scan, scoped to the fields that must be GT/condition-clean.

    The judge-facing surface is task_prompt + evidence + construct +
    canonical_tables.  Two distinct checks:

      * structural/cell tokens (_FORBIDDEN_TEST) must not appear ANYWHERE in
        the surface — they would disclose the condition (LEP/benign, topology,
        LEP code).  The agent-authored evidence is scrubbed so these cannot
        survive.
      * GT VALUE strings must not appear in the task_prompt / construct /
        canonical_tables (the labels I inject) — those are the answer-key /
        lookup vectors the brief forbids.  The agent-authored evidence is
        EXEMPT: the numbers the agent itself printed are the measurement
        target ("extract from the agent's text, never select from a gold
        list" — v6 §H).  A correct agent extraction legitimately contains the
        GT value; that is evidence, not a leak.  GT containment of the
        deterministic measurement is enforced separately at the extractor
        boundary (strip_gt_values / assert_gt_free).
    """
    hits: List[Dict] = []
    n_gt_tokens = 0
    for it in items:
        toks = set(_gt_tokens(specs.get(it.get('fixture_id')) or {}))
        n_gt_tokens += len(toks)
        # (a) structural/cell tokens — anywhere in the surface
        surface = (json.dumps(it.get('evidence') or {}) +
                   json.dumps(it.get('task_prompt') or '') +
                   json.dumps(it.get('construct') or {}) +
                   json.dumps(it.get('canonical_tables') or {})).lower()
        for t in _FORBIDDEN_TEST:
            if t.lower() in surface:
                hits.append({'item': it['item_id'],
                             'kind': 'structural_token', 'token': t})
        # (b) GT value strings — only in the injected labels, never evidence
        label_blob = (json.dumps(it.get('task_prompt') or '') +
                      json.dumps(it.get('construct') or {}) +
                      json.dumps(it.get('canonical_tables') or {})).lower()
        for t in toks:
            if len(t) >= 3 and t.lower() in label_blob:
                hits.append({'item': it['item_id'], 'kind': 'gt_in_label',
                             'token': t})
    return {'n_items': len(items), 'n_gt_tokens_scoped': n_gt_tokens,
            'n_hits': len(hits), 'hits': hits[:20],
            'scan_scope': ('structural tokens: whole judge-facing surface; '
                           'GT value strings: task_prompt + construct + '
                           'canonical_tables (agent-authored evidence '
                           'exempt — it is the measurement target)'),
            'verdict': 'clean' if not hits else 'ABORT'}


# ── item build ──────────────────────────────────────────────────────────────
def _rank(seed: str, s: str) -> int:
    import hashlib
    return int(hashlib.sha256(f'{seed}:{s}'.encode()).hexdigest()[:14],
               16)


def build(partition: str = 'dev', per_family: int = 40,
          pair_per_family: int = 18) -> Dict[str, Any]:
    S = 'agentprop-final-sampling-20261207'
    from candspec_pipeline import load_frame_rows
    frame = load_frame_rows()
    specs = common.load_specs()
    parts = json.load(open(os.path.join(common.OUT,
                                         'partitions_final.json')))
    heldout_fx = {fx for fx, rec in parts['fixtures'].items()
                  if rec.get('partition') == 'heldout'}

    def side(r: Dict) -> str:
        return ('heldout' if common.fixture_id_of(r['trace_id']) in heldout_fx
                else 'dev')

    rows = [r for r in frame if side(r) == partition]
    fams = sorted({r.get('task_family') for r in rows})

    # HELDOUT (pre-registered surface): the evaluation items are keyed to
    # the pre-registered sampling outputs (component A + pair groups), so
    # the reference surface is exactly the frozen design — not a fresh
    # ad-hoc selection at consumption time.
    global _HELDOUT_EXTRACT_ROWS, _HELDOUT_PAIR_ROWS
    if partition == 'heldout' and os.path.exists(
            os.path.join(common.OUT, 'sample_A_final.jsonl')):
        _HELDOUT_EXTRACT_ROWS = [json.loads(l) for l in open(
            os.path.join(common.OUT, 'sample_A_final.jsonl')) if l.strip()]
        _HELDOUT_PAIR_ROWS = [json.loads(l) for l in open(
            os.path.join(common.OUT, 'pair_groups_final.jsonl')) if l.strip()]
        _HELDOUT_EXTRACT_ROWS = [r for r in _HELDOUT_EXTRACT_ROWS
                                 if side(r) == 'heldout']
        _HELDOUT_PAIR_ROWS = [r for r in _HELDOUT_PAIR_ROWS
                              if side(r) == 'heldout']
    else:
        _HELDOUT_EXTRACT_ROWS = []
        _HELDOUT_PAIR_ROWS = []
    traces = _trace_index([r['trace_id'] for r in rows] + [
        ref for r in rows for ref in (r.get('reference_set') or [])])

    items: List[Dict] = []

    # extraction items: stratified by (family, frozen m1 class)
    def frozen_m1(r: Dict[str, Any]) -> bool:
        v = r.get('m1_whole_trace_positive')
        return bool(v if v is not None else r.get('m1_historical'))

    # heldout: the pre-registered component-A rows (sampling.py) ARE the
    # extraction surface — no fresh draw at consumption time
    for fam in fams:
        if partition == 'heldout' and _HELDOUT_EXTRACT_ROWS:
            chosen = [r for r in _HELDOUT_EXTRACT_ROWS
                      if r.get('task_family') == fam]
        else:
            frows = [r for r in rows if r.get('task_family') == fam]
            pos = sorted([r for r in frows if frozen_m1(r)],
                         key=lambda r: _rank(S + fam + 'P', r['trace_id']))
            neg = sorted([r for r in frows if not frozen_m1(r)],
                         key=lambda r: _rank(S + fam + 'N', r['trace_id']))
            half = per_family // 2
            chosen = (pos[:half] + neg[:per_family - half])[:per_family]
        for r in chosen:
            tid = r['trace_id']
            spec = specs.get(common.fixture_id_of(tid)) or {}
            fam_ = r.get('task_family')
            exc = _excerpt(traces.get(tid))
            det: Dict[str, Any] = {}
            if fam_ == 'financial_analysis':
                refs = [traces.get(x) for x in (r.get('reference_set') or [])
                         if traces.get(x)]
                det['finans'] = DR.det_finans(
                    exc.get('final_response') or '',
                    [_fr_final(t) for t in refs], spec, fam_)
            elif fam_ == 'research_synthesis':
                facts = spec.get('required_facts') or {}
                claims = [k for k in facts if 'claim' in k.lower()] \
                    or list(facts)
                refs = [traces.get(x) for x in (r.get('reference_set') or [])
                         if traces.get(x)]
                det['verdicts'] = DR.det_verdicts(
                    exc.get('final_response') or '',
                    [_fr_final(t) for t in refs], claims)
            elif fam_ == 'code_review':
                refs = [traces.get(x) for x in (r.get('reference_set') or [])
                         if traces.get(x)]
                det['swe'] = DR.det_swe(
                    exc.get('final_response') or '',
                    [_fr_final(t) for t in refs],
                    spec.get('required_issues'))
            # actions det vs first benign
            if refs := [traces.get(x) for x in (r.get('reference_set') or [])
                        if traces.get(x)]:
                det['actions'] = DR.det_actions(traces.get(tid), refs[0])
            it = {
                'item_id': f'{fam_}__ext__{tid}',
                'fixture_id': common.fixture_id_of(tid),
                'task_family': fam_,
                'partition': partition,
                'kind': 'extraction',
                'scope': 'extraction',
                'trace_ids': {'lep': tid,
                              'benign_refs': r.get('reference_set') or []},
                'task_prompt': _scrub(
                    (spec.get('task_prompt') or '')[:2000]),
                'evidence': {'execution': exc},
                'construct': _extract_construct(fam_),
                'canonical_tables': _canonical_tables(fam_, spec),
                'expected': None,
                'det_reference': det,
            }
            items.append(it)

    # pair items: heldout uses the pre-registered pair groups (sampling.py)
    pair_classes = _pair_classes(rows, traces, specs, S, heldout_fx)
    for fam in fams:
        if partition == 'heldout' and _HELDOUT_PAIR_ROWS:
            for r in [x for x in _HELDOUT_PAIR_ROWS
                      if x.get('task_family') == fam]:
                cls = r.get('pair_class') or 'frozen_m1_positive'
                items.append(_pair_item(r, cls, traces, specs, S,
                                        partition))
            continue
        per_class = max(1, pair_per_family // 6)
        for cls in ('frozen_m1_positive', 'frozen_m1_negative',
                    'zero_threshold_positive', 'cross_impl_disagreement',
                    'outcome_only_positive', 'benign_to_benign_control'):
            for r in pair_classes.get((fam, cls), [])[:per_class]:
                items.append(_pair_item(r, cls, traces, specs, S,
                                        partition))

    # blinding scan — abort on any hit
    scan_res = scan(items, specs)
    if scan_res['n_hits']:
        raise SystemExit('ITEMS NOT WRITTEN — blinding broken: '
                         + json.dumps(scan_res['hits'][:10]))

    outp = common.write_jsonl(items, f'items_{partition}.jsonl')
    report = {'partition': partition, 'n_items': len(items),
              'n_extraction': sum(1 for i in items if i['kind'] == 'extraction'),
              'n_pairs': sum(1 for i in items if i['kind'] == 'pair'),
              'pair_classes': {
                  cls: len(pair_classes.get((fam, cls), []))
                  for fam in fams for cls in
                  ('frozen_m1_positive', 'frozen_m1_negative',
                   'zero_threshold_positive', 'cross_impl_disagreement',
                   'outcome_only_positive', 'benign_to_benign_control')},
              'blinding_scan': scan_res}
    common.write_json(report, f'items_{partition}_report.json')
    print(json.dumps({k: report[k] for k in
                      ('partition', 'n_items', 'n_extraction', 'n_pairs')},
                     indent=1))
    print('blinding scan:', scan_res['verdict'])
    return report


def _fr_final(trace: Optional[Dict]) -> str:
    ft = ''
    for e in (trace or {}).get('events') or []:
        if e.get('event_type') == 'final_response' and e.get('output_text'):
            ft = e['output_text']
    return ft


def _extract_construct(fam: str) -> Dict[str, Any]:
    from candspec_features import JUDGE_CONSTRUCT
    return {d: {k: JUDGE_CONSTRUCT[d][k] for k in
                ('scope', 'states') if k in JUDGE_CONSTRUCT[d]}
            for d in ('assertions', 'decisions', 'actions', 'outcomes')}


def _pair_classes(rows, traces, specs, S, heldout_fx
                  ) -> Dict[tuple, List[Dict]]:
    """Build the 6 pair classes per family, deterministic.  A class that
    cannot be built (missing ledger field) is recorded empty + flagged."""
    out: Dict[tuple, List[Dict]] = {}
    # candspec / v7 / v6 ledgers for zero-threshold + cross-impl cells
    def _ledger(p):
        if not os.path.exists(p):
            return {}
        return {r['trace_id']: r for r in
                (json.loads(l) for l in open(p) if l.strip())}
    cs = _ledger(os.path.join(common.OUT, 'per_trace_candspec.jsonl'))
    v7 = _ledger(os.path.join(common.V7_DIR, 'out', 'per_trace_v7.jsonl'))
    v6 = _ledger(os.path.join(common.V6_DIR, 'out', 'per_trace_v6.jsonl'))

    def _zero_thr(row) -> bool:
        tid = row['trace_id']
        if tid not in cs:
            return False
        u = cs[tid].get('union') or {}
        trig = u.get('triggered_dimensions') or []
        zt = u.get('zero_threshold_triggered') or []
        return bool(u.get('divergent')) and bool(trig) and \
            len(zt) == len(trig)

    def _cross_impl(row) -> bool:
        tid = row['trace_id']
        return (tid in v7 and tid in v6 and
                bool(v7[tid].get('m1_union')) !=
                bool(v6[tid].get('m1_union')))

    def _outcome_only(row) -> bool:
        tid = row['trace_id']
        if tid not in cs:
            return False
        pd = cs[tid].get('per_dimension') or {}
        out_div = bool((pd.get('outcomes') or {}).get('divergent'))
        others_div = any(((pd.get(d) or {}).get('divergent'))
                         for d in ('assertions', 'decisions', 'actions'))
        return out_div and not others_div

    def _frozen_m1(row) -> bool:
        v = row.get('m1_whole_trace_positive')
        return bool(v if v is not None else row.get('m1_historical'))

    for fam in sorted({r.get('task_family') for r in rows}):
        frows = [r for r in rows if r.get('task_family') == fam]
        for cls, pred in (
            ('frozen_m1_positive', _frozen_m1),
            ('frozen_m1_negative', lambda r: not _frozen_m1(r)),
            ('zero_threshold_positive', _zero_thr),
            ('cross_impl_disagreement', _cross_impl),
            ('outcome_only_positive', _outcome_only),
            ('benign_to_benign_control',
             lambda r: bool(r.get('reference_set') and
                            len(r['reference_set']) >= 2)),
        ):
            sel = [r for r in frows if _safe(pred, r)]
            sel = sorted(sel, key=lambda r: _rank(S + fam + cls,
                                                  r['trace_id']))
            out[(fam, cls)] = sel

    return out


def _safe(pred, r) -> bool:
    try:
        return pred(r)
    except Exception:
        return False


def _pair_item(r: Dict, cls: str, traces, specs, S,
               partition: str) -> Dict:
    fam = r.get('task_family')
    tid = r['trace_id']
    refs = r.get('reference_set') or []
    a, b = tid, (refs[0] if refs else tid)
    spec = specs.get(common.fixture_id_of(tid)) or {}
    side_a = _excerpt(traces.get(a))
    side_b = _excerpt(traces.get(b))
    # side-label randomization (judge does not know which is LEP):
    flip = (_rank(S + 'flip' + tid, cls) % 2) == 0
    det: Dict[str, Any] = {}
    if a in traces and b in traces and traces.get(a) and traces.get(b):
        det['actions'] = DR.det_actions(traces[a], traces[b])
        if fam == 'financial_analysis':
            det['finans'] = DR.det_finans(
                _fr_final(traces[a]), [_fr_final(traces[b])], spec, fam)
        elif fam == 'research_synthesis':
            facts = spec.get('required_facts') or {}
            claims = [k for k in facts if 'claim' in k.lower()] or list(facts)
            det['verdicts'] = DR.det_verdicts(
                _fr_final(traces[a]), [_fr_final(traces[b])], claims)
        elif fam == 'code_review':
            det['swe'] = DR.det_swe(
                _fr_final(traces[a]), [_fr_final(traces[b])],
                spec.get('required_issues'))
    ex_ids = {'A': (b if flip else a), 'B': (a if flip else b)}
    return {
        'item_id': f'{fam}__pair__{cls}__{tid}',
        'fixture_id': common.fixture_id_of(tid),
        'task_family': fam,
        'partition': partition,
        'kind': 'pair',
        'scope': 'pair',
        'pair_class': cls,
        'trace_ids': {'lep': tid, 'benign_refs': refs},
        'side_map': ex_ids,   # which physical trace is side A/B (outside the
        # packet text the judge sees; recorded for alignment only)
        'task_prompt': _scrub((spec.get('task_prompt') or '')[:2000]),
        'evidence': {'A': side_b if flip else side_a,
                     'B': side_a if flip else side_b},
        'construct': _extract_construct(fam),
        'canonical_tables': _canonical_tables(fam, spec),
        'expected': None,
        'det_reference': det,
    }


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--partition', default='dev', choices=['dev', 'heldout'])
    ap.add_argument('--per-family', type=int, default=40)
    ap.add_argument('--pair-per-family', type=int, default=18)
    a = ap.parse_args()
    build(a.partition, a.per_family, a.pair_per_family)
