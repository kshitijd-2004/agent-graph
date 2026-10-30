"""Shared helpers for BND V3 tests: O(1) trace/spec loading via the
prebuilt disk index under ``analysis/bnd_v3_dev/`` (development artifact;
the public test suite only depends on V3 module + pytest).

The index is rebuilt by ``tests/bnd_v3_helpers.py::rebuild_index`` when
missing.  It is *test input* derived from the frozen production traces,
never a modification of them.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional

REPO = Path(__file__).resolve().parents[1]
TRACE_DIR = REPO / 'analysis/full_production_consolidated/source/traces'
SPEC_ROOT = REPO / 'workspace_fixtures'
INDEX_FILE = REPO / 'analysis/bnd_v3_dev/trace_index.json'
CELL_FILE = REPO / 'analysis/bnd_v3_dev/cell_map.json'

_trace_index: Optional[Dict[str, str]] = None
_cell_map: Optional[dict] = None
_spec_cache: Dict[str, Optional[dict]] = {}


def rebuild_index() -> None:
    """(Re)build the disk index from the frozen production traces."""
    import glob
    idx: Dict[str, str] = {}
    cell_map: Dict[str, List[dict]] = {'benign': {}, 'lep': {}}
    pat = re.compile(r'"trace_id"\s*:\s*"([^"]+)"')
    for p in sorted(glob.glob(str(TRACE_DIR / '*_trace.json'))):
        if not Path(p).is_file():
            continue
        with open(p) as f:
            head = f.read(400)
        m = pat.search(head)
        if not m:
            continue
        tid = m.group(1)
        idx[tid] = p
        with open(p) as f:
            d = json.load(f)
        md = d.get('metadata', {})
        fx = md.get('fixture_id')
        top = md.get('topology')
        ev = md.get('execution_variant', 'standard') or 'standard'
        cond = md.get('condition')
        if fx:
            cell = f'{fx}|{top}|{ev}'
            entry = {'path': p, 'trace_id': tid,
                     'repetition_index': md.get('repetition_index')}
            if cond in ('benign', 'counterfactual'):
                cell_map['benign'].setdefault(cell, []).append(entry)
            elif cond == 'single_lep' and md.get('dataset_eligible', True):
                cell_map['lep'].setdefault(cell, []).append(entry)
    INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.write_text(json.dumps(idx, sort_keys=True))
    CELL_FILE.write_text(json.dumps(cell_map, sort_keys=True))


def trace_index() -> Dict[str, str]:
    global _trace_index
    if _trace_index is None:
        if INDEX_FILE.is_file():
            _trace_index = json.loads(INDEX_FILE.read_text())
        else:
            rebuild_index()
            _trace_index = json.loads(INDEX_FILE.read_text())
    return _trace_index


def cell_map() -> dict:
    global _cell_map
    if _cell_map is None:
        if CELL_FILE.is_file():
            _cell_map = json.loads(CELL_FILE.read_text())
        else:
            rebuild_index()
            _cell_map = json.loads(CELL_FILE.read_text())
    return _cell_map


def load_trace(trace_id: str) -> dict:
    """Load a production trace by trace_id (O(1) after index build)."""
    return json.loads(Path(trace_index()[trace_id]).read_text())


def load_spec(fixture_id: str) -> Optional[dict]:
    if fixture_id not in _spec_cache:
        p = SPEC_ROOT / fixture_id / 'manifest.json'
        _spec_cache[fixture_id] = json.loads(p.read_text()) \
            if p.is_file() else None
    return _spec_cache[fixture_id]


def benign_refs_for(cell: Dict[str, str], n: int = 3) -> List[dict]:
    """First *n* distinct-repetition benign refs for a (fixture, topology,
    execution_variant) cell, in the same deterministic policy as the
    analysis script's ``discover()``."""
    cmap = cell_map()
    key = f"{cell['fixture']}|{cell['topology']}|{cell['ev']}"
    entries = cmap['benign'].get(key, [])
    seen: Dict[Optional[int], dict] = {}
    for e in sorted(entries, key=lambda e: e['path']):
        r = e.get('repetition_index')
        if r is not None and r not in seen:
            seen[r] = e
    refs = [json.loads(Path(seen[r]['path']).read_text())
            for r in sorted(seen)[:n]]
    return refs


def refs_for_trace(trace: dict, n: int = 3) -> List[dict]:
    md = trace.get('metadata', {})
    cell = {'fixture': md.get('fixture_id'), 'topology': md.get('topology'),
            'ev': md.get('execution_variant', 'standard') or 'standard'}
    return benign_refs_for(cell, n)
