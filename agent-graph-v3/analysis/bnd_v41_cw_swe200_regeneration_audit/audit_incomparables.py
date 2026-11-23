"""Read-only root-cause audit of the 7,783 incomparable BND V4.1 executions.

Re-imports the FROZEN functions (discover, select_compatible_references,
execution_shape, trace_cell) and re-discovers the SAME merged staged
population the frozen run used, then for every incomparable LEP:
  * recomputes the exact benign matching key + pool
  * re-runs the frozen shape filter to confirm the selected count
  * classifies WHY the references are missing / insufficient:
      1. no benign reps generated at all in the cell
      2. reps generated but structurally incompatible (mixed shapes)
      3. reps exist but dedup/exclusion removed them
      4. reps exist & compatible but <3
  and writes a machine-readable manifest of every missing benign
  configuration + a human-readable audit summary.

No external writes except into this audit directory. Read-only elsewhere.
"""
from __future__ import annotations
import json, sys, collections, os
from pathlib import Path

ROOT = Path('/u/kdhande/agent-graph/agent-graph-v3')
sys.path.insert(0, str(ROOT))
from benchmark import baseline_deviation as bd          # frozen module
from scripts.analyze_baseline_deviation import discover # frozen discover

OUT = ROOT / 'analysis' / 'bnd_v41_cw_swe200_regeneration_audit'
STAGED = ROOT / 'analysis' / 'bnd_v41_cw_swe200_provisional' / 'staged' / 'merged'

def shape(tr: dict) -> dict:
    return bd.execution_shape(tr)

def read(path: Path) -> dict:
    return json.loads(Path(path).read_text())

def main() -> None:
    references, lep_records, skipped = discover(STAGED)
    print(f'benign cells: {len(references)}, LEP traces: {len(lep_records)}, '
          f'skipped={skipped}', file=sys.stderr)

    # Apply the same population restriction the frozen run used:
    # --execution-variant standard, --exclude-lep LEP_MEMORY_POISONING
    lep_records = [r for r in lep_records
                   if r['execution_variant'] == 'standard'
                   and r['lep_code'] != 'LEP_MEMORY_POISONING']
    print(f'LEP population after restriction: {len(lep_records)}', file=sys.stderr)

    # Group cells the same way main() does: (fixture, topology, execution_variant)
    cells_by_key = collections.defaultdict(list)
    for rec in lep_records:
        cells_by_key[(rec['fixture'], rec['topology'], rec['execution_variant'])].append(rec)

    incomparable = []
    comparable = 0
    cell_stats = collections.defaultdict(lambda: {
        'pool_size': 0, 'pool_shapes': collections.Counter(),
        'lep_shapes': collections.Counter(), 'n_lep': 0})
    # Load each cell's pool once
    pools = {}
    for key in cells_by_key:
        ref_entry = references.get(key) or {'pool_paths': []}
        pools[key] = [read(p) for p in ref_entry.get('pool_paths', ref_entry.get('paths', []))]

    for key, recs in cells_by_key.items():
        pool = pools[key]
        cell = cell_stats[key]
        cell['pool_size'] = len(pool)
        cell['n_lep'] = len(recs)
        for pt in pool:
            cell['pool_shapes'][json.dumps(shape(pt), sort_keys=True)] += 1
        for rec in recs:
            lep = read(rec['path'])
            cell['lep_shapes'][json.dumps(shape(lep), sort_keys=True)] += 1
            refs = bd.select_compatible_references(lep, pool)
            rec = dict(rec)
            rec['pool_size'] = len(pool)
            rec['n_compatible'] = len(refs)
            rec['compatible_refs'] = [r.get('trace_id') for r in refs]
            rec['pool_shapes'] = dict(cell['pool_shapes'])
            rec['lep_shape'] = json.dumps(shape(lep), sort_keys=True)
            rec['pool_size_distinct_shapes'] = len(cell['pool_shapes'])
            if len(refs) < 3:
                incomparable.append(rec)
            else:
                comparable += 1

    print(f'comparable: {comparable}, incomparable: {len(incomparable)}', file=sys.stderr)

    # Classify
    def classify(rec):
        sels = rec['n_compatible']
        pool_shapes = rec['pool_size_distinct_shapes']
        if sels == 0 and pool_shapes >= 2:
            return 'mixed_shapes_no_solo_match'
        if sels == 0 and pool_shapes == 1 and rec['pool_size'] > 0:
            return 'structural_mismatch_same_shape'
        if rec['pool_size'] == 0:
            return 'no_benign_generated'
        # sels in 1..2
        return f'fewer_than_3_compatible_{sels}'
    for rec in incomparable:
        rec['root_cause'] = classify(rec)

    cause_counts = collections.Counter(r['root_cause'] for r in incomparable)
    print('root causes:', dict(cause_counts), file=sys.stderr)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'incomparable_manifest.jsonl').write_text(
        '\n'.join(json.dumps(r, sort_keys=True, default=str) for r in incomparable) + '\n')
    (OUT / 'root_cause_counts.json').write_text(json.dumps(
        dict(cause_counts), indent=2, sort_keys=True) + '\n')

    # Per-cell summary of what's missing
    cell_rows = []
    for key, st in cell_stats.items():
        recs_inc = [r for r in incomparable if (r['fixture'], r['topology'], r['execution_variant']) == key]
        if not recs_inc:
            continue
        need = 3
        have = max(r['n_compatible'] for r in recs_inc)
        missing = max(0, need - have)
        cell_rows.append({
            'cell': list(key),
            'n_lep_comparable': st['n_lep'] - len(recs_inc),
            'n_lep_incomparable': len(recs_inc),
            'pool_size': st['pool_size'],
            'distinct_shapes_in_pool': st['pool_size_distinct_shapes'],
            'pool_shape_counts': {k: v for k, v in st['pool_shapes'].items()},
            'max_compatible_refs_observed': have,
            'missing_refs_to_reach_3': missing,
            'root_causes': dict(collections.Counter(r['root_cause'] for r in recs_inc)),
        })
    cell_rows.sort(key=lambda r: (-r['n_lep_incomparable'], str(r['cell'])))
    (OUT / 'missing_cells.jsonl').write_text(
        '\n'.join(json.dumps(r, sort_keys=True, default=str) for r in cell_rows) + '\n')

    print(f'cells needing regen: {sum(1 for c in cell_rows if c["missing_refs_to_reach_3"] > 0)}',
          file=sys.stderr)

if __name__ == '__main__':
    main()
