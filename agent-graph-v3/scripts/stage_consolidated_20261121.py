#!/usr/bin/env python3.11
"""Stage the consolidated AgentProp dataset for BND V4.1 + downstream analysis.

Read-only with respect to the consolidated source: symlinks only, never copy.
Applies the locked dedup rule:
  * LEP cells (fixture, topology, lep_code, propagation_mode, repetition_index):
    the highest-priority source wins; ALL of the winner's attempts are kept
    (intended repetitions preserved); lower-priority attempts for the same
    cell are excluded duplicates.
  * Benign cells (fixture, topology, execution_variant, repetition_index):
    same rule.

The consolidated dataset is a single source directory, so dedup is purely
within-source: same execution_id or same trace hash within a cell.

Outputs:
  analysis/consolidated_20261121_bnd_v41/staged/merged/traces/
  analysis/consolidated_20261121_bnd_v41/provenance.jsonl
"""
from __future__ import annotations
import json, os, hashlib
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path('/u/kdhande/agent-graph/agent-graph-v3')
OUT = ROOT / 'analysis' / 'consolidated_20261121_bnd_v41'
SOURCE = Path('/projects/biha/agentprop_consolidated_20261121/traces')


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scan():
    """Scan the consolidated source, returning (benign_recs, lep_recs, rejected_counts).

    Gates:
      * execution_variant == standard (all traces already are, but verify)
      * condition in {single_lep, benign, counterfactual}
      * NO admission_status gate (the consolidated source predates that contract,
        matching the legacy frozen pipeline convention)
      * NO memory_enabled exclusion here (the --exclude-lep LEP_MEMORY_POISONING
        flag on the BND analyzer handles memory LEPs)
    """
    recs = []
    rejected = Counter()
    lep_by_exec_id = defaultdict(list)
    benign_by_exec_id = defaultdict(list)

    for f in sorted(SOURCE.glob('*_trace.json')):
        try:
            t = json.loads(f.read_bytes())
            m = t['metadata']
        except Exception as exc:
            rejected[f'unparseable:{type(exc).__name__}'] += 1
            continue

        # Gate: execution variant
        if m.get('execution_variant', 'standard') != 'standard':
            rejected['not_standard_variant'] += 1
            continue

        # Gate: condition
        cond = m.get('condition')
        if cond not in ('single_lep', 'benign', 'counterfactual'):
            rejected[f'skip_condition:{cond}'] += 1
            continue

        # Collect identity fields
        fixture = m.get('fixture_id')
        topology = m.get('topology')
        exec_variant = m.get('execution_variant', 'standard')
        rep_index = m.get('repetition_index')
        attempt_id = m.get('attempt_id')
        lep_codes = m.get('lep_codes') or []
        lep_code = lep_codes[0] if lep_codes else None
        propagation_mode = m.get('propagation_mode', 'single_origin')
        task_family = m.get('task_family')
        trace_id = t.get('trace_id', '')

        # Track execution_id for dedup within cells
        if cond == 'single_lep':
            lep_key = (fixture, topology, lep_code, propagation_mode, rep_index)
            lep_by_exec_id[lep_key].append({
                'path': f, 'trace_id': trace_id, 'attempt_id': attempt_id,
                'fixture': fixture, 'topology': topology,
                'task_family': task_family, 'lep_code': lep_code,
                'propagation_mode': propagation_mode,
                'execution_variant': exec_variant,
            })
        else:
            ben_key = (fixture, topology, exec_variant, rep_index)
            benign_by_exec_id[ben_key].append({
                'path': f, 'trace_id': trace_id, 'attempt_id': attempt_id,
                'fixture': fixture, 'topology': topology,
                'task_family': task_family,
                'execution_variant': exec_variant,
            })

    return lep_by_exec_id, benign_by_exec_id, rejected


def dedup_cell(records, key_field='attempt_id'):
    """Within a cell (same fixture/topology/.../rep_index), dedup by key_field.

    Per the existing convention: when multiple attempts share the same key_field
    value, they are exact duplicates and only one is kept.  Distinct key_field
    values are distinct legitimate repetitions and all are kept.
    """
    seen_keys = set()
    kept = []
    dups = 0
    for r in sorted(records, key=lambda x: x.get('trace_id', '')):
        k = r.get(key_field)
        if k in seen_keys:
            dups += 1
            continue
        seen_keys.add(k)
        kept.append(r)
    return kept, dups


def main():
    ddir = OUT / 'staged' / 'merged' / 'traces'
    ddir.mkdir(parents=True, exist_ok=True)
    # Clean any previous staging
    for f in ddir.iterdir():
        if f.is_symlink() or f.is_file():
            f.unlink()

    lep_by_key, benign_by_key, rejected = scan()

    # Dedup within each cell by attempt_id
    lep_kept = []
    lep_dup_total = 0
    lep_decisions = []
    for key in sorted(lep_by_key):
        recs = lep_by_key[key]
        kept, dups = dedup_cell(recs)
        lep_dup_total += dups
        for r in kept:
            lep_kept.append(r)
        if dups:
            lep_decisions.append({
                'key': key,
                'kept_attempts': [r['attempt_id'] for r in kept],
                'excluded_duplicate_attempts': [
                    r['attempt_id'] for r in recs if r['attempt_id']
                    not in {x['attempt_id'] for x in kept}
                ],
                'duplicate_count': dups,
            })

    benign_kept = []
    benign_dup_total = 0
    benign_decisions = []
    for key in sorted(benign_by_key):
        recs = benign_by_key[key]
        kept, dups = dedup_cell(recs)
        benign_dup_total += dups
        for r in kept:
            benign_kept.append(r)
        if dups:
            benign_decisions.append({
                'key': key,
                'kept_attempts': [r['attempt_id'] for r in kept],
                'excluded_duplicate_attempts': [
                    r['attempt_id'] for r in recs if r['attempt_id']
                    not in {x['attempt_id'] for x in kept}
                ],
                'duplicate_count': dups,
            })

    # Create symlinks
    used = set()
    for r in lep_kept + benign_kept:
        n = r['path'].name
        if n in used:
            stem, ext = os.path.splitext(n)
            i = 1
            while f'{stem}__dup{i}{ext}' in used:
                i += 1
            n = f'{stem}__dup{i}{ext}'
        (ddir / n).symlink_to(r['path'].resolve())
        used.add(n)

    # Summary statistics
    lep_cells = len(lep_by_key)
    benign_cells = len(benign_by_key)
    lep_total_raw = sum(len(v) for v in lep_by_key.values())
    benign_total_raw = sum(len(v) for v in benign_by_key.values())

    # LEP breakdown by code
    lep_code_dist = Counter()
    lep_topo_dist = Counter()
    lep_mode_dist = Counter()
    lep_fam_dist = Counter()
    for key in lep_by_key:
        lep_code_dist[key[2]] += len(lep_by_key[key])
        lep_topo_dist[key[1]] += len(lep_by_key[key])
        lep_mode_dist[key[3]] += len(lep_by_key[key])

    # Benign breakdown
    benign_topo_dist = Counter()
    benign_fam_dist = Counter()
    for key in benign_by_key:
        benign_topo_dist[key[1]] += len(benign_by_key[key])

    # Complete cells (>=3 distinct benign reps)
    complete_cells = sum(1 for v in benign_by_key.values()
                         if len({r['attempt_id'] for r in v}) >= 3)

    prov = {
        'kind': 'run',
        'run': 'consolidated_20261121_staging',
        'source': str(SOURCE),
        'lep_cells': lep_cells,
        'benign_cells': benign_cells,
        'lep_raw_total': lep_total_raw,
        'benign_raw_total': benign_total_raw,
        'lep_dedup_removed': lep_dup_total,
        'benign_dedup_removed': benign_dup_total,
        'lep_staged': len(lep_kept),
        'benign_staged': len(benign_kept),
        'total_staged': len(used),
        'complete_benign_cells_3plus': complete_cells,
        'rejected_at_scan': dict(rejected),
        'lep_by_code': dict(lep_code_dist),
        'lep_by_topology': dict(lep_topo_dist),
        'lep_by_mode': dict(lep_mode_dist),
        'benign_by_topology': dict(benign_topo_dist),
        'dedup_rules': (
            'Within-cell dedup by attempt_id: distinct attempt_ids are distinct '
            'legitimate repetitions; identical attempt_ids are exact duplicates.'
        ),
    }

    with (OUT / 'provenance.jsonl').open('w') as fh:
        fh.write(json.dumps(prov, sort_keys=True, default=str) + '\n')
        for d in lep_decisions:
            fh.write(json.dumps({'kind': 'lep_dedup', **d},
                                sort_keys=True, default=str) + '\n')
        for d in benign_decisions:
            fh.write(json.dumps({'kind': 'benign_dedup', **d},
                                sort_keys=True, default=str) + '\n')

    print(f'=== STAGING COMPLETE ===')
    print(f'Source: {SOURCE}')
    print(f'Output: {ddir}')
    print(f'')
    print(f'Raw trace counts:')
    print(f'  LEP cells: {lep_cells} ({lep_total_raw} raw files)')
    print(f'  Benign cells: {benign_cells} ({benign_total_raw} raw files)')
    print(f'  Dedup removed: {lep_dup_total} LEP + {benign_dup_total} benign')
    print(f'  Staged: {len(lep_kept)} LEP + {len(benign_kept)} benign = {len(used)} total')
    print(f'  Complete benign cells (>=3 reps): {complete_cells}')
    print(f'')
    print(f'LEP by code: {dict(lep_code_dist)}')
    print(f'LEP by topology: {dict(lep_topo_dist)}')
    print(f'LEP by mode: {dict(lep_mode_dist)}')
    print(f'')
    print(f'Rejected at scan: {dict(rejected)}')
    print(f'Provenance: {OUT / "provenance.jsonl"}')


if __name__ == '__main__':
    main()
