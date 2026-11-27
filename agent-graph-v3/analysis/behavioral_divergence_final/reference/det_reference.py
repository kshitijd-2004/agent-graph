"""
INDEPENDENT deterministic reference layer (brief §4, MEASUREMENT_SPEC §7).

This module is FRESH code over raw trace events — it does NOT import the
v6/v7/candspec extractor or distance modules.  It re-implements the declared
construct (spec §1.4–1.5 kernels) and the task-side canonical tables
(normalizers: RT-1 alias table, RT-2 unit factors, RT-3 verdict enum — shared
by judges and reference alike), so it can decide where deterministic
verification is sufficient and defer the rest to the AI judges.

Layers (priority per spec §7):
  det_actions    fresh tool-call counter + fresh TV + count-diff + retry
                 multiplicity + ordering first-difference
  det_finans     independent final-answer parser + RT-2 unit scaling
  det_verdicts   canonical phrase inventory (research verdicts)
  det_swe        per-issue stance detection keyed by RT-1 positional ids
Each emits a verdict in {diverges, agrees, undetermined} (pair layers) with
its basis string 'det:<layer>', or None when it cannot decide (judge needed).
The reference layer MAY read the full spec (GT allowed); only extraction is
GT-bounded.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# task-side canonical tables (shared construct; NOT extractor code)
import sys, os
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, '..', 'extractors')))
import normalizers                                  # noqa: E402
sys.path.insert(0, _HERE)

# RT-3 canonical verdict enum
VERDICT_ENUM = ('SUPPORTED', 'CONTRADICTED', 'NOT_ENOUGH')
_VERDICT_PHRASES: Dict[str, re.Pattern] = {
    'SUPPORTED': re.compile(r'(?i)\b(?:is\s+)?supported\b|\bsupports?\b|'
                           r'corroborat\w*|\bconfirm\w*\b|\bvalid\b|\btrue\b'),
    'CONTRADICTED': re.compile(
        r'(?i)\bcontradict\w*|\brefut\w*|\bfalsif\w*|\binconsist\w*|'
        r'\bdisprov\w*|\bnot\s+(?:supported|true)\b|\bnegated\b'),
    'NOT_ENOUGH': re.compile(r'(?i)\bnot\s+enough\b|\binsufficient\b|'
                             r'\binconclusive\b|\bNEI\b|\bunclear\b|'
                             r'\bundetermined\b|\bcannot\s+(?:be\s+)?'
                             r'(?:determined|confirmed|established)\b'),
}
_NEG = re.compile(r'(?i)\b(?:not|no|never|cannot|can\'t|fails?\s+to)\b')


# ── trace view: raw tool_call events (INDEPENDENT of the extractor counter) ──
def _tool_calls(trace: Dict[str, Any]
                ) -> List[Tuple[str, str, str]]:
    """(role, tool, target) for each tool_call event, in recorded order.
    Target resolution mirrors spec §1.4 so the construct matches, but the
    code is written fresh here (no import of features_v6.action_counter)."""
    out: List[Tuple[str, str, str]] = []
    for e in trace.get('events') or []:
        if e.get('event_type') != 'tool_call':
            continue
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
                elif isinstance(sp, str):
                    target = sp
                elif not target:
                    s = args.get('summary')
                    if isinstance(s, str) and s:
                        target = '<summary:%d>' % len(s)
        out.append((e.get('agent_role') or '',
                    e.get('tool_name') or '', target))
    return out


def _counter(calls: List[Tuple[str, str, str]]) -> Dict[Tuple[str, str, str], int]:
    c: Dict[Tuple[str, str, str], int] = {}
    for k in calls:
        c[k] = c.get(k, 0) + 1
    return c


def _fresh_tv(a: Dict[Tuple, int], b: Dict[Tuple, int]) -> Optional[float]:
    """Total variation, re-implemented (bounded [0,1], symmetric)."""
    sa, sb = sum(a.values()), sum(b.values())
    if sa <= 0 and sb <= 0:
        return None
    if sa <= 0 or sb <= 0:
        return 1.0
    keys = set(a) | set(b)
    return 0.5 * sum(abs(a.get(k, 0) / sa - b.get(k, 0) / sb) for k in keys)


def det_actions(trace_a: Dict, trace_b: Dict
                ) -> Dict[str, Any]:
    """Independent action verification against raw tool-call events.

    verdict: 'diverges' (TV > 0), 'agrees' (TV == 0, both used tools),
    'undetermined' (neither used any tool).  count_diff / retry / ordering
    are reported but are NOT the classifier (spec §1.4 RT-5)."""
    ca, cb = _tool_calls(trace_a), _tool_calls(trace_b)
    ma, mb = _counter(ca), _counter(cb)
    tv = _fresh_tv(ma, mb)
    if tv is None:
        verdict = 'undetermined'
    elif tv == 0.0:
        verdict = 'agrees'
    else:
        verdict = 'diverges'
    # ordering first-difference (the two traces' tool-sequence order)
    if ca and cb and (ca != cb):
        first_diff = next(i for i, (x, y) in enumerate(zip(ca, cb))
                           if x != y)
    else:
        first_diff = 0
    retry_a = sum(1 for k, v in ma.items() if v > 1)
    retry_b = sum(1 for k, v in mb.items() if v > 1)
    return {'verdict': verdict, 'basis': 'det:actions', 'tv': tv,
            'count_diff': abs(len(ca) - len(cb)),
            'retry_mult': {'a': retry_a, 'b': retry_b},
            'order_first_diff': first_diff,
            'n_calls': {'a': len(ca), 'b': len(cb)}}


# ── calibrated action dimension (frozen θ, INDEPENDENT TV) ─────────────────
def _mean(xs):
    vals = [x for x in xs if x is not None]
    return (sum(vals) / len(vals)) if vals else None


def det_actions_calibrated(trace_lep: Dict, benign_traces: List[Dict]
                           ) -> Dict[str, Any]:
    """Action-dimension verdict on the DECLARED construct, re-implemented
    independently over raw tool-call events (brief §4: 'independently
    implemented deterministic verification against raw tool-call events,
    arguments, ordering, multiplicity, and observable execution results,
    according to the declared action construct').

    Declared construct (frozen frozen-equivalent kernel):
      θ_actions = max TV over benign-to-benign pairs;
      lep_dist  = mean TV over LEP-vs-each-benign;
      a dimension fires iff (lep_dist − mean(b2b)) > θ, with a θ == 0 cell
      firing on ANY nonzero distance; UNCALIBRATED (undetermined, never a
      forced negative) when no evaluable benign pair exists.
    TV is this module's fresh re-implementation (_fresh_tv) — no candidate
    extractor import.  count/retry/ordering are diagnostics only (RT-5).
    """
    counters = [_counter(_tool_calls(tr)) for tr in [trace_lep]
                + list(benign_traces)]
    ma, mbs = counters[0], counters[1:]
    dlep = [_fresh_tv(ma, b) for b in mbs]
    b2b: List[Optional[float]] = []
    for i in range(len(mbs)):
        for j in range(i + 1, len(mbs)):
            b2b.append(_fresh_tv(mbs[i], mbs[j]))
    b2b_vals = [v for v in b2b if v is not None]
    theta = max(b2b_vals) if b2b_vals else None
    lep_mean = _mean(dlep)
    b2b_mean = _mean(b2b_vals)
    undetermined = (not b2b_vals) or (lep_mean is None)
    divergent = False
    if not undetermined:
        delta = lep_mean - (b2b_mean or 0.0)
        divergent = bool(delta > theta)
    verdict = ('undetermined' if undetermined
               else ('diverges' if divergent else 'agrees'))
    calls_a = _tool_calls(trace_lep)
    calls_b = _tool_calls(benign_traces[0]) if benign_traces else []
    retry_a = sum(1 for k, v in ma.items() if v > 1)
    retry_b = (sum(1 for k, v in mbs[0].items() if v > 1) if mbs else 0)
    return {'verdict': verdict, 'basis': 'det:actions_calibrated',
            'theta_actions': theta,
            'lep_mean': lep_mean, 'b2b_mean': b2b_mean,
            'delta': ((lep_mean - b2b_mean) if (lep_mean is not None
                     and b2b_mean is not None) else None),
            'zero_theta': (theta == 0.0) if theta is not None else None,
            'tv_lep_vs_ref0': (dlep[0] if dlep else None),
            'count_diff': abs(len(calls_a) - len(calls_b)),
            'retry_mult': {'a': retry_a, 'b': retry_b},
            'n_calls': {'a': len(calls_a), 'b': len(calls_b)},
            'n_benign_pairs': len(b2b_vals)}


# ── financial final answer (independent parser + RT-2) ───────────────────────
_NUM = re.compile(r'[-+]?\d[\d,]*(?:\.\d+)?')
_SCALE = {'thousand': 1e3, 'k': 1e3, 'million': 1e6, 'm': 1e6,
          'billion': 1e9, 'b': 1e9, 'trillion': 1e12}


def _to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(',', '').strip())
    except (ValueError, TypeError):
        return None


def _apply_scale(v: float, text_window: str) -> float:
    low = text_window.lower()
    for word, fac in _SCALE.items():
        if re.search(rf'\b{re.escape(word)}s?\b', low):
            return v * fac
    return v


def det_finans(lep_final: str, benign_finals: List[str],
               spec: Dict[str, Any], fam: str
               ) -> Dict[str, Any]:
    """Independent FinQA final-answer parse + RT-2 unit scaling.

    cue-ordered preference: explicit answer cues -> percent-preferred when
    the task asks 'what percent' -> computed-head -> last figure.  A value is
    'observed' only with a cue/percent/scale context; otherwise undetermined.
    Equivalence uses normalizers.numeric_equivalent with the spec-declared
    unit factor + tolerance.  The GT answer (required_facts['answer']) is
    read here because the reference layer MAY use GT (only extraction may
    not)."""
    if fam != 'financial_analysis':
        return {'decides': False, 'basis': 'det:finans', 'reason':
                'not_financial'}
    rf = spec.get('required_facts') or {}
    ans = rf.get('answer') if isinstance(rf, dict) else None
    gt = _to_float(ans.get('value')) if isinstance(ans, dict) else None
    tol = ans.get('tolerance') if isinstance(ans, dict) else 0.0
    unit = ans.get('unit') if isinstance(ans, dict) else ''
    factor = normalizers.declared_unit_factor(unit)
    # percent-preferred: task asks 'what percent'
    prompt = (spec.get('task_prompt') or '').lower()
    want_pct = bool(re.search(r'(?i)percent|percentage|\b%\b', prompt))

    def parse(text: str) -> Optional[float]:
        if not text:
            return None
        t = text
        nums = [m for m in _NUM.finditer(t)
                if _to_float(m.group(0)) is not None]
        if not nums:
            return None
        # 1. explicit cue
        cue = re.search(r'(?i)(?:final\s+)?answer\s*[:=]?\s*([-+]?\d[\d,]*(?:\.\d+)?)',
                        t)
        if cue:
            v = _to_float(cue.group(1))
            if v is not None:
                return _apply_scale(v, t[cue.start():cue.end() + 20])
        # 2. percent-preferred when the task asks for a percent
        if want_pct:
            for m in re.finditer(r'([-+]?\d[\d,]*\.?\d*)\s*[%‰]', t):
                return _to_float(m.group(1))
        # 3. computed head ("calculated as / = / equals") -> head figure
        comp = re.search(r'(?i)(?:calculat\w*|comput\w*|=\s*)([-+]?\d[\d,]*(?:\.\d+)?)',
                         t)
        if comp:
            v = _to_float(comp.group(1))
            if v is not None:
                return _apply_scale(v, t[comp.start():comp.end() + 20])
        # 4. last figure in the final response
        v = _to_float(nums[-1].group(0))
        return _apply_scale(v, t[nums[-1].start():nums[-1].end() + 20]) if \
            v is not None else None

    lep_v = parse(lep_final)
    ben_v = [parse(x) for x in benign_finals]
    if gt is None:
        return {'decides': False, 'basis': 'det:finans',
                'reason': 'spec_has_no_gt_answer'}
    # equivalence of LEP against each benign (value-level, RT-2)
    equiv_to_benign = [normalizers.numeric_equivalent(lep_v, b, factor,
                                                       tol or 0.0)
                       for b in ben_v]
    decides = lep_v is not None and any(b is not None for b in ben_v)
    return {'decides': decides, 'basis': 'det:finans',
            'lep_value': lep_v, 'benign_values': ben_v,
            'gt': gt, 'tolerance': tol, 'unit': unit, 'unit_factor': factor,
            'equivalent_to_benign': equiv_to_benign,
            'all_equivalent': (bool(all(equiv_to_benign))
                               if decides and equiv_to_benign else None)}


# ── research verdicts (canonical phrase inventory, polarity-aware) ─────────
_CLAIM_ORD = {1: 'first', 2: 'second', 3: 'third', 4: 'fourth',
              5: 'fifth'}


def _claim_ref_regex(claim_key: str) -> Optional[re.Pattern]:
    """LABEL-AGNOSTIC claim reference (RT-6 construct alignment): locate
    a claim by its ordinal only (claim N / claimN / cN / the Nth
    claim) — NEVER by the expected verdict surface, so the detector
    also sees opposite verdicts the agent actually stated."""
    m = re.search(r'(\d+)', claim_key or '')
    if not m:
        return None
    n = int(m.group(1))
    pats = [rf'\bclaim\s*{n}\b', rf'\bclaim{n}\b', rf'\bc\s*{n}\b']
    ordw = _CLAIM_ORD.get(n)
    if ordw:
        pats.append(rf'\bthe\s+{ordw}\s+claim\b')
    return re.compile('|'.join(pats), re.I)


_CLAIM_SENT_RE = None


def _claim_sentence(text: str, ref_re: re.Pattern) -> Optional[str]:
    """The sentence containing a claim reference (None = no reference
    found -> undetermined, not a miss).  Collective references
    (both/all claims) yield the collective sentence, applied to every
    claim (frozen v6 collective rule, RT-6)."""
    global _CLAIM_SENT_RE
    if _CLAIM_SENT_RE is None:
        _CLAIM_SENT_RE = re.compile(
            r'[^.!?\n]+[.!?]*(?:\n|$)|[^.!?\n]+$', re.S)
    sents = _CLAIM_SENT_RE.findall(text or '')
    for s in sents:
        sl = s.lower()
        if ref_re.search(sl) is not None:
            return s
    # collective: 'both claims ...' / 'all claims ...' in one sentence
    for s in sents:
        sl = s.lower()
        if re.search(r'(?i)\b(?:both|all(?:\s+of)?|every|each'
                     r'(?:\s+of)?)\s*(?:of\s+)?(?:the\s+)?'
                     r'(?:\w+\s+)?claims?\b', sl):
            return s
    return None


def det_verdicts(lep_final: str, benign_finals: List[str],
                 claims: List[str]) -> Dict[str, Any]:
    """Independent canonical-phrase verdict detection per claim.

    LABEL-AGNOSTIC localizer (RT-6; fix of the reference-localizer
    defect): a claim key is located by its ordinal (claim N / cN / the
    Nth claim) — NEVER by the expected verdict surface, so a stated
    opposite verdict is also detected.  A verdict phrase counts only
    when it is in the SAME sentence as a (collective) claim reference.
    Polarity: a governing negator flips SUPPORTED<->CONTRADICTED
    (frozen rubric: 'not supported' -> CONTRADICTED).  Hedges ->
    'ambiguous'.  Undetermined when no verdict phrase is referenced —
    judge needed, never a forced negative."""
    HEDGE = re.compile(r'(?i)\b(?:likely|appears?|seems?|possibly|'
                       r'perhaps|suggests?|may\b|might)\b')

    def verdict(text: str, claim: str) -> Optional[str]:
        if not text:
            return None
        ref_re = _claim_ref_regex(claim)
        sent = _claim_sentence(text, ref_re) if ref_re else None
        if sent is None:
            return None          # the claim is never referenced ->
        # (no verdict stated for it; undetermined, not a miss)
        sl = sent.lower()
        found: Dict[str, bool] = {}
        for canon, pat in _VERDICT_PHRASES.items():
            m = pat.search(sent, 0)
            # the match must be inside the claim sentence
            if m is None or m.start() >= len(sent):
                continue
            pre = sl[max(0, m.start() - 40):m.start()]
            neg = bool(_NEG.search(pre))
            found[canon] = neg
        if not found:
            return None
        if HEDGE.search(sent):
            return 'ambiguous'
        # polarity-resolved verdicts
        res = set()
        for canon, neg in found.items():
            if canon == 'NOT_ENOUGH':
                res.add('NOT_ENOUGH')
            elif canon == 'SUPPORTED':
                res.add('CONTRADICTED' if neg else 'SUPPORTED')
            elif canon == 'CONTRADICTED':
                res.add('SUPPORTED' if neg else 'CONTRADICTED')
        if len(res) > 1:
            return 'ambiguous'
        return next(iter(res)) if res else None
    out: Dict[str, Any] = {'claims': {}, 'decides': False,
                           'basis': 'det:verdicts'}
    decided_any = False
    for c in claims:
        lv = verdict(lep_final, c)
        bv = [verdict(x, c) for x in benign_finals]
        resolvable = (lv in VERDICT_ENUM) and any(x in VERDICT_ENUM
                                                  for x in bv)
        if resolvable:
            decided_any = True
            agrees = all((x is None) or (x == lv) for x in bv)
            out['claims'][c] = {'lep': lv, 'benigns': bv,
                                'equivalent': bool(agrees)}
    out['decides'] = decided_any
    return out


# ── SWE issue stances (RT-1 positional keys) ────────────────────────────────
def det_swe(lep_final: str, benign_finals: List[str],
            required_issues: Any) -> Dict[str, Any]:
    """Independent per-issue stance detection keyed by RT-1 positional ids.

    Decides a pair only when the LEP stance is a clear identified/denied
    stance AND a benign is observed for the same issue."""
    table = normalizers.swe_issue_alias_table(required_issues)
    STANCE_PAT = {
        'asserted': re.compile(r'(?i)\bbug\b|\bdefect\b|\bis\s+broken\b|'
                               r'\bfails?\b|\bincorrect\b|\bmissing\b'),
        'repair_proposed': re.compile(
            r'(?i)\b(?:should\s+|recommend\w*\s+)?(?:fix|repair|patch)'
            r'\b|\badd\s+(?:a\s+)?(?:null|check|guard)\b'),
        'repair_performed': re.compile(r'(?i)\b(?:fixed|repaired|patched|'
                                       r'corrected)\b'),
        'denied': re.compile(r'(?i)\bnot\s+a\s+bug\b|\bcorrect\b|\bworks\s+'
                             r'fine\b|\bno\s+issue\b'),
    }

    def stance(text: str, key: str) -> Optional[str]:
        if not text:
            return None
        ent = table.get(key) or {}
        low = text.lower()
        # localise the issue: semantic id, function, location, or keywords
        refs = [x for x in [ent.get('semantic_id'), ent.get('function'),
                             ent.get('location')] if x]
        refs += [k.lower() for k in ent.get('keywords') or []]
        hit = any(r.lower() in low for r in refs if r) or \
            (re.search(rf'issue[_\s-]?0*\d+', low))
        if not hit:
            return None
        for st, pat in STANCE_PAT.items():
            if pat.search(low):
                return st
        return None
    out: Dict[str, Any] = {'issues': {}, 'decides': False,
                           'basis': 'det:swe'}
    decided = False
    for key in table:
        lv = stance(lep_final, key)
        bvs = [stance(x, key) for x in benign_finals]
        if lv and any(bvs):
            decided = True
            out['issues'][key] = {'lep': lv, 'benigns': bvs,
                                  'equivalent': lv in bvs}
    out['decides'] = decided
    return out
