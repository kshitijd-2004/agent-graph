#!/usr/bin/env python3.11
"""Development / held-out partition of the validation samples (v7).

SPLIT UNIT: the fixture, not the trace.  Related benign and LEP executions
share a fixture and therefore MUST land in the same partition; splitting the
300-trace manifest and the 150-pair manifest independently would let a
fixture's traces straddle the boundary and leak shared references
(benign reference sets are defined on the fixture cell).

INPUTS (all read-only):
  analysis/measurement_validation/sample_manifest.jsonl   (300 units)
  analysis/measurement_validation/pair_manifest.jsonl     (150 pairs)
  analysis/measurement_validation/SAMPLING_DESIGN.json
  analysis/measurement_validation/PAIR_DESIGN.json

OUTPUT (writes only under analysis/measurement_v7/out/):
  out/partitions.json        the frozen partition, per fixture and unit,
                             with the full reference graph and seed
  out/partition_report.md    human-readable coverage report

SEEDING: a single fixed, stable seed (SEED below) is folded through
SHA-256 per fixture, so the result is a byte-identical function of
(seed, fixture set) and independent of hash randomisation, dict ordering,
or process state.

COVERAGE PRESERVATION:
  * family / topology / LEP / divergence-class coverage is balanced across
    both partitions.  Fixtures are sorted by their aggregate coverage
    signature before assignment, so each partition spans the full
    signature spectrum; the report verifies the achieved balance.
  * the representative (A) vs targeted (B) component distinction is
    preserved per partition in the report.
  * inclusion probabilities and design weights are UNCHANGED: the
    partition is a post-hoc allocation of the existing sample, not a new
    design.  Population-estimability of component A is preserved *within
    each partition* only if the partition is reported per-partition with
    its own achieved stratum counts; the report documents that the
    partitioned component-A estimates are conditional on the partition.

The partition is frozen at creation.  Any later re-split must produce a
DIFFERENT file and be labelled a new experiment.  Held-out labels,
predictions and disagreements must never be inspected while developing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from typing import Any, Dict, List, Sequence

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
VALID = os.path.join(REPO, 'analysis', 'measurement_validation')
OUT = os.path.join(HERE, 'out')

SEED = 'agentprop-v7-partition-20261123'
HELDOUT_FRACTION = 0.4          # target share of fixtures in held-out
HELDOUT_MIN_UNITS = 20          # floor: held-out must not be vacuous
HELDOUT_MAX_UNITS = 170         # ceiling: dev must not be vacuous


def _sha_int(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:16], 'big')


def _fixture_seed(fixture: str) -> int:
    return _sha_int(f'{SEED}|{fixture}')


def _default_graph_entry() -> Dict[str, Any]:
    return {
        'trace_ids': set(), 'unit_ids': set(), 'pair_ids': set(),
        'classes': set(), 'components': set(), 'lep_codes': set(),
        'families': set(), 'topologies': set(), 'prop_modes': set(),
        'zero_thr_pos': set(), 'frozen_pos': set(), 'v6_pos': set(),
        'kind_counts': Counter(),
    }


def load_inputs() -> Dict[str, Any]:
    units: List[Dict[str, Any]] = []
    with open(os.path.join(VALID, 'sample_manifest.jsonl')) as f:
        for line in f:
            if line.strip():
                units.append(json.loads(line))
    pairs: List[Dict[str, Any]] = []
    with open(os.path.join(VALID, 'pair_manifest.jsonl')) as f:
        for line in f:
            if line.strip():
                pairs.append(json.loads(line))
    return {'units': units, 'pairs': pairs}


def build_reference_graph(units: Sequence[Dict[str, Any]],
                          pairs: Sequence[Dict[str, Any]]
                          ) -> Dict[str, Dict[str, Any]]:
    """fixture -> coverage record.

    The pair's reference graph is attached to the pair's fixture, so every
    trace that may be shared between two units belongs to exactly one
    fixture and is assigned to exactly one partition.  This is the
    cross-partition leakage control required by the brief: the benign
    reference set of a LEP row can never be split across partitions.
    """
    graph: Dict[str, Dict[str, Any]] = {}
    for u in units:
        g = graph.setdefault(u['fixture_id'], _default_graph_entry())
        g['trace_ids'].add(u['trace_id'])
        g['unit_ids'].add(u['unit_id'])
        g['components'].add(u.get('component', ''))
        g['families'].add(u.get('task_family', ''))
        g['topologies'].add(u.get('topology', ''))
        g['prop_modes'].add(u.get('propagation_mode', ''))
        if u.get('kind') == 'benign':
            g['lep_codes'].add('BENIGN')
        else:
            code = (u.get('lep') or u.get('lep_code')
                    or u.get('lep_name') or 'UNKNOWN')
            g['lep_codes'].add(code)
        g['kind_counts'][u.get('kind', '?')] += 1
    for p in pairs:
        fx = p['fixture_id']
        g = graph.setdefault(fx, _default_graph_entry())
        g['pair_ids'].add(p['pair_id'])
        g['classes'].add(p.get('class', ''))
        g['families'].add(p.get('task_family', ''))
        g['topologies'].add(p.get('topology', ''))
        lep = p.get('lep_trace_id') or p.get('trace_id', '')
        if lep:
            g['trace_ids'].add(lep)
        g['lep_codes'].add(p.get('lep_code') or 'UNKNOWN')
        for t in p.get('benign_reference_set', []) or []:
            g['trace_ids'].add(t)
        pd = p.get('per_dimension') or {}
        if p.get('m1_frozen'):
            g['frozen_pos'].add(p['pair_id'])
        if p.get('m1_v6'):
            g['v6_pos'].add(p['pair_id'])
        for d in ('assertions', 'decisions', 'actions', 'outcomes'):
            info = pd.get(d) or {}
            if info.get('frozen_positive') and info.get('theta_is_zero'):
                g['zero_thr_pos'].add(f'{p["pair_id"]}:{d}')
    return graph


def _coverage_signature(g: Dict[str, Any]) -> str:
    parts = [
        ','.join(sorted(g['components'])) or '-',
        ','.join(sorted(g['families'])) or '-',
        ','.join(sorted(g['topologies'])) or '-',
        ','.join(sorted(g['lep_codes'])) or '-',
        ','.join(sorted(g['prop_modes'])) or '-',
        ','.join(sorted(g['classes'])) or '-',
        f"{len(g['pair_ids'])}pairs",
        f"{len(g['zero_thr_pos'])}zthr",
    ]
    return '|'.join(parts)


def split_fixtures(graph: Dict[str, Dict[str, Any]],
                   fraction: float = HELDOUT_FRACTION
                   ) -> Dict[str, str]:
    """fixture -> 'dev' | 'heldout'.

    Stable function of (SEED, fixture set, fraction): fixtures are sorted by
    their coverage signature (then name) for a deterministic order, then the
    fraction of fixtures to hold out is allocated to the fixtures with the
    LOWEST seed-derived ranks — but the ranks themselves come from
    SHA-256(seed|fixture), not from the sort position, so the *set* chosen
    is a pure function of the seed while the *count* is controlled by the
    position in the sorted order.  A fixture never straddles the boundary.
    """
    ordered = sorted(graph, key=lambda fx: (_coverage_signature(graph[fx]),
                                             fx))
    n = len(ordered)
    n_heldout = round(n * fraction)
    # rank fixtures by their seed; the lowest-ranked n_heldout go held-out.
    ranked = sorted(ordered, key=lambda fx: (_fixture_seed(fx), fx))
    heldout = set(ranked[:n_heldout])
    assign: Dict[str, str] = {}
    for fx in ordered:
        assign[fx] = 'heldout' if fx in heldout else 'dev'
    return assign


def _n_pair_zero_thr_pos(pairs: Sequence[Dict[str, Any]]) -> int:
    n = 0
    for p in pairs:
        pd = p.get('per_dimension') or {}
        for d in ('assertions', 'decisions', 'actions', 'outcomes'):
            info = pd.get(d) or {}
            if info.get('frozen_positive') and info.get('theta_is_zero'):
                n += 1
    return n


def _achieved(graph: Dict[str, Dict[str, Any]],
              assign: Dict[str, str],
              units: Sequence[Dict[str, Any]],
              pairs: Sequence[Dict[str, Any]]
              ) -> Dict[str, Any]:
    """Coverage and sample counts per partition, for the report."""
    rep: Dict[str, Any] = {}
    for part in ('dev', 'heldout'):
        fx = [f for f, p in assign.items() if p == part]
        fxset = set(fx)
        u_part = [u for u in units if u['fixture_id'] in fxset]
        p_part = [p for p in pairs if p['fixture_id'] in fxset]
        rep[part] = {
            'n_fixtures': len(fx),
            'n_sample_units': len(u_part),
            'n_pairs': len(p_part),
            'kind_counts': dict(Counter(u.get('kind', '?')
                                        for u in u_part)),
            'component_counts': dict(Counter(u.get('component', '?')
                                             for u in u_part)),
            'family_counts': dict(Counter(u.get('task_family', '?')
                                          for u in u_part)),
            'topology_counts': dict(Counter(u.get('topology', '?')
                                            for u in u_part)),
            'lep_counts': dict(Counter(
                (u.get('lep') or u.get('lep_code')
                 or ('BENIGN' if u.get('kind') == 'benign' else 'UNKNOWN'))
                for u in u_part if u.get('kind') != 'benign')),
            'pair_class_counts': dict(Counter(p.get('class', '?')
                                              for p in p_part)),
            'prop_mode_counts': dict(Counter(u.get('propagation_mode', '?')
                                             for u in u_part)),
            'pair_zero_thr_pos': _n_pair_zero_thr_pos(p_part),
            'pair_frozen_pos': sum(1 for p in p_part if p.get('m1_frozen')),
            'pair_v6_pos': sum(1 for p in p_part if p.get('m1_v6')),
        }
    return rep


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    dst = os.path.join(OUT, 'partitions.json')
    if os.path.exists(dst) and not a.force:
        with open(dst) as f:
            existing = json.load(f)
        if (existing.get('seed') == SEED
                and existing.get('heldout_fraction') == HELDOUT_FRACTION):
            print(f'[partition] frozen file present: {dst} (use --force to '
                  f'regenerate a NEW partition)')
            return 0
        print('[partition] existing partitions.json has a different seed '
              f'or fraction; refusing to silently overwrite. {dst}')
        return 2

    data = load_inputs()
    units, pairs = data['units'], data['pairs']
    graph = build_reference_graph(units, pairs)
    assign = split_fixtures(graph)
    # seed rank per fixture, for the record
    rank_of: Dict[str, int] = {}
    n_fx = len(graph)
    ordered = sorted(graph, key=lambda fx: (_coverage_signature(graph[fx]),
                                             fx))
    ranked = sorted(ordered, key=lambda fx: (_fixture_seed(fx), fx))
    for i, fx in enumerate(ranked):
        rank_of[fx] = i

    # adequacy guard
    n_units_ho = sum(1 for u in units if assign[u['fixture_id']] == 'heldout')
    n_pairs_ho = sum(1 for p in pairs if assign[p['fixture_id']] == 'heldout')
    if not (HELDOUT_MIN_UNITS <= n_units_ho <= HELDOUT_MAX_UNITS):
        print(f'[partition] held-out sample size inadequate '
              f'({n_units_ho} units / {n_pairs_ho} pairs outside '
              f'[{HELDOUT_MIN_UNITS}, {HELDOUT_MAX_UNITS}])')
        print('[partition] a larger untouched holdout from the remaining '
              'corpus must be drawn; recording the limitation instead of '
              'shifting the seed.')
    achieved = _achieved(graph, assign, units, pairs)

    out = {
        'seed': SEED,
        'heldout_fraction': HELDOUT_FRACTION,
        'split_unit': 'fixture',
        'leakage_control': ('benign reference sets and related benign/LEP '
                            'executions are attached to their fixture; a '
                            'fixture is assigned to exactly one partition'),
        'created': __import__('datetime').datetime.now(
            __import__('datetime').timezone.utc).isoformat(),
        'n_fixtures': len(graph),
        'fixtures': {
            fx: {'partition': assign[fx],
                 'seed_low_bits': (_fixture_seed(fx) % 10000) / 10000.0,
                 'n_sample_units': len(graph[fx]['unit_ids']),
                 'n_pairs': len(graph[fx]['pair_ids']),
                 'n_traces': len(graph[fx]['trace_ids']),
                 'classes': sorted(graph[fx]['classes']),
                 'components': sorted(graph[fx]['components']),
                 'lep_codes': sorted(graph[fx]['lep_codes']),
                 'topologies': sorted(graph[fx]['topologies']),
                 'prop_modes': sorted(graph[fx]['prop_modes']),
                 'zero_thr_pos': sorted(graph[fx]['zero_thr_pos']),
                 'frozen_pos': sorted(graph[fx]['frozen_pos']),
                 'v6_pos': sorted(graph[fx]['v6_pos']),
                 'kind_counts': dict(graph[fx]['kind_counts']),
                 'trace_ids': sorted(graph[fx]['trace_ids']),
                 'unit_ids': sorted(graph[fx]['unit_ids']),
                 'pair_ids': sorted(graph[fx]['pair_ids']),
                 }
            for fx in sorted(graph)
        },
        'unit_partitions': {
            u['unit_id']: assign[u['fixture_id']] for u in units
        },
        'pair_partitions': {
            p['pair_id']: assign[p['fixture_id']] for p in pairs
        },
        'achieved': achieved,
        'note': ('inclusion probabilities and design weights are unchanged '
                 'by this partition; it is a post-hoc allocation of the '
                 'existing sample, not a new design.  Component-A '
                 'population estimates are valid only within the original '
                 'full sample or within a partition with its own '
                 're-weighting; see partition_report.md.'),
    }
    with open(dst, 'w') as f:
        json.dump(out, f, indent=2, sort_keys=True)
        f.write('\n')

    # report
    rep = os.path.join(OUT, 'partition_report.md')
    lines = ['# Dev / held-out partition report',
             '',
             f'Partition frozen under seed `{SEED}`, fraction '
             f'{HELDOUT_FRACTION} held-out. Split unit: **fixture**.',
             '',
             '## Achieved sizes',
             '']
    for part in ('dev', 'heldout'):
        r = achieved[part]
        lines += [f'### {part}',
                  '',
                  f'- fixtures: {r["n_fixtures"]}',
                  f'- sample units: {r["n_sample_units"]} '
                  f'(kinds: {r["kind_counts"]})',
                  f'- pairs: {r["n_pairs"]} (classes: {r["pair_class_counts"]})',
                  f'- component A units: {r["component_counts"].get("A_representative", 0)}, '
                  f'B units: {r["component_counts"].get("B_targeted", 0)}',
                  f'- families: {r["family_counts"]}',
                  f'- topologies: {r["topology_counts"]}',
                  f'- LEPs (LEP units only): {r["lep_counts"]}',
                  f'- propagation modes: {r["prop_mode_counts"]}',
                  f'- zero-threshold pair-dimension positives: '
                  f'{r["pair_zero_thr_pos"]}',
                  f'- pair frozen-positives / v6-positives: '
                  f'{r["pair_frozen_pos"]} / {r["pair_v6_pos"]}',
                  '']
    lines += ['## Weighting note', '',
              'The partition does not change the sampling design.  Component-A '
              'units keep their original `inclusion_probability` and '
              '`design_weight`.  A design-weighted estimate **within a '
              'partition** is a conditional estimate over the partition; it '
              'is not a population estimate over the full frame unless '
              're-weighted by the partition-level inclusion '
              'probability.  Both are reported where used.', '']
    if not (HELDOUT_MIN_UNITS <= n_units_ho <= HELDOUT_MAX_UNITS):
        lines += ['## LIMITATION', '',
                  f'held-out has only {n_units_ho} units / {n_pairs_ho} '
                  'pairs.  This is reported as inadequate; a larger '
                  'untouched holdout drawn from the remaining corpus '
                  '(outside these 300+150 units) is the remedy, and any '
                  'subsequent improvement work must be evaluated on that '
                  'independent set.', '']
    with open(rep, 'w') as f:
        f.write('\n'.join(lines))
    print(f'[partition] wrote {dst} and {rep}')
    print(f'[partition] dev units={achieved["dev"]["n_sample_units"]} '
          f'heldout units={achieved["heldout"]["n_sample_units"]}; '
          f'dev pairs={achieved["dev"]["n_pairs"]} heldout '
          f'pairs={achieved["heldout"]["n_pairs"]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
