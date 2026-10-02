"""Strict loading of saved normal benchmark selections; no execution semantics."""
from collections import defaultdict
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path

from benchmark.benchmark_runner import BenchmarkManifest
from tasks.registry import get_default_leps, get_task_registry

SETTINGS = {
    'num_repetitions', 'num_benign_repetitions', 'dry_run', 'model_name',
    'backend_name', 'vllm_url', 'seed', 'max_events', 'max_agent_turns', 'temperature',
}
ENTRY_FIELDS = {
    'run_id', 'scenario_id', 'task_family', 'fixture_id', 'task_variant', 'condition',
    'execution_variant', 'lep_codes', 'topology', 'propagation_mode', 'lep_code',
    'repetition_index', 'pair_tag', 'is_baseline',
}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, f'Duplicate JSON key: {key}')
        result[key] = value
    return result


def load_plan_manifest(path: Path, base: BenchmarkManifest):
    """Return manifest settings and exact entries, validated against normal planning.

    This v1 format records global execution settings, not per-entry overrides.
    Unknown/missing settings or entry fields reject rather than being discarded.
    Planner run IDs are retained, although their original full-plan indices need
    not equal indices in the fixture-restricted validation plan.
    """
    data = json.loads(path.read_text(), object_pairs_hook=_unique_keys)
    _require(isinstance(data, dict) and data.get('schema') == 'agentprop-readiness-selection-v1',
             'Unsupported plan manifest schema')
    _require(set(data) <= {'schema', 'settings', 'selection', 'execution_ready', 'blocker',
                          'fixture_manifest_sha256', 'scenarios'}, 'Unknown manifest fields')
    settings = data.get('settings')
    _require(isinstance(settings, dict) and set(settings) == SETTINGS,
             'Manifest must explicitly provide exactly the supported execution settings')
    for key in ('num_repetitions', 'num_benign_repetitions', 'max_events', 'max_agent_turns', 'seed'):
        _require(type(settings[key]) is int, f'{key} must be an integer')
    _require(settings['num_repetitions'] >= 0 and settings['num_benign_repetitions'] >= 0
             and settings['max_events'] > 0 and settings['max_agent_turns'] > 0, 'Invalid execution limits')
    _require(type(settings['dry_run']) is bool, 'dry_run must be a boolean')
    temp = settings['temperature']
    _require(type(temp) in (int, float) and math.isfinite(temp) and temp >= 0, 'Invalid temperature')
    _require(settings['backend_name'] in ('api', 'vllm'), 'Unknown backend')
    for key in ('model_name', 'vllm_url'):
        _require(isinstance(settings[key], str) and bool(settings[key].strip()), f'Invalid {key}')
    entries = data.get('scenarios')
    _require(isinstance(entries, list) and bool(entries), 'scenarios must be a nonempty list')
    fixtures = defaultdict(list)
    identities, run_ids = set(), set()
    for entry in entries:
        _require(isinstance(entry, dict) and set(entry) == ENTRY_FIELDS, 'Missing/unknown scenario fields')
        for key in ENTRY_FIELDS - {'lep_codes', 'repetition_index', 'is_baseline'}:
            _require(isinstance(entry[key], str), f'{key} must be a string')
        _require(type(entry['repetition_index']) is int and entry['repetition_index'] >= 0,
                 'Invalid repetition_index')
        _require(type(entry['is_baseline']) is bool and isinstance(entry['lep_codes'], list)
                 and all(isinstance(c, str) for c in entry['lep_codes']), 'Invalid LEP/baseline fields')
        _require(bool(entry['scenario_id']) and entry['scenario_id'] not in identities,
                 'Duplicate or empty scenario identity')
        _require(bool(entry['run_id']) and entry['run_id'] not in run_ids, 'Duplicate or empty run identity')
        identities.add(entry['scenario_id'])
        run_ids.add(entry['run_id'])
        family, fixture = entry['task_family'], entry['fixture_id']
        _require(family in get_task_registry(), f'Unknown task family: {family}')
        _require(fixture and Path(fixture).name == fixture and fixture not in ('.', '..'), 'Invalid fixture ID')
        if fixture not in fixtures[family]:
            fixtures[family].append(fixture)
    hashes = data.get('fixture_manifest_sha256')
    _require(isinstance(hashes, dict), 'Missing fixture manifest hashes')
    for fixture in {f for group in fixtures.values() for f in group}:
        raw = (base.fixture_root / fixture / 'manifest.json').read_bytes()
        _require(hashes.get(fixture) == hashlib.sha256(raw).hexdigest(), f'Fixture manifest changed: {fixture}')
    manifest = replace(base, **settings, task_families=list(fixtures), fixture_ids=dict(fixtures),
                       topologies=list(dict.fromkeys(e['topology'] for e in entries)),
                       propagation_modes=list(dict.fromkeys(e['propagation_mode'] for e in entries)),
                       lep_configs=[replace(lep, task_family=family) for family in fixtures
                                    for lep in get_default_leps(family)], smoke_fixtures=False)
    # Membership in the existing planner enforces fixture, family, variant,
    # LEP, memory variant, topology/mode, repetition, pair tag and identity.
    canonical = {e['scenario_id']: e for e in manifest.build_plan()}
    for entry in entries:
        expected = canonical.get(entry['scenario_id'])
        _require(expected is not None and all(entry[k] == expected[k] for k in ENTRY_FIELDS - {'run_id'}),
                 f'Cannot resolve scenario exactly: {entry["scenario_id"]}')
    return manifest, entries
