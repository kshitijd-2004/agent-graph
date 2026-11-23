"""Tests for extraction-state canonicalisation.

The single most consequential distinction in the whole measurement is
``absent`` versus ``unavailable``.  Collapsing them would turn "we could not
extract this" into "the agent did not say this", which silently converts
missing data into a confident negative — and a confident negative is exactly
what makes a dimension look non-divergent.

These tests pin that distinction at the ``views`` layer, which is where the
two extractors' different vocabularies are unified.
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'scripts'))
sys.path.insert(0, HERE)

import views as V  # noqa: E402
import common as C  # noqa: E402


def test_canonical_state_vocabulary_is_exactly_the_five_states():
    assert set(V.CANONICAL_STATES) == {
        'observed', 'absent', 'ambiguous', 'unavailable', 'not_applicable'}


@pytest.mark.parametrize('raw', [None, '', 'missing', 'not_observed',
                                 'not_available', 'unavailable', 'none'])
def test_missing_values_canonicalise_to_unavailable_not_absent(raw):
    """A field that was never populated is UNAVAILABLE.  Calling it `absent`
    would assert the agent said nothing, which the trace does not establish."""
    assert V.canon_state(raw) == 'unavailable'


def test_absent_is_reserved_for_an_actual_negative_finding():
    """`absent` must only come from an extractor that actually looked and
    found nothing — never from a missing field."""
    assert V.canon_state('absent') == 'absent'
    assert V.canon_state('absent') != V.canon_state(None)


def test_observed_and_ambiguous_survive_unchanged():
    for s in ('observed', 'ambiguous', 'not_applicable'):
        assert V.canon_state(s) == s


def test_unknown_state_string_does_not_silently_become_absent():
    """An unrecognised state is a bug in the producer.  It must surface as a
    distinct sentinel, never as ``absent`` (a confident negative) and never as
    ``observed``."""
    out = V.canon_state('some_new_state')
    assert out == 'unknown', (
        f'unrecognised state canonicalised to {out!r}; it must stay a '
        f'distinct sentinel so downstream code cannot mistake a producer bug '
        f'for a real label')
    assert out not in ('absent', 'observed', 'unavailable')


def test_unknown_is_not_part_of_the_canonical_vocabulary():
    """``unknown`` is a diagnostic, not a label: if it were canonical,
    downstream code could silently treat it as a measured state."""
    assert 'unknown' not in V.CANONICAL_STATES


def test_canon_state_is_idempotent():
    """The canonicaliser is applied at several layers; re-applying it must
    not change a value."""
    for s in V.CANONICAL_STATES:
        assert V.canon_state(V.canon_state(s)) == s


def test_feature_value_and_raw_value_disagree_only_where_expected():
    """``_fv`` treats a bare non-None value as ``observed`` and a bare None as
    ``unavailable``.  Both are correct: a populated field IS an observation,
    and an empty one is not a negative."""
    assert V._fv({'value': 3.5, 'state': 'observed'}) == (3.5, 'observed')
    assert V._fv(None) == (None, 'unavailable')
    assert V._fv(3.5) == (3.5, 'observed')
    # the load-bearing distinction
    assert V._fv(None)[1] != V._fv(0)[1], (
        'a None field and a genuine zero must not share a state: 0 is an '
        'observed value, None is missing data')


def test_zero_is_observed_not_unavailable():
    """A zero answer is a real finding.  Mapping it to unavailable would
    discard the single most common numeric answer in FinQA."""
    for zero in (0, 0.0, '0'):
        assert V.canon_state('observed') == 'observed'
    assert V._fv(0) == (0, 'observed')
    assert V._fv(0.0) == (0.0, 'observed')


def test_state_map_covers_every_state_the_extractors_emit():
    """If an extractor emits a state the map does not know, it becomes
    ``unknown`` and is silently dropped from coverage counts."""
    import glob
    import re
    v6 = os.path.join(C.VALIDATION_ROOT, '..', 'measurement_v6')
    emitted = set()
    for p in glob.glob(os.path.join(v6, '*.py')):
        src = open(p, encoding='utf-8').read()
        for m in re.finditer(r"""state\s*=\s*['"]([a-z_]+)['"]""", src):
            emitted.add(m.group(1))
    unknown = {s for s in emitted
               if V.canon_state(s) == 'unknown'}
    assert not unknown, (
        f'extractors emit states the canonicaliser maps to `unknown`: '
        f'{sorted(unknown)} — these would be dropped from coverage counts')
