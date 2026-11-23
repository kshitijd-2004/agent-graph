#!/usr/bin/env python3.11
"""Build a compact, cached metadata index over the consolidated trace corpus.

READ-ONLY over the corpus: the 26,965 files under
``analysis/consolidated_20261121_bnd_v41/staged/merged/traces/`` are opened for
reading only; nothing is written outside ``analysis/measurement_validation/``.

The corpus is ~27k JSON documents and takes ~2 minutes to scan.  Every
downstream script needs the same handful of metadata fields, so this script
extracts them ONCE into ``.cache/trace_index.jsonl`` and every later stage
reads that.  Re-running is safe and idempotent; ``--force`` re-scans.

Output row fields (all read from the trace document itself, never inferred):
    trace_id, path, task_family, fixture_id, topology, propagation_mode,
    condition, lep_codes, execution_variant, repetition_index, n_events,
    has_final_response
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import common as C  # noqa: E402

INDEX_PATH = os.path.join(C.CACHE_DIR, 'trace_index.jsonl')


def index_one(path: str) -> dict | None:
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            t = json.load(fh)
    except Exception:                                             # noqa: BLE001
        return None
    m = t.get('metadata') or {}
    tid = t.get('trace_id')
    if not tid:
        return None
    events = t.get('events') or []
    return {
        'trace_id': tid,
        'path': os.path.realpath(path),
        'task_family': m.get('task_family'),
        'fixture_id': m.get('fixture_id') or tid.split('.')[0],
        'topology': m.get('topology'),
        'propagation_mode': m.get('propagation_mode', 'single_origin'),
        'condition': m.get('condition'),
        'lep_codes': m.get('lep_codes') or [],
        'execution_variant': m.get('execution_variant', 'standard'),
        'repetition_index': m.get('repetition_index'),
        'injection_origin_count': t.get('injection_origin_count'),
        'n_events': len(events),
        'has_final_response': any(e.get('event_type') == 'final_response'
                                  and (e.get('output_text') or '')
                                  for e in events),
        'schema_version': t.get('schema_version'),
    }


def build(force: bool = False) -> str:
    if os.path.isfile(INDEX_PATH) and not force:
        n = sum(1 for _ in open(INDEX_PATH))
        print(f'index present: {INDEX_PATH} ({n} rows); use --force to rebuild')
        return INDEX_PATH
    d = C.CONS_TRACES
    names = sorted(n for n in os.listdir(d) if n.endswith('.json'))
    t0 = time.time()
    rows, bad = [], 0
    for i, n in enumerate(names):
        r = index_one(os.path.join(d, n))
        if r is None:
            bad += 1
        else:
            rows.append(r)
        if (i + 1) % 5000 == 0:
            print(f'  scanned {i + 1}/{len(names)} '
                  f'({time.time() - t0:.0f}s)', file=sys.stderr)
    C.ensure_dir(C.CACHE_DIR)
    C.write_jsonl(INDEX_PATH, rows)
    print(f'indexed {len(rows)} traces ({bad} unreadable) in '
          f'{time.time() - t0:.0f}s -> {INDEX_PATH}')
    return INDEX_PATH


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args()
    build(a.force)


if __name__ == '__main__':
    main()
