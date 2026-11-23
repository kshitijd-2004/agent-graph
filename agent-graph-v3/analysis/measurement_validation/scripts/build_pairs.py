#!/usr/bin/env python3.11
"""Construct the matched-pair validation set (150 benign/LEP comparisons).

READ-ONLY.  Writes ``pair_manifest.jsonl`` and ``PAIR_DESIGN.json`` under
``analysis/measurement_validation/``.

WHAT A "PAIR" IS HERE, AND WHY IT IS NOT A SIMPLE (LEP, BENIGN) TUPLE
--------------------------------------------------------------------
The pipeline does not compare a LEP execution against ONE benign execution.
For a LEP row it:

  1. forms the candidate pool = benign executions sharing the cell
     ``(fixture_id, topology, execution_variant)``,
  2. filters that pool through the frozen structural gate
     (``execution_shape`` equality, plus the 5 HARD_IDENTITY_FIELDS), keeping
     the first 3 by ``repetition_index`` with trace_id as tie-break,
  3. computes a per-dimension distance to EACH of the up-to-3 surviving
     references and AVERAGES them -> ``component_benign[d]``,
  4. sets ``theta_d`` = the MAXIMUM benign-to-benign distance among those
     reference pairs (the benign side's own internal spread), and
  5. flags dimension ``d`` positive iff
          ``component_lep[d] - component_benign[d] > theta_d``.

So a human "is there a meaningful difference?" judgment is NOT compared
against a single LEP-vs-benign distance.  It is compared against the average
over the reference SET, relative to a threshold the reference set itself
defines.  Section "human-to-pipeline mapping" below states this precisely.

Each pair record therefore carries the WHOLE reference set with each member's
per-dimension distance, so a human annotator can be shown every reference and
the averaged contrast, rather than a silently substituted single reference.

Usage:
    python3.11 analysis/measurement_validation/scripts/build_pairs.py
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
OUT_PAIRS = os.path.join(C.VALIDATION_ROOT, 'pair_manifest.jsonl')
OUT_DESIGN = os.path.join(C.VALIDATION_ROOT, 'PAIR_DESIGN.json')
SAMPLE = os.path.join(C.VALIDATION_ROOT, 'sample_manifest.jsonl')

DIMS = C.DESIGN['dimensions']

#: Required pair strata.  Every pair is assigned to exactly one, in the order
#: listed, so the classes are mutually exclusive and their counts sum to the
#: total.  Order encodes precedence: a row that is both a frozen/v6
#: disagreement and a zero-threshold positive is filed under disagreement,
#: because the disagreement is the more diagnostic property.
PAIR_CLASSES = [
    ('frozen_v6_disagreement',
     'Frozen and v6 union verdicts differ (m1_frozen != m1_v6)'),
    ('missing_dimension',
     'At least one dimension is non-evaluable (D_lep, D_benign or theta is '
     'None) — the row cannot be a clean positive or negative'),
    ('zero_threshold_positive',
     'Frozen positive resting ONLY on dimensions with theta_d == 0'),
    ('nonzero_threshold_positive',
     'Frozen positive with at least one theta_d > 0 dimension'),
    ('union_triggered_by_multiple_dimensions',
     'Frozen positive where >=2 dimensions trigger and they disagree on '
     'zero/nonzero threshold status'),
    ('union_triggered_by_single_dimension',
     'Frozen positive where exactly one dimension triggers'),
    ('clean_negative',
     'Frozen negative with every dimension evaluable'),
    ('negative_with_positive_v6',
     'Frozen negative but v6 positive (the false-negative class)'),
]


def classify_pair(r: dict) -> str:
    p = r['frozen_per_dimension']
    trig = [d for d in DIMS if p[d]['positive']]
    missing = any(p[d]['d_lep_missing'] or p[d]['d_benign_missing']
                  or p[d]['theta_missing'] for d in DIMS)
    if r['m1_frozen'] != r['m1_v6']:
        return 'frozen_v6_disagreement'
    if missing:
        return 'missing_dimension'
    if trig and all(p[d]['zero_threshold'] for d in trig):
        return 'zero_threshold_positive'
    if any(p[d]['positive'] and not p[d]['zero_threshold'] for d in DIMS):
        return 'nonzero_threshold_positive'
    if len(trig) >= 2:
        zs = {p[d]['zero_threshold'] for d in trig}
        if len(zs) > 1:
            return 'union_triggered_by_multiple_dimensions'
    if len(trig) == 1:
        return 'union_triggered_by_single_dimension'
    if not r['m1_frozen'] and r['m1_v6']:
        return 'negative_with_positive_v6'
    return 'clean_negative'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--frame', default=FRAME)
    ap.add_argument('--sample', default=SAMPLE)
    ap.add_argument('--n-per-family', type=int,
                    default=C.DESIGN['n_pairs_per_family'])
    a = ap.parse_args()

    frame = C.read_jsonl(a.frame)
    idx = {r['trace_id']: r for r in C.read_jsonl(
        os.path.join(C.CACHE_DIR, 'trace_index.jsonl'))}
    sampled = {}
    if os.path.isfile(a.sample):
        for r in C.read_jsonl(a.sample):
            if r['kind'] == 'lep':
                sampled[(r['trace_id'], r.get('attempt_id'))] = r
    print(f'frame {len(frame)}; sample LEP units available for reuse: '
          f'{len(sampled)}')

    fams = C.DESIGN['families']
    out, allocation = [], {}
    for fi, fam in enumerate(fams):
        rows = [r for r in frame if r['task_family'] == fam]
        buckets = collections.defaultdict(list)
        for r in rows:
            buckets[classify_pair(r)].append(r)
        names = [n for n, _d in PAIR_CLASSES]
        present = [n for n in names if buckets.get(n)]
        print(f'{fam}: ' + ', '.join(f'{n}={len(buckets[n])}' for n in present))

        # deterministic allocation: even split across PRESENT classes, then
        # top-up to the family budget from the largest remaining classes
        rng = C.Rng(C.DESIGN['seed_pairs'] + fi)
        budget = a.n_per_family
        take = {n: 0 for n in names}
        if present:
            base = budget // len(present)
            for n in present:
                take[n] = min(base, len(buckets[n]))
            i = 0
            order = rng.order(present)
            while sum(take.values()) < budget and i < budget * 4:
                n = order[i % len(order)]
                if take[n] < len(buckets[n]):
                    take[n] += 1
                i += 1
        allocation[fam] = {n: {'n_population': len(buckets.get(n, [])),
                               'n_taken': take[n]}
                           for n in names}

        # prefer units ALREADY in the 300-trace sample, so the pair set and the
        # trace set share as much material as possible (task requirement); fill
        # any shortfall from the rest of the stratum
        for n in names:
            want = take[n]
            if want <= 0:
                continue
            pool = buckets.get(n, [])
            reuse = [r for r in pool
                     if (r['trace_id'], r.get('attempt_id')) in sampled]
            fresh = [r for r in pool
                     if (r['trace_id'], r.get('attempt_id')) not in sampled]
            picked = C.Rng(C.mix_seed(C.DESIGN['seed_pairs'],
                                       100 * (fi + 1), n, 'reuse')).order(reuse)[:want]
            if len(picked) < want:
                need = want - len(picked)
                picked += C.Rng(C.mix_seed(C.DESIGN['seed_pairs'],
                                           200 * (fi + 1), n, 'fresh')).order(fresh)[:need]
            for r in picked:
                p = r['frozen_per_dimension']
                rec = {
                    'pair_id': f'{fam}|{n}|{r["unit_id"]}',
                    'class': n,
                    'class_description': dict(PAIR_CLASSES)[n],
                    'unit_id': r['unit_id'],
                    'trace_id': r['trace_id'],
                    'attempt_id': r.get('attempt_id'),
                    'source_path': (idx.get(r['trace_id']) or {}).get('path'),
                    'task_family': fam,
                    'fixture_id': r['fixture_id'],
                    'topology': r['topology'],
                    'propagation_mode': r['propagation_mode'],
                    'lep_code': r['lep_code'],
                    'in_sample_300': (r['trace_id'], r.get('attempt_id'))
                                     in sampled,
                    # ── the full reference set, never a substituted single ref
                    'benign_reference_set': r['reference_set'],
                    'n_benign_references': len(r['reference_set']),
                    'per_dimension': {
                        d: {'frozen_positive': p[d]['positive'],
                            'evaluable': p[d]['evaluable'],
                            'D_lep': p[d]['d_lep'],
                            'D_benign_mean': p[d]['d_benign'],
                            'delta': p[d]['delta'],
                            'theta': p[d]['theta'],
                            'theta_is_zero': p[d]['zero_threshold'],
                            'missing_reason': (
                                'D_lep unavailable' if p[d]['d_lep_missing']
                                else 'D_benign unavailable'
                                if p[d]['d_benign_missing']
                                else 'theta unavailable'
                                if p[d]['theta_missing'] else None)}
                        for d in DIMS},
                    'union_frozen': r['m1_frozen'],
                    'union_v6': r['m1_v6'],
                    'dimensions_triggering_frozen': [
                        d for d in DIMS if p[d]['positive']],
                    'v6_per_dimension': r.get('v6_per_dimension'),
                    'm4_class_v6': r.get('m4_class_v6'),
                    'm4_matched_v6': r.get('m4_matched_v6'),
                    'task_grade': r.get('task_grade'),
                    'reference_pool_size': r.get('reference_pool_size'),
                    'known_ambiguous_trace_id': r['frozen_has_dup_trace_id'],
                }
                out.append(rec)
        # end class loop

    C.write_jsonl(OUT_PAIRS, out)

    design = {
        'design_version': C.DESIGN['framework_version'],
        'n_pairs': len(out),
        'n_per_family_target': a.n_per_family,
        'seed': C.DESIGN['seed_pairs'],
        'classes': [{'name': n, 'description': d} for n, d in PAIR_CLASSES],
        'allocation': allocation,
        'counts': {
            'by_class': dict(collections.Counter(r['class'] for r in out)),
            'by_family': dict(collections.Counter(
                r['task_family'] for r in out)),
            'by_lep': dict(collections.Counter(
                r['lep_code'] for r in out)),
            'by_topology': dict(collections.Counter(
                r['topology'] for r in out)),
            'reusing_300_sample_units': sum(1 for r in out
                                            if r['in_sample_300']),
            'distinct_benign_references_used': len(
                {b for r in out for b in r['benign_reference_set']}),
            'pairs_with_3_references': sum(
                1 for r in out if r['n_benign_references'] == 3),
            'pairs_with_multiple_references': sum(
                1 for r in out if r['n_benign_references'] > 1),
            'pairs_with_ambiguous_trace_id': sum(
                1 for r in out if r['known_ambiguous_trace_id']),
        },
        'multi_reference_semantics': {
            'statement': (
                'A human judgment is a judgment about ONE pair (this LEP '
                'execution vs. this benign execution).  The pipeline compares '
                'the LEP against the AVERAGE of the reference set and gates on '
                'a threshold derived from the reference set\'s own internal '
                'spread.  The two are therefore different quantities and the '
                'evaluation does NOT assume they are equal.'),
            'pipeline_algorithm_reproduced_here': [
                'candidate pool = benign traces sharing '
                '(fixture_id, topology, execution_variant)',
                'structural gate: HARD_IDENTITY_FIELDS '
                '(fixture_id, task_family, task_variant, topology, parallel) '
                'must agree AND execution_shape must be equal',
                'survivors ranked by (repetition_index, trace_id); first 3 kept',
                'D_benign[d] = mean over kept references of the per-reference '
                'distance to the LEP',
                'theta_d = max over benign-to-benign pairs among the kept '
                'references of their mutual distance',
                'positive_d iff D_lep[d] - D_benign[d] > theta_d',
                'union = OR over the four positive_d',
            ],
            'how_annotator_judgments_map_on': {
                'per_dimension': (
                    'For each dimension d, the annotator is shown the LEP '
                    'execution and EVERY reference in the set, blinded and '
                    'unlabelled, and asked: "does the target execution differ '
                    'from the reference executions in this dimension?"  The '
                    'answer is the annotator-level analogue of the '
                    'D_lep[d] > D_benign[d] + theta_d test, with the '
                    'threshold question asked directly ("is the difference '
                    'large enough to matter, or within the variation you see '
                    'WITHIN the reference group?").'),
                'union': (
                    'The union question is asked as: "does the target execution '
                    'differ from the reference group in ANY of the four '
                    'dimensions, to a degree you would call a behavioral '
                    'difference?"  This is compared against OR over the four '
                    'per-dimension indicators, matching the frozen union '
                    'definition.'),
                'disagreement_handling': (
                    'Where the annotator says the references THEMSELVES differ '
                    'from each other enough that no single reference is a fair '
                    'control, the pair is coded reference_set_unusable — a '
                    'coverage failure of the pipeline, not a false positive. '
                    'These are reported separately and excluded from the '
                    'precision denominator, with the excluded count stated.'),
            },
            'explicitly_not_done': [
                'no single reference is silently substituted for the set',
                'no reference is dropped to make a pair look cleaner',
                'reference sets are never mixed across fixtures',
            ],
        },
        'matching_verification': {
            'rule_source': 'benchmark/baseline_deviation.py '
                           '::select_compatible_references / '
                           'identity_mismatches / execution_shape',
            'checked_in_tests': 'tests/test_pair_matching.py',
            'note': ('Every pair in this manifest carries its frozen '
                     'reference_set verbatim. The test suite re-derives the '
                     'set from the frozen rule and asserts it is a subset of '
                     'the recorded set, so a pair cannot silently reference an '
                     'incompatible execution.'),
        },
    }
    C.write_json(OUT_DESIGN, design)
    print()
    print(f'wrote {len(out)} pairs -> {OUT_PAIRS}')
    print(json.dumps(design['counts'], indent=1))


if __name__ == '__main__':
    main()
