#!/usr/bin/env python3.11
"""Construct the stratified measurement-validation sample (300 unique traces).

READ-ONLY over every frozen artifact.  Writes ``sample_manifest.jsonl`` and
``SAMPLING_DESIGN.json`` under ``analysis/measurement_validation/``.

DESIGN — two components, kept strictly identifiable
---------------------------------------------------
**A. Representative probability sample.**

  The frame is 16,773 measurement rows = 16,773 distinct LEP executions
  (verified: no two frame units sharing a trace_id have the same attempt_id).
  The representative component is drawn as a stratified SRSWOR over that
  frame, stratified by ``(task_family x M1_status)`` where

      M1_status = 'frozen_neg_v6_neg' | 'frozen_neg_v6_pos'
                | 'frozen_pos_v6_neg' | 'frozen_pos_v6_pos'

  because "frozen/v6 disagreement" is one of the required coverage axes and
  it must be present in the UNWEIGHTED component too (otherwise the
  disagreement-rate estimate would rest entirely on the enriched sample).

  Allocation is proportional-with-floor: each stratum's take is
  ``max(1, round(n_h * f))`` subject to the family budget, then topped up to
  the budget from the largest remaining strata.  The result is that stratum
  inclusion probabilities differ (so ``weight = N_h / n_h`` is non-constant
  and MUST be applied for any population-level estimate) — this is the
  textbook reason the naive pooled rate is not the population rate.

  Benign executions are drawn in a SEPARATE component (A2) with their own
  strata, because benign traces are not in the LEP frame at all: they are
  the match controls, and they are the population in which "persistent benign
  failure" lives.

**B. Targeted error-enriched sample.**

  Deliberately over-represents a list of named diagnostic conditions
  (frozen/v6 disagreement, zero-threshold positives, polarity inversions,
  ambiguous numeric answers, unavailable features, persistent benign failure,
  unusual tool-call sequences, assertions/decisions disagreement).  Each
  stratum is defined by a PREDICATE over the frame, documented in
  ``SAMPLING_DESIGN.json``, and every selected unit records WHICH predicate
  put it there.  Inclusion probabilities are recorded for the targeted
  component too (they are not equal to the representative ones) so a
  reader can see exactly why the two components cannot be pooled.

  The targeted component is NOT weighted back to the population and its
  accuracy numbers are NEVER presented as population accuracy.

**No impossible combinations.**  Strata are built by intersecting real
frame predicates, so a stratum that does not exist simply receives 0 units
and is reported as empty rather than being forced.

Usage:
    python3.11 analysis/measurement_validation/scripts/sample_validation.py
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import common as C  # noqa: E402

FRAME = os.path.join(C.CACHE_DIR, 'frame_units.jsonl')
OUT_MANIFEST = os.path.join(C.VALIDATION_ROOT, 'sample_manifest.jsonl')
OUT_DESIGN = os.path.join(C.VALIDATION_ROOT, 'SAMPLING_DESIGN.json')

DIMS = C.DESIGN['dimensions']


# ── framing helpers ──────────────────────────────────────────────────────────
def m1_status(r: dict) -> str:
    return ('frozen_pos' if r['m1_frozen'] else 'frozen_neg') + '_' + \
           ('v6_pos' if r['m1_v6'] else 'v6_neg')


def all_dims_missing(r: dict) -> bool:
    return all(r['frozen_per_dimension'][d]['d_lep_missing'] for d in DIMS)


def zero_only_positive(r: dict) -> bool:
    p = r['frozen_per_dimension']
    t = [d for d in DIMS if p[d]['positive']]
    return bool(t) and all(p[d]['zero_threshold'] for d in t)


def nonzero_positive(r: dict) -> bool:
    p = r['frozen_per_dimension']
    return any(p[d]['positive'] and not p[d]['zero_threshold'] for d in DIMS)


def v6_zero_only(r: dict) -> bool:
    u = r.get('v6_union') or {}
    t = u.get('triggered_dimensions') or []
    return bool(t) and len(u.get('zero_threshold_triggered') or []) == len(t)


def unresolved_dup(r: dict) -> bool:
    return bool(r.get('frozen_has_dup_trace_id'))


#: Targeted predicates.  Each entry is (name, human description, predicate).
#: They are MUTUALLY OVERLAPPING by design (a trace can be both a
#: disagreement and a zero-threshold positive); a unit records every
#: predicate it satisfies, so no information is lost by the overlap.
TARGETED_PREDICATES = [
    ('frozen_v6_m1_disagreement',
     'Frozen and v6 disagree on the four-dimensional union (M1)',
     lambda r: r['m1_frozen'] != r['m1_v6']),
    ('zero_threshold_positive',
     'Frozen M1 positive resting ONLY on dimensions with theta_d == 0',
     zero_only_positive),
    ('nonzero_threshold_positive',
     'Frozen M1 positive with at least one dimension whose theta_d > 0',
     nonzero_positive),
    ('v6_zero_threshold_ony',
     'v6 union positive where every triggered dimension has theta_d == 0',
     v6_zero_only),
    ('polarity_inversion',
     'An observed assertion value flips SUPPORTED<->CONTRADICTED between '
     'the frozen and v6 extractors',
     lambda r: (r.get('derived') or {}).get('polarity_inversions', 0) > 0),
    ('extractor_value_differs',
     'An observed assertion or decision has a DIFFERENT value under the two '
     'extractors',
     lambda r: ((r.get('derived') or {}).get('value_diff_assertions', 0) > 0
                or (r.get('derived') or {}).get('value_diff_decisions', 0) > 0)),
    ('extractor_state_differs',
     'An assertion key has a DIFFERENT observation STATE under the two '
     'extractors',
     lambda r: (r.get('derived') or {}).get('state_diff_assertions', 0) > 0),
    ('ambiguous_answer',
     'A required fact, verdict or stance is AMBIGUOUS under either extractor',
     lambda r: (
         any((r['states'].get(w) or {}).get('assertions', {}).get('ambiguous', 0)
             for w in ('frozen', 'v6'))
         or (r.get('derived') or {}).get('frozen_deliverable_state')
         == 'ambiguous'
         or (r.get('derived') or {}).get('v6_deliverable_state') == 'ambiguous')),
    ('answer_unavailable',
     'The FINAL DELIVERABLE for this family is not extractable under either '
     'extractor (financial: final answer; research: claim verdicts; code: '
     'issue stances)',
     lambda r: ((r.get('derived') or {}).get('frozen_deliverable_state')
                in (None, 'unavailable')
                or (r.get('derived') or {}).get('v6_deliverable_state')
                in (None, 'unavailable'))),
    ('partial_deliverable_extraction',
     'The frozen extractor observed some but not all of the family '
     'deliverable items (partial extraction, the state that most easily '
     'masquerades as a behavioral difference)',
     lambda r: 0.0 < ((r.get('derived') or {})
                      .get('frozen_deliverable_observed_frac') or 0.0) < 1.0),
    ('all_dimensions_unevaluable',
     'Every per-dimension distance is unavailable for the LEP side',
     all_dims_missing),
    ('dimension_partially_unevaluable',
     'At least one but not all per-dimension distances are unavailable',
     lambda r: (not all_dims_missing(r)
                and any(r['frozen_per_dimension'][d]['d_lep_missing']
                        for d in DIMS))),
    ('retry_or_duplicate_actions',
     'A (role, tool, target) action key is repeated within one trace, giving '
     'the action dimension a retry-blind structure',
     lambda r: (r.get('derived') or {}).get('action_dup_max', 0) > 1),
    ('assertions_decisions_disagree',
     'The assertions and decisions dimensions disagree on divergence '
     '(the double-counting / separation concern)',
     lambda r: (r['frozen_per_dimension']['assertions']['positive']
                != r['frozen_per_dimension']['decisions']['positive'])
     or (bool((r.get('v6_per_dimension') or {}).get('assertions', {})
              .get('divergent'))
         != bool((r.get('v6_per_dimension') or {}).get('decisions', {})
                 .get('divergent')))),
    ('m4_matched_degraded',
     'v6 M4-matched: perturbation-ASSOCIATED task degradation against a '
     'benign reference that succeeded',
     lambda r: bool(r.get('m4_matched_v6'))),
    ('m4_persistent_failure',
     'M3-supported LEP failure on a fixture where the benign references also '
     'failed (the documented M4 over-statement class)',
     lambda r: r.get('m4_class_v6') == 'persistent_failure'),
    ('unresolved_duplicate_id',
     'The frozen pipeline emitted two distinct rows sharing one trace_id',
     unresolved_dup),
    ('delivery_unverified',
     'The downstream artifact records the perturbation as not delivered',
     lambda r: r.get('delivery_verified') is False),
    ('not_m3_supported',
     'No dependency-supported downstream reach',
     lambda r: r.get('m3_reach_class') == 'not_downstream'),
]


def benign_predicates(idx_rows):
    """Targeted predicates over the BENIGN control population.

    Benign executions have no LEP, so most LEP-side strata do not apply.
    What matters here is benign-side variability, because that is what the
    threshold theta_d is estimated FROM: a benign pool whose members disagree
    strongly with each other sets a high threshold, and vice versa.
    """
    return [
        ('no_tool_use',
         'A benign execution that used no tool at all (action dimension '
         'undefined against it)',
         lambda r: (r.get('n_actions') or 0) == 0),
        ('heavy_retry',
         'A benign execution with a repeated (role, tool, target) key '
         '(multiplicity >= 3)',
         lambda r: (r.get('action_dup_max') or 0) >= 3),
        ('many_events',
         'A benign execution in the top decile by event count',
         lambda r: (r.get('n_events') or 0) >= (r.get('_p90_events') or 10 ** 9)),
        ('no_final_answer',
         'A benign execution whose final answer is not extractable',
         lambda r: r.get('frozen_answer_state') in (None, 'unavailable')),
    ]


# ── selection primitives ─────────────────────────────────────────────────────
def stratify(rows, key_fn):
    out = collections.defaultdict(list)
    for r in rows:
        out[key_fn(r)].append(r)
    return out


def allocate_proportional(strata: dict, budget: int, rng_seed: int,
                          floor: int = 1):
    """Proportional allocation with a floor, then top-up to the budget.

    Returns {stratum_key: n_to_take}.  Deterministic given the seed: once the
    fractional parts tie, the tie is broken by a seeded uniform draw rather
    than by dict order, so the allocation is reproducible and not
    insertion-order dependent.
    """
    total = sum(len(v) for v in strata.values())
    if total == 0 or budget <= 0:
        return {k: 0 for k in strata}
    rng = C.Rng(rng_seed)
    keys = sorted(strata, key=str)
    # cap the floor at what the stratum can supply
    take = {k: min(floor, len(strata[k])) for k in keys}
    remaining = budget - sum(take.values())
    if remaining <= 0:
        # floor already exceeds budget: trim the smallest strata first
        order = sorted(keys, key=lambda k: (len(strata[k]), str(k)))
        for k in order:
            if sum(take.values()) <= budget:
                break
            take[k] = max(0, take[k] - 1)
        return take
    # proportional on the residual
    exact = {k: remaining * len(strata[k]) / total for k in keys}
    base = {k: int(exact[k]) for k in keys}
    # distribute the remainder by largest fractional part, seeded tie-break
    fracs = sorted(keys, key=lambda k: (-(exact[k] - base[k]), rng.uniform()))
    for k in keys:
        take[k] += base[k]
    i = 0
    while sum(take.values()) < budget and i < len(fracs) * 4:
        k = fracs[i % len(fracs)]
        if take[k] < len(strata[k]):
            take[k] += 1
        i += 1
    # never exceed what the stratum holds; push the excess to strata with room
    for k in keys:
        if take[k] > len(strata[k]):
            take[k] = len(strata[k])
    i = 0
    while sum(take.values()) < budget and i < len(keys) * 40:
        k = keys[i % len(keys)]
        if take[k] < len(strata[k]):
            take[k] += 1
        i += 1
    return take


def draw_units(rows, n, seed, stratum_of):
    """Seeded SRSWOR of ``n`` units plus per-unit inclusion probability."""
    if n <= 0 or not rows:
        return []
    N = len(rows)
    picked = C.Rng(seed).srs_wor(list(range(N)), n)
    out = []
    for i in picked:
        r = rows[i]
        rec = {
            'unit_id': r['unit_id'],
            'trace_id': r['trace_id'],
            'attempt_id': r.get('attempt_id'),
            'source_path': None,       # filled by the caller from the index
            'task_family': r['task_family'],
            'fixture_id': r['fixture_id'],
            'topology': r['topology'],
            'propagation_mode': r['propagation_mode'],
            'lep_code': r['lep_code'],
            'kind': 'lep',
            'reference_set': r['reference_set'],
            'stratum': stratum_of(r),
            'n_in_stratum': N,
            'n_drawn_from_stratum': n,
            'inclusion_probability': n / N,
            'design_weight': N / n,
            'population_estimable': True,
            'weight_meaning': 'srs_within_stratum',
        }
        out.append(rec)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--frame', default=FRAME)
    a = ap.parse_args()

    frame = C.read_jsonl(a.frame)
    idx = {r['trace_id']: r for r in C.read_jsonl(
        os.path.join(C.CACHE_DIR, 'trace_index.jsonl'))}
    print(f'frame units: {len(frame)}')
    D = C.DESIGN
    fams = D['families']

    # ── Component A1: representative LEP sample ──────────────────────────────
    per_fam_lep = D['n_rep_lep_per_family']
    A1 = []
    A1_allocation = {}
    for fi, fam in enumerate(fams):
        rows = [r for r in frame if r['task_family'] == fam]
        strata = stratify(rows, m1_status)
        take = allocate_proportional(strata, per_fam_lep,
                                     D['seed_rep_leps'] + fi)
        A1_allocation[fam] = {k: {'n_population': len(strata[k]),
                                  'n_taken': take[k],
                                  'inclusion_probability':
                                      (take[k] / len(strata[k]))
                                      if strata[k] else None}
                              for k in sorted(strata, key=str)}
        for k in sorted(strata, key=str):
            A1 += draw_units(strata[k], take[k],
                             C.mix_seed(D['seed_rep_leps'], 1000 * (fi + 1), k),
                             lambda r: f'{r["task_family"]}|{m1_status(r)}')
    for u in A1:
        u['component'] = 'A_representative'

    # ── Component A2: representative BENIGN sample ───────────────────────────
    # Benign population = the union of every reference set in the frame; this
    # is exactly the control pool the thresholds are calibrated from.
    benign_ids = sorted({rid for r in frame for rid in r['reference_set']})
    benign_rows = []
    for bid in benign_ids:
        meta = idx.get(bid)
        if not meta:
            continue
        p = meta['path']
        try:
            tr = C.read_json(p)
        except Exception:                                       # noqa: BLE001
            continue
        m = tr.get('metadata') or {}
        benign_rows.append({
            'trace_id': bid,
            'path': p,
            'task_family': m.get('task_family'),
            'fixture_id': m.get('fixture_id') or bid.split('.')[0],
            'topology': m.get('topology'),
            'propagation_mode': m.get('propagation_mode', 'single_origin'),
            'n_events': len(tr.get('events') or []),
        })
    print(f'benign control population: {len(benign_rows)} executions '
          f'({len(benign_ids)} unique ids referenced by the frame)')

    # benign-side extraction states (both extractors), needed for the
    # persistent-benign-failure and unavailable strata
    sys.path.insert(0, HERE)
    from extractors import load_specs, run_both                     # noqa: E402
    from frame_population import deliverable_states                 # noqa: E402
    from graders import grade_family                                # noqa: E402
    specs = load_specs()

    def benign_enrich(b):
        spec = specs.get(b['fixture_id'])
        if spec is None:
            b['grade'] = 'ungradeable'
            b['frozen_deliverable_state'] = None
            b['n_actions'] = 0
            b['action_dup_max'] = 0
            return b
        tr = C.read_json(b['path'])
        fam = b['task_family']
        st = run_both(tr, spec, fam)
        fz = st['frozen_obj']
        dl = deliverable_states(fam, st['frozen'], st['v6'])
        b['frozen_deliverable_state'] = dl['frozen']['dominant']
        b['v6_deliverable_state'] = dl['v6']['dominant']
        b['deliverable_observed_frac'] = (
            (dl['frozen']['states'].get('observed', 0) / dl['frozen']['n'])
            if dl['frozen']['n'] else None)
        b['grade'], b['grade_detail'] = _grade_benign(fam, fz, spec)
        b['n_actions'] = sum(fz.action_counters.values())
        ctr = {}
        for k, n in fz.action_counters.items():
            ctr[k] = ctr.get(k, 0) + n
        b['action_dup_max'] = max(ctr.values()) if ctr else 0
        return b

    benign_rows = [benign_enrich(b) for b in benign_rows]
    print(f'benign graded: '
          f'{collections.Counter(b["grade"] for b in benign_rows).most_common()}')

    # p90 event count for the "many events" predicate
    ev = sorted(b['n_events'] for b in benign_rows)
    p90 = ev[int(0.9 * (len(ev) - 1))] if ev else 0
    for b in benign_rows:
        b['_p90_events'] = p90

    per_fam_ben = D['n_rep_benign_per_family']
    A2 = []
    A2_allocation = {}
    for fi, fam in enumerate(fams):
        rows = [b for b in benign_rows if b['task_family'] == fam]
        strata = stratify(rows, lambda b: b['grade'] or 'unknown')
        take = allocate_proportional(strata, per_fam_ben,
                                     D['seed_rep_benign'] + fi)
        A2_allocation[fam] = {k: {'n_population': len(strata[k]),
                                  'n_taken': take[k]} for k in sorted(strata)}
        for k in sorted(strata, key=str):
            n = take[k]
            if n <= 0:
                continue
            N = len(strata[k])
            for i in C.Rng(C.mix_seed(D['seed_rep_benign'],
                                      1000 * (fi + 1), k)).srs_wor(
                    list(range(N)), n):
                b = strata[k][i]
                A2.append({
                    'unit_id': b['trace_id'] + '#benign',
                    'trace_id': b['trace_id'],
                    'attempt_id': None,
                    'source_path': b['path'],
                    'task_family': fam,
                    'fixture_id': b['fixture_id'],
                    'topology': b['topology'],
                    'propagation_mode': b['propagation_mode'],
                    'lep_code': None,
                    'kind': 'benign',
                    'reference_set': [],
                    'stratum': f'benign|{fam}|{b["grade"]}',
                    'n_in_stratum': N,
                    'n_drawn_from_stratum': n,
                    'inclusion_probability': n / N,
                    'design_weight': N / n,
                    'population_estimable': True,
                    'weight_meaning': 'srs_within_stratum',
                    'benign_grade_frozen': b['grade'],
                    'component': 'A_representative',
                })

    # ── Component B: targeted error-enriched LEP sample ──────────────────────
    per_fam_tgt = D['n_targeted_lep_per_family']
    B = []
    B_strata = {}
    for fi, fam in enumerate(fams):
        rows = [r for r in frame if r['task_family'] == fam]
        # a unit is a member of every predicate it satisfies
        buckets = collections.defaultdict(list)
        for r in rows:
            for name, _desc, pred in TARGETED_PREDICATES:
                try:
                    if pred(r):
                        buckets[name].append(r)
                except Exception:                               # noqa: BLE001
                    continue
        B_strata[fam] = {k: len(v) for k, v in sorted(buckets.items())}
        # round-robin over predicates, de-duplicating units, until budget
        rng = C.Rng(D['seed_targeted'] + fi)
        order = [n for n, _d, _p in TARGETED_PREDICATES]
        order = rng.order(order)
        chosen, seen = [], set()
        cursor = {k: 0 for k in order}
        perm = {k: rng.order(list(range(len(buckets[k])))) for k in order}
        exhausted = set()
        while len(chosen) < per_fam_tgt and len(exhausted) < len(order):
            for k in order:
                if len(chosen) >= per_fam_tgt:
                    break
                if k in exhausted:
                    continue
                while cursor[k] < len(perm[k]) and \
                        buckets[k][perm[k][cursor[k]]]['unit_id'] in seen:
                    cursor[k] += 1
                if cursor[k] >= len(perm[k]):
                    exhausted.add(k)
                    continue
                r = buckets[k][perm[k][cursor[k]]]
                cursor[k] += 1
                seen.add(r['unit_id'])
                tags = [n for n, _d, pr in TARGETED_PREDICATES
                        if _safe(pr, r)]
                N = len(buckets[k])
                chosen.append({
                    'unit_id': r['unit_id'],
                    'trace_id': r['trace_id'],
                    'attempt_id': r.get('attempt_id'),
                    'source_path': (idx.get(r['trace_id']) or {}).get('path'),
                    'task_family': r['task_family'],
                    'fixture_id': r['fixture_id'],
                    'topology': r['topology'],
                    'propagation_mode': r['propagation_mode'],
                    'lep_code': r['lep_code'],
                    'kind': 'lep',
                    'reference_set': r['reference_set'],
                    'stratum': f'targeted|{fam}|{k}',
                    'targeted_predicates': tags,
                    'primary_targeted_predicate': k,
                    'n_in_stratum': N,
                    'n_drawn_from_stratum': 1,
                    # Within-bucket draw probability, kept for auditability.
                    # NOT a population inclusion probability: this unit was
                    # selected BECAUSE it satisfies an outcome-dependent
                    # predicate, so weighting it back to the population would
                    # be biased.  population_estimable=False is what stops a
                    # naive pooled estimate from being computed.
                    'inclusion_probability': 1 / N if N else None,
                    'design_weight': N if N else None,
                    'population_estimable': False,
                    'weight_meaning': 'within_targeted_bucket_only',
                    'component': 'B_targeted',
                })
        B += chosen

    # ── targeted benign component (B2) ───────────────────────────────────────
    per_fam_tgt_ben = D['n_targeted_benign_per_family']
    B2 = []
    B2_strata = {}
    bpreds = benign_predicates(benign_rows)
    for fi, fam in enumerate(fams):
        rows = [b for b in benign_rows if b['task_family'] == fam]
        buckets = collections.defaultdict(list)
        for r in rows:
            for name, _desc, pred in bpreds:
                try:
                    if pred(r):
                        buckets[name].append(r)
                except Exception:                               # noqa: BLE001
                    continue
        B2_strata[fam] = {k: len(v) for k, v in sorted(buckets.items())}
        rng = C.Rng(D['seed_targeted'] + 500 + fi)
        order = rng.order([n for n, _d, _p in bpreds])
        chosen, seen = [], set()
        cursor = {k: 0 for k in order}
        perm = {k: rng.order(list(range(len(buckets[k])))) for k in order}
        exhausted = set()
        while len(chosen) < per_fam_tgt_ben and len(exhausted) < len(order):
            for k in order:
                if len(chosen) >= per_fam_tgt_ben:
                    break
                if k in exhausted:
                    continue
                while cursor[k] < len(perm[k]) and \
                        buckets[k][perm[k][cursor[k]]]['trace_id'] in seen:
                    cursor[k] += 1
                if cursor[k] >= len(perm[k]):
                    exhausted.add(k)
                    continue
                r = buckets[k][perm[k][cursor[k]]]
                cursor[k] += 1
                seen.add(r['trace_id'])
                N = len(buckets[k])
                chosen.append({
                    'unit_id': r['trace_id'] + '#benign',
                    'trace_id': r['trace_id'],
                    'attempt_id': None,
                    'source_path': r['path'],
                    'task_family': fam,
                    'fixture_id': r['fixture_id'],
                    'topology': r['topology'],
                    'propagation_mode': r['propagation_mode'],
                    'lep_code': None,
                    'kind': 'benign',
                    'reference_set': [],
                    'stratum': f'targeted_benign|{fam}|{k}',
                    'targeted_predicates': [n for n, _d, pr in bpreds
                                            if _safe(pr, r)],
                    'primary_targeted_predicate': k,
                    'n_in_stratum': N,
                    'n_drawn_from_stratum': 1,
                    # See the LEP-targeted note above: within-bucket only.
                    'inclusion_probability': 1 / N if N else None,
                    'design_weight': N if N else None,
                    'population_estimable': False,
                    'weight_meaning': 'within_targeted_bucket_only',
                    'benign_grade_frozen': r['grade'],
                    'component': 'B_targeted',
                })
        B2 += chosen

    # ── assemble, deduplicate ACROSS components (representative wins) ────────
    all_recs = A1 + A2 + B + B2
    by_unit = {}
    for rec in all_recs:
        k = (rec['unit_id'], rec['kind'])
        if k in by_unit:
            prev = by_unit[k]
            if prev['component'] == 'A_representative':
                prev.setdefault('also_selected_by', []).append(rec['component'])
                prev.setdefault('also_in_targeted_strata', []).append(
                    rec['stratum'])
                continue
            if rec['component'] == 'A_representative':
                rec.setdefault('also_selected_by', []).append(prev['component'])
                by_unit[k] = rec
                continue
            continue
        by_unit[k] = rec

    final = sorted(by_unit.values(), key=lambda r: (r['component'],
                                                    r['task_family'],
                                                    r['unit_id']))
    for r in final:
        meta = idx.get(r['trace_id'])
        if meta and not r.get('source_path'):
            r['source_path'] = meta['path']
        r['source_path'] = (idx.get(r['trace_id']) or {}).get('path') \
            if r['kind'] == 'lep' else r.get('source_path')

    C.write_jsonl(OUT_MANIFEST, final)

    # ── design record ────────────────────────────────────────────────────────
    def count(pred, rows):
        return sum(1 for r in rows if pred(r))

    design = {
        'design_version': D['framework_version'],
        'seeds': {k: v for k, v in D.items() if k.startswith('seed')},
        'frame': {
            'source': 'analysis/consolidated_20261121_bnd_v41',
            'n_measurement_rows': len(frame),
            'n_unique_trace_ids': len({r['trace_id'] for r in frame}),
            'n_ambiguous_trace_ids': len({r['trace_id'] for r in frame
                                          if r['frozen_has_dup_trace_id']}),
            'population_definition': (
                'The 16,773 comparable LEP measurement rows of the consolidated '
                'BND V4.1 run: rows whose reference pool satisfied the frozen '
                'comparability gate (>=3 structurally compatible benign '
                'references sharing one fixture/topology/cell). '
                '1,164 of the 17,937 staged LEP traces are NOT comparable and '
                'are outside this frame.'),
            'not_the_same_population_as': [
                'analysis/bnd_v41_cw_swe200_provisional/run_merged '
                '(frozen module 6f0f7ff3; 10,057 comparable rows, a different '
                'module revision and a different trace staging)',
                'the 17,937 staged LEP traces of the consolidated corpus '
                '(includes the 1,164 non-comparable rows)',
                'the 26,965 staged trace files (includes 9,028 benign '
                'executions that are controls, not measurements)',
            ],
        },
        'benign_control_population': {
            'n_unique_referenced_benign_executions': len(benign_rows),
            'definition': ('Every trace_id appearing in any frame row\'s '
                           'reference_set. These are the controls the benign '
                           'thresholds are calibrated from.'),
            'grade_distribution_frozen':
                dict(collections.Counter(b['grade'] for b in benign_rows)),
        },
        'component_A_representative': {
            'description': ('Stratified SRSWOR over the frame, stratified by '
                            '(task_family x M1_status); benign component '
                            'stratified by (task_family x frozen benign '
                            'grade). Inclusion probabilities are per stratum '
                            'N_h/n_h and are recorded on every unit.'),
            'lep_allocation': A1_allocation,
            'benign_allocation': A2_allocation,
            'population_weight_eligible': True,
        },
        'component_B_targeted': {
            'description': ('Error-enriched selection. Each stratum is a '
                            'documented predicate over the frame; units are '
                            'taken round-robin across predicates with a fixed '
                            'seeded permutation. Units satisfying several '
                            'predicates record all of them.'),
            'predicates': [{'name': n, 'description': d}
                           for n, d, _p in TARGETED_PREDICATES],
            'benign_predicates': [{'name': n, 'description': d}
                                  for n, d, _p in bpreds],
            'stratum_sizes': B_strata,
            'benign_stratum_sizes': B2_strata,
            'population_weight_eligible': False,
            'warning': ('Targeted strata are deliberately over-sampled. The '
                        'pooled rate over B is NOT a population estimate and '
                        'must never be reported as one. Every report in this '
                        'framework separates A and B.'),
        },
        'counts': {
            'total_units': len(final),
            'A_representative': sum(1 for r in final
                                    if r['component'] == 'A_representative'),
            'B_targeted': sum(1 for r in final
                              if r['component'] == 'B_targeted'),
            'lep': sum(1 for r in final if r['kind'] == 'lep'),
            'benign': sum(1 for r in final if r['kind'] == 'benign'),
            'by_family': dict(collections.Counter(
                r['task_family'] for r in final)),
            'by_family_and_kind': {
                f'{f}|{k}': v for (f, k), v in
                sorted(collections.Counter(
                    (r['task_family'], r['kind']) for r in final).items())},
            'by_topology': dict(collections.Counter(
                r['topology'] for r in final)),
            'by_lep': dict(collections.Counter(
                r['lep_code'] for r in final if r['kind'] == 'lep')),
            'by_propagation_mode': dict(collections.Counter(
                r['propagation_mode'] for r in final)),
            # The emitted sample records deliberately do NOT carry the BND
            # verdicts (they would leak the label into the annotation
            # packets), so the achieved positive rate is joined back from the
            # frame by unit_id rather than read off the sample rows.
            'achieved_m1_frozen_pos': _frame_pos_rate(final, frame),
            'achieved_m1_v6_pos': _frame_pos_rate(final, frame, key='m1_v6'),
            'targeted_predicate_membership': {
                n: count(lambda r: n in (r.get('targeted_predicates') or []),
                         final) for n, _d, _p in TARGETED_PREDICATES},
        },
        'constraints_respected': {
            'no_memory_poisoning': True,
            'note': ('LEP_MEMORY_POISONING and memory-enabled variants are '
                     'absent from this corpus (verified: 0 traces with a '
                     'MEMORY_POISONING lep_code, 0 non-standard '
                     'execution_variant), so nothing had to be excluded.'),
            'only_four_retained_leps': True,
            'no_impossible_combinations': (
                'empty strata are reported as size 0, never forced'),
        },
        'reproducibility': {
            'seed': D['seed'],
            'frame_rows': len(frame),
            'reproducible_command': (
                'python3.11 analysis/measurement_validation/scripts/'
                'sample_validation.py'),
            'note': ('Component A is reproducible from the frame alone: '
                     'strata are sorted before the seeded draw, and the draw '
                     'uses an explicit Mersenne-Twister stream rather than '
                     "random.sample's implementation details."),
        },
        'weight_semantics': {
            'A_representative': ('design_weight = 1/pi is a population '
                                 'weight; weighted estimates over component A '
                                 'generalise to the 16,773-row frame'),
            'B_targeted': ('design_weight is a within-bucket draw weight '
                           'ONLY. Component B is selected on outcome-'
                           'dependent predicates, so it is NOT population-'
                           'estimable; population_estimable=False on every '
                           'component-B row. Pooling A and B and weighting '
                           'the union would be biased and must not be done.'),
        },
    }
    C.write_json(OUT_DESIGN, design)

    print()
    print(f'wrote {len(final)} sampled units -> {OUT_MANIFEST}')
    print(json.dumps(design['counts'], indent=1)[:2200])


def _safe(pred, r):
    try:
        return bool(pred(r))
    except Exception:                                           # noqa: BLE001
        return False


def _frame_pos_rate(units, frame_rows, key='m1_frozen'):
    """Achieved positive rate in the drawn sample, split by kind.

    A representative sample of a 24.7 %-positive population should land near
    24.7 %; reporting the achieved rate (rather than assuming it) is how the
    sample's representativeness gets checked instead of asserted.
    """
    by_id = {r['unit_id']: r for r in frame_rows}
    out = {}
    for kind in ('lep', 'benign'):
        sub = [u for u in units if u.get('kind') == kind]
        vals = [(by_id.get(u['unit_id']) or {}).get(key) for u in sub]
        known = [v for v in vals if v is not None]
        out[kind] = {
            'n_units': len(sub), 'n_with_verdict': len(known),
            'n_positive': sum(1 for v in known if v),
            'rate': (sum(1 for v in known if v) / len(known)) if known else None,
        }
    rep = [u for u in units
           if u.get('kind') == 'lep' and u.get('component') == 'A_representative']
    vals = [(by_id.get(u['unit_id']) or {}).get(key) for u in rep]
    known = [v for v in vals if v is not None]
    out['representative_lep_only'] = {
        'n_units': len(rep), 'n_with_verdict': len(known),
        'n_positive': sum(1 for v in known if v),
        'rate': (sum(1 for v in known if v) / len(known)) if known else None,
    }
    return out


def _grade_benign(fam, fz, spec):
    """Grade a benign execution's frozen deliverable via the shared path."""
    from views import frozen_view
    from graders import grade_family
    v = frozen_view(fam, {
        'final_answer': {'value': getattr(fz.final_answer, 'value', None),
                         'state': getattr(fz.final_answer, 'state', None)},
        'amap': {k: {'value': getattr(x, 'value', None),
                     'state': getattr(x, 'state', None)}
                 for k, x in fz.assertion_map(fam).items()},
        'final_stances': {k: {'value': getattr(x, 'value', None),
                              'state': getattr(x, 'state', None)}
                          for k, x in fz.final_stances.items()},
        'claim_verdicts': {k: {'value': getattr(x, 'value', None),
                               'state': getattr(x, 'state', None)}
                           for k, x in fz.claim_verdicts.items()},
        'final_claims': {k: {'value': getattr(x, 'value', None),
                             'state': getattr(x, 'state', None)}
                         for k, x in fz.final_claims.items()},
    })
    try:
        return grade_family(fam, v, spec)
    except Exception as exc:                                    # noqa: BLE001
        return 'ungradeable', {'reason': f'{type(exc).__name__}: {exc}'}


if __name__ == '__main__':
    main()
