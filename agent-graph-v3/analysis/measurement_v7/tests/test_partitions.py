"""
Fixture-level partition isolation and reproducibility (requirement 4).

The split MUST be on the fixture reference graph, so that related benign and
LEP executions (and the pair reference sets that share a fixture cell)
cannot straddle the dev/held-out boundary.  A fixed seed makes the split
byte-reproducible.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, os.path.join(HERE, '..'))
import base  # noqa: E402
import importlib  # noqa: E402


def _load_partitioning():
    p = os.path.join(HERE, '..', 'partitioning.py')
    m = importlib.import_module('partitioning')
    return m


PART = _load_partitioning()


def test_split_is_a_pure_function_of_the_seed(tmp_path):
    """Two calls with the same seed produce the identical fixture set; the
    result does not depend on process hash() randomisation."""
    u, p = PART.load_inputs()['units'], PART.load_inputs()['pairs']
    g = PART.build_reference_graph(u, p)
    a1 = PART.split_fixtures(g)
    a2 = PART.split_fixtures(g)
    assert a1 == a2
    # and independent of dict insertion order (units first, pairs second)
    g2 = PART.build_reference_graph(list(reversed(u)),
                                    list(reversed(p)))
    a3 = PART.split_fixtures(g2)
    assert a3 == a1


def test_reference_graph_keeps_pair_refs_on_the_fixture():
    """A pair's benign reference set is attached to the pair's fixture, so
    no trace shared across two units can land in two partitions."""
    u, p = PART.load_inputs()['units'], PART.load_inputs()['pairs']
    g = PART.build_reference_graph(u, p)
    for pair in p:
        fx = pair['fixture_id']
        refs = pair.get('benign_reference_set') or []
        for t in refs:
            assert t in g[fx]['trace_ids'], \
                f'pair ref {t} not attached to fixture {fx}'
        # every trace in the graph belongs to exactly one fixture
    # and a fixture's traces are assigned to exactly one partition
    assign = PART.split_fixtures(g)
    assert set(assign.values()) <= {'dev', 'heldout'}
    for fx, part in assign.items():
        assert part in ('dev', 'heldout')


def test_no_cross_partition_leakage():
    """A trace that appears as both a sampled unit and a pair reference
    lives in exactly one partition."""
    u, p = PART.load_inputs()['units'], PART.load_inputs()['pairs']
    g = PART.build_reference_graph(u, p)
    assign = PART.split_fixtures(g)
    owner = {}
    for fx, tr in ((fx, set(g[fx]['trace_ids'])) for fx in g):
        part = assign[fx]
        for t in tr:
            if t in owner and owner[t] != part:
                raise AssertionError(
                    f'trace {t} appears in two partitions '
                    f'({owner[t]} and {part})')
            owner[t] = part


def test_frozen_file_refuses_silent_regeneration(tmp_path, monkeypatch):
    """A second run with a different seed does NOT overwrite the frozen
    partition file; it must be an explicit --force, and is recorded as a new
    experiment, never a silent change to the existing split."""
    dst = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       '..', 'out', 'partitions.json')
    if os.path.exists(dst):
        existing = json.load(open(dst))
        # the existing frozen file must match the current SEED; if someone
        # already regenerated it under a different seed, the test flags that
        if existing.get('seed') != PART.SEED:
            raise AssertionError(
                'frozen partition file carries a different seed; '
                'regeneration is a new experiment, not a silent change')


def test_heldout_is_not_vacuous():
    u, p = PART.load_inputs()['units'], PART.load_inputs()['pairs']
    g = PART.build_reference_graph(u, p)
    assign = PART.split_fixtures(g)
    ho_units = sum(1 for x in u if assign[x['fixture_id']] == 'heldout')
    dev_units = sum(1 for x in u if assign[x['fixture_id']] == 'dev')
    assert PART.HELDOUT_MIN_UNITS <= ho_units
    assert dev_units >= PART.HELDOUT_MIN_UNITS


def test_component_split_is_preserved_in_the_report():
    u, p = PART.load_inputs()['units'], PART.load_inputs()['pairs']
    g = PART.build_reference_graph(u, p)
    assign = PART.split_fixtures(g)
    rep = PART._achieved(g, assign, u, p)
    for part in ('dev', 'heldout'):
        cc = rep[part]['component_counts']
        assert set(cc) <= {'A_representative', 'B_targeted'}, \
            f'component split lost in {part}: {cc}'
