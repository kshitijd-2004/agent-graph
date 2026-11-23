"""
Ground-truth containment — the leak that MUST fail a run (requirement 6).

Reuses the validation framework's ``strip_gt_values`` feed and re-verifies:
  * it is faithful (task-side label/unit survive),
  * it is contained (no GT-value field survives),
  * the runtime guard ``assert_gt_free`` fires on a leak,
  * the canary probe detects a leaked field and flags a vacuous probe.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, os.path.join(HERE, '..'))
import base  # noqa: E402


SAMPLE_SPEC = {
    'task_prompt': 'find the property and equipment figure',
    'required_facts': {
        'fact_1': {'label': 'property and equipment', 'value': 28663.0,
                   'tolerance': 0.01, 'unit': 'USD thousands',
                   'pattern': '(?i)\\bproperty[\\d.]*(28,?663)(?![\\d])',
                   'source_text': '28663'},
        'answer': {'label': 'hard assets percent', 'value': 12.27,
                   'tolerance': 0.02, 'unit': 'percent',
                   'pattern': '12\\.27', 'source_text': '12.27'},
    },
}


def test_strip_keeps_task_side_fields():
    clean = base.strip_gt_values(SAMPLE_SPEC)
    f1 = clean['required_facts']['fact_1']
    assert f1.get('label') == 'property and equipment'
    assert f1.get('unit') == 'USD thousands'


def test_strip_removes_gt_value_fields():
    clean = base.strip_gt_values(SAMPLE_SPEC)
    f1 = clean['required_facts']['fact_1']
    for k in ('value', 'tolerance', 'pattern', 'source_text'):
        assert k not in f1, f'GT field {k} survived the strip'


def test_assert_gt_free_fires_on_leak():
    import pytest
    with pytest.raises(base.GTLeakError):
        base.assert_gt_free({'required_facts': {'f': {'value': 1}}},
                            'test')


def test_strip_passes_guard():
    clean = base.strip_gt_values(SAMPLE_SPEC)
    base.assert_gt_free(clean, 'test')   # must not raise


def test_canary_detects_leak():
    # a strip that fails to remove a field would surface it; the real strip
    # is clean, so the canary reports no leak and a non-vacuous probe
    clean = base.strip_gt_values(SAMPLE_SPEC)
    probe = base.canary_leak_probe(SAMPLE_SPEC, clean)
    assert probe['leak'] is False
    assert probe['n_gt_value_fields'] >= 4   # fact_1 + answer entries
    assert probe['vacuous'] is False


def test_canary_vacuous_flag():
    clean = base.strip_gt_values({'required_facts': {
        'f': {'label': 'x'}}})
    probe = base.canary_leak_probe({'required_facts': {'f': {
        'label': 'x'}}}, clean)
    assert probe['vacuous'] is True
    assert probe['n_gt_value_fields'] == 0
