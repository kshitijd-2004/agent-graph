#!/usr/bin/env python3.11
"""Stage the FOUR BND V4.1 input sources into a NEW merged trace dir (v2).

Read-only with respect to every source: traces are symlinked, never copied.
Extends the original bnd_v41_cw_swe200_provisional staging (which covered
consolidated + cw_regen + swe_200) with the now-completed bvrl-regen
(job 3600260, production_run/bv_rl_regeneration_v2) as the HIGHEST-priority
source, under the same locked dedup rule as the original staging:

  * LEP cells (fixture, topology, lep_code, mode, repetition): the newest
    source holding the cell wins (bvrl > swe_200 > cw_regen >
    consolidated); all of the winner's attempts are kept, losers are
    excluded duplicates.
  * Benign cells (fixture, topology, execution_variant, repetition): same
    rule. Fresh bvrl reps (rep indices above every existing pool rep) ADD
    to pools without displacing existing files, so they can lift bv/rl
    cells from <3 same-shape references to >=3.
  * Gates: legacy consolidated = frozen-faithful (no admission gate,
    rejected_traces/ never read); the three regeneration sources
    = standard + admission.status == admitted only.

Writes ONLY into analysis/downstream_propagation_corrected/staged_v2/.
"""
from __future__ import annotations
import json, os
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path('/u/kdhande/agent-graph/agent-graph-v3')
OUT = ROOT / 'analysis' / 'downstream_propagation_corrected' / 'staged_v2'
CONSD = ROOT / 'analysis' / 'full_production_consolidated' / 'source'
CWREG = ROOT / 'production_run' / 'coordinator_workers_regeneration'
SWE200 = ROOT / 'production_runs' / 'swebench_200_addition'
BVRL = ROOT / 'production_run' / 'bv_rl_regeneration_v2'


def walk_traces(root: Path):
    for dirpath, _dn, fnames in os.walk(root):
        if os.path.basename(dirpath) != 'traces':
            continue
        for fn in fnames:
            if fn.endswith('_trace.json'):
                yield Path(dirpath) / fn


class Rec:
    __slots__ = ('path', 'src', 'meta')
    def __init__(self, path, src):
        self.path = path
        self.src = src
        self.meta = None


def scan(root: Path, src: str, legacy: bool = False):
    recs, rejected = [], Counter()
    for f in walk_traces(root):
        try:
            m = json.loads(f.read_text())['metadata']
        except Exception as exc:
            rejected[f'unparseable:{type(exc).__name__}'] += 1
            continue
        if m.get('execution_variant', 'standard') != 'standard':
            rejected['not_standard_variant'] += 1
            continue
        cond = m.get('condition')
        if cond not in ('single_lep', 'benign', 'counterfactual'):
            rejected[f'skip_condition:{cond}'] += 1
            continue
        if not legacy:
            a = m.get('admission') or {}
            if a.get('status') != 'admitted':
                rejected[f"rejected:{a.get('reason') or 'unknown'}"] += 1
                continue
        r = Rec(f, src)
        r.meta = m
        recs.append(r)
    return recs, rejected


def lep_key(r):
    m = r.meta
    return (m.get('fixture_id'), m.get('topology'),
            (m.get('lep_codes') or [None])[0],
            m.get('propagation_mode', 'single_origin'),
            m.get('repetition_index'))

def benign_key(r):
    m = r.meta
    return (m.get('fixture_id'), m.get('topology'),
            m.get('execution_variant', 'standard'),
            m.get('repetition_index'))


def select(recs_by_src, priority, keyfn):
    by_key = defaultdict(lambda: defaultdict(list))
    for src, recs in recs_by_src.items():
        for r in recs:
            by_key[keyfn(r)][src].append(r)
    kept, decisions = [], []
    for key in sorted(by_key):
        avail = [s for s in priority if by_key[key].get(s)]
        winner = avail[0]
        losers = avail[1:]
        winners = by_key[key][winner]
        kept += [(r.path, winner) for r in winners]
        decisions.append({
            'key': key, 'winning_src': winner,
            'chosen_attempts': [r.meta.get('attempt_id') for r in winners],
            'excluded_duplicate_attempts': [
                {'src': s, 'attempt': r.meta.get('attempt_id')}
                for s in losers for r in by_key[key][s]],
        })
    return kept, decisions


def main():
    # The frozen discover() reads ``<source>/traces/``, so the merged dir is
    # OUT/merged/traces and the analyzer is invoked with --source OUT/merged.
    ddir = OUT / 'merged' / 'traces'
    ddir.mkdir(parents=True, exist_ok=True)
    for f in ddir.iterdir():
        f.unlink()

    cons, _ = scan(CONSD, 'consolidated', legacy=True)
    cw, _ = scan(CWREG, 'cw_regen')
    swe, _ = scan(SWE200, 'swe_200')
    bv, bv_rej = scan(BVRL, 'bvrl_regen')
    print(f'staged records: consolidated={len(cons)} cw_regen={len(cw)} '
          f'swe_200={len(swe)} bvrl_regen={len(bv)}')
    print(f'  bvrl_regen breakdown: {Counter(r.meta.get("condition") for r in bv)}')
    print(f'  bvrl_regen rejected (not staged): {dict(bv_rej)}')

    def split_kind(recs):
        leps = [r for r in recs if r.meta.get('condition') == 'single_lep']
        bens = [r for r in recs if r.meta.get('condition') in ('benign', 'counterfactual')]
        return leps, bens

    by_src = {}
    for src, recs in (('consolidated', cons), ('cw_regen', cw),
                      ('swe_200', swe), ('bvrl_regen', bv)):
        l, b = split_kind(recs)
        by_src[src] = (l, b)

    prio = ['bvrl_regen', 'swe_200', 'cw_regen', 'consolidated']
    lep_kept, lep_dec = select({s: by_src[s][0] for s in prio}, prio, lep_key)
    ben_kept, ben_dec = select({s: by_src[s][1] for s in prio}, prio, benign_key)

    used = set()
    for target, _src in lep_kept + ben_kept:
        n = target.name
        if n in used:
            stem, ext = os.path.splitext(n)
            i = 1
            while f'{stem}__dup{i}{ext}' in used:
                i += 1
            n = f'{stem}__dup{i}{ext}'
        (ddir / n).symlink_to(target.resolve())
        used.add(n)
    n_ex = sum(len(d['excluded_duplicate_attempts']) for d in lep_dec + ben_dec)

    prov = {
        'kind': 'run', 'run': 'merged_v2', 'priority': prio,
        'lep_cells': len(lep_dec), 'benign_cells': len(ben_dec),
        'lep_files_kept': len(lep_kept), 'benign_files_kept': len(ben_kept),
        'excluded_duplicate_attempts': n_ex,
        'lep_by_winner': dict(Counter(d['winning_src'] for d in lep_dec)),
        'benign_by_winner': dict(Counter(d['winning_src'] for d in ben_dec)),
        'bvrl_rejected_reported': dict(bv_rej),
    }
    with (OUT / 'provenance.jsonl').open('w') as fh:
        fh.write(json.dumps(prov, sort_keys=True, default=str) + '\n')
        for d in lep_dec:
            fh.write(json.dumps({'kind': 'lep_cell', **d}, sort_keys=True, default=str) + '\n')
        for d in ben_dec:
            fh.write(json.dumps({'kind': 'benign_cell', **d}, sort_keys=True, default=str) + '\n')
    print(f'merged: lep kept={len(lep_kept)} benign kept={len(ben_kept)} '
          f'files={len(used)} excluded dups={n_ex}')
    print(f'  lep winners: {prov["lep_by_winner"]}')
    print(f'  benign winners: {prov["benign_by_winner"]}')


if __name__ == '__main__':
    main()
