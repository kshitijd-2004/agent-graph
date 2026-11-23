"""Build the exact-shape benign regeneration manifest (read-only audit).

Reuses the frozen `discover` + `select_compatible_references` + `execution_shape`
to recompute every incomparable LEP exactly as the frozen run did, groups by
exact execution shape, and emits:
  - regen_manifest.jsonl : one row per (cell, generable LEP shape) that needs
    N new benign executions, with exact regen config
  - unrecoverable_rows.jsonl : LEP rows no benign regen can fix
  - summary.json + AUDIT_SUMMARY.md
Writes only into this audit directory.
"""
from __future__ import annotations
import json, sys, collections
from pathlib import Path

ROOT = Path('/u/kdhande/agent-graph/agent-graph-v3')
sys.path.insert(0, str(ROOT))
from benchmark import baseline_deviation as bd
from scripts.analyze_baseline_deviation import discover

OUT = ROOT / 'analysis' / 'bnd_v41_cw_swe200_regeneration_audit'
STAGED = ROOT / 'analysis' / 'bnd_v41_cw_swe200_provisional' / 'staged' / 'merged'

def exact_shape(t: dict) -> dict:
    return bd.execution_shape(t)

def shape_sig(t: dict) -> str:
    s = exact_shape(t)
    return json.dumps(s, sort_keys=True)

def shape_kind(s: dict, topology: str) -> str:
    """Canonical-shape classification per topology (from observed census)."""
    trans = s['transfers']
    roots = s['initial_roles']
    if topology == 'coordinator_workers':
        fan = [1 for a, b in trans if a == 'coordinator' and b != 'coordinator']
        relay = [1 for a, b in trans
                 if a not in ('coordinator',) and b not in ('coordinator',)]
        if roots == ['coordinator'] and len(fan) >= 2 and not relay:
            return 'CW_FANOUT_canonical'
        if relay:
            return 'CW_RELAY_coord_first' if roots == ['coordinator'] else 'CW_RELAY_worker_first'
        return 'CW_OTHER'
    if topology == 'review_loop':
        # Canonical serial 2-stage chain: single initial role + 2 transfer edges
        if len(roots) == 1 and len(trans) == 2:
            return 'review_loop_serial_canonical'
        return 'review_loop_OTHER'
    # branch_and_verify: canonical = two parallel workers -> verifier
    if len(roots) == 2 and len(trans) == 2:
        return f'{topology}_serial_canonical'
    return f'{topology}_OTHER'

def generable(kind: str) -> bool:
    """Can the CURRENT generation code produce a benign trace of this shape?
    CW: yes for canonical FANOUT (commit 93985e5); NO for the two pre-fix
    RELAY recording variants. bv/rl: yes for the serial skeleton."""
    return kind in ('CW_FANOUT_canonical', 'branch_and_verify_serial_canonical',
                    'review_loop_serial_canonical')

def main() -> None:
    refs, lep, _ = discover(STAGED)
    lep = [r for r in lep if r['execution_variant'] == 'standard'
           and r['lep_code'] != 'LEP_MEMORY_POISONING']
    cells: dict[tuple, list] = collections.defaultdict(list)
    for rec in lep:
        cells[(rec['fixture'], rec['topology'], rec['execution_variant'])].append(rec)

    pools: dict[tuple, list[tuple[dict, str, str]]] = {}
    for key in cells:
        e = refs.get(key) or {'pool_paths': []}
        pp = e.get('pool_paths', e.get('paths', []))
        pools[key] = []
        for p in pp:
            t = json.loads(Path(p).read_text())
            pools[key].append((t, p, shape_kind(exact_shape(t), key[1])))

    comp_n = 0
    inc_rows: list[dict] = []
    for key, recs in cells.items():
        pool = pools[key]
        for rec in recs:
            t = json.loads(Path(rec['path']).read_text())
            sig = shape_sig(t)
            kind = shape_kind(exact_shape(t), key[1])
            sel = bd.select_compatible_references(t, [p[0] for p in pool])
            if len(sel) >= 3:
                comp_n += 1
                continue
            inc_rows.append({
                'fixture': key[0], 'topology': key[1], 'execution_variant': key[2],
                'lep_code': rec['lep_code'], 'propagation_mode': rec['propagation_mode'],
                'task_family': rec['task_family'], 'attempt_id': rec.get('attempt_id'),
                'lep_path': rec['path'],
                'lep_shape_sig': sig, 'lep_shape_kind': kind,
                'pool_size': len(pool),
                'pool_kind_counts': dict(collections.Counter(p[2] for p in pool)),
                'n_compatible': len(sel),
                'compatible_refs': [s.get('trace_id') for s in sel],
            })
    print(f'comparable={comp_n} incomparable={len(inc_rows)}', file=sys.stderr)
    assert comp_n == 10057 and len(inc_rows) == 7783, 'mismatch with frozen run'

    # Per (cell, generable-shape) regen demand: need 3 refs of that exact shape.
    cell_shape = collections.defaultdict(lambda: {
        'n_lep_inc': 0, 'lep_codes': set(), 'prop_modes': set(),
        'task_family': None, 'pool_size': 0, 'kind_counts': collections.Counter()})
    for r in inc_rows:
        k = (r['fixture'], r['topology'], r['execution_variant'], r['lep_shape_kind'])
        a = cell_shape[k]
        a['n_lep_inc'] += 1
        a['lep_codes'].add(r['lep_code'])
        a['prop_modes'].add(r['propagation_mode'])
        a['task_family'] = r['task_family']
        a['pool_size'] = r['pool_size']
        a['kind_counts'].update(r['pool_kind_counts'])
    have = {}  # (cell,kind) -> n pool members of that exact kind
    for key in {k[:3] for k in cell_shape}:
        for (t, _p, kind) in pools[key]:
            have[(key[0], key[1], key[2], kind)] = have.get((key[0], key[1], key[2], kind), 0) + 1

    regen_rows, unrec_rows = [], []
    for (fx, topo, ev, kind), a in sorted(cell_shape.items()):
        have_k = have.get((fx, topo, ev, kind), 0)
        if generable(kind):
            add = max(0, 3 - have_k)
            regen_rows.append({
                'cell': [fx, topo, ev], 'topology': topo, 'task_family': a['task_family'],
                'fixture': fx, 'target_shape_kind': kind,
                'target_shape_sig': next(r['lep_shape_sig'] for r in inc_rows
                                         if r['fixture'] == fx and r['topology'] == topo
                                         and r['lep_shape_kind'] == kind),
                'existing_same_shape_refs': have_k,
                'new_benign_executions': add,
                'lep_codes': sorted(a['lep_codes']),
                'prop_modes': sorted(a['prop_modes']),
                'n_incomparable_leps_served': a['n_lep_inc'],
                'pool_size': a['pool_size'],
                'regen_condition': 'benign', 'execution_variant': 'standard',
                'topology_target': None,
                'shape_note': ('canonical coordinator-first FANOUT skeleton '
                               '(commit 93985e5 generation code)'
                               if kind == 'CW_FANOUT_canonical'
                               else 'canonical serial skeleton'),
            })
        else:
            for r in inc_rows:
                if (r['fixture'], r['topology'], r['execution_variant'],
                        r['lep_shape_kind']) == (fx, topo, ev, kind):
                    unrec_rows.append({**r, 'unrecoverable_reason': (
                        'LEP records a pre-fix RELAY skeleton; current generation '
                        'code records only the canonical FANOUT skeleton, so no '
                        'newly generated benign can match. Recovery would require '
                        're-executing the LEP under post-fix code (population '
                        'decision, out of scope for benign-only regen).')})
    OUT.mkdir(parents=True, exist_ok=True)
    def dump(name, rows):
        with open(OUT / name, 'w') as fh:
            for r in rows:
                fh.write(json.dumps(r, sort_keys=True, default=str) + '\n')
    dump('regen_manifest.jsonl', regen_rows)
    dump('unrecoverable_rows.jsonl', unrec_rows)

    # Full per-LEP manifest: every incomparable LEP with exact matching key,
    # existing compatible refs, missing count, reason, and its regen config.
    per_lep = []
    for r in inc_rows:
        rec = {
            'fixture': r['fixture'], 'topology': r['topology'],
            'execution_variant': r['execution_variant'],
            'lep_code': r['lep_code'], 'propagation_mode': r['propagation_mode'],
            'task_family': r['task_family'], 'attempt_id': r.get('attempt_id'),
            'lep_source_path': r['lep_path'],
            'benign_matching_key': {
                'cell': [r['fixture'], r['topology'], r['execution_variant']],
                'note': ('frozen discover() pools benigns on '
                         '(fixture, topology, execution_variant) ONLY; '
                         'propagation mode is not a matching axis; the frozen '
                         'arbiter additionally requires identical '
                         'execution_shape (exact skeleton)'),
                'target_shape': r['lep_shape_sig'],
            },
            'pool_size_existing': r['pool_size'],
            'existing_compatible_refs': r['compatible_refs'],
            'missing_ref_count': max(0, 3 - r['n_compatible']),
            'reason': f"need_at_least_3_benign_references:{r['n_compatible']}"
                      + ('; pre-fix RELAY skeleton cannot be matched by any '
                         'newly generated benign (current code records the '
                         'canonical FANOUT skeleton only)'
                         if not generable(r['lep_shape_kind']) else ''),
            'recoverable': generable(r['lep_shape_kind']),
            'regen_config': ({
                'condition': 'benign',
                'topology': r['topology'],
                'fixture': r['fixture'],
                'task_family': r['task_family'],
                'execution_variant': 'standard',
                'target_exact_shape': r['lep_shape_sig'],
                'new_rep_indices': 'fresh indices above all existing pool reps',
                'n_new_benign_executions': 3 - r['n_compatible'],
            } if generable(r['lep_shape_kind']) else None),
        }
        per_lep.append(rec)
    dump('incomparable_leps.jsonl', per_lep)

    n_new = sum(r['new_benign_executions'] for r in regen_rows)
    n_rec_leps = sum(r['n_incomparable_leps_served'] for r in regen_rows
                     if r['new_benign_executions'] > 0 or
                        r['existing_same_shape_refs'] >= 3)
    n_unrec = len(unrec_rows)
    summary = {
        'verified_comparable_before': comp_n,
        'incomparable_total': len(inc_rows),
        'regen_configs': len(regen_rows),
        'regen_configs_requiring_new_executions': sum(
            1 for r in regen_rows if r['new_benign_executions'] > 0),
        'new_benign_executions_total': n_new,
        'incomparable_leps_recoverable_by_regen': n_rec_leps,
        'unrecoverable_leps': n_unrec,
        'expected_comparable_after': comp_n + n_rec_leps,
        'expected_comparability_after': round((comp_n + n_rec_leps) / (comp_n + len(inc_rows)), 4),
    }
    (OUT / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2), file=sys.stderr)

if __name__ == '__main__':
    main()
