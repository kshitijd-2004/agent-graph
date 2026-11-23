"""
Action diagnostics — the separate measurements that the scale-invariant TV
kernel cannot see.  Each diagnostic is tested on its own and NONE is a
weighted composite of the others.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', 'extractors'))

import importlib.util  # noqa: E402
sys.path.insert(0, os.path.join(HERE, '..', '..', 'measurement_v6'))
AD_PATH = os.path.abspath(os.path.join(HERE, '..', 'distances',
                                       'action_diagnostics.py'))
spec = importlib.util.spec_from_file_location('v7_action_diag', AD_PATH)
AD = importlib.util.module_from_spec(spec)
spec.loader.exec_module(AD)


def _trace(seq):
    events = []
    for i, (role, tool, target) in enumerate(seq):
        events.append({'event_type': 'tool_call', 'agent_role': role,
                       'tool_name': tool,
                       'tool_arguments': {'path': target} if target else {}})
    return {'events': events}


A = _trace([('r', 'read', 'a.py'), ('r', 'read', 'b.py'),
            ('a', 'write', 'c.md')])
B = _trace([('r', 'read', 'a.py'), ('r', 'read', 'a.py'),
            ('r', 'read', 'b.py'), ('a', 'write', 'c.md')])


def test_tv_scale_invariance_is_the_documented_limitation():
    """{x:1} vs {x:20} -> 0.0 under TV; that is WHY the count diagnostic
    exists.  We assert the limitation so a future change is visible."""
    import distances as V6D   # the v6 TV kernel
    x1 = _trace([('r', 'read', 'a.py')])
    x20 = _trace([('r', 'read', 'a.py')] * 20)
    assert V6D.total_variation(
        {'k': 1}, {'k': 20}) == 0.0


def test_count_diff_sees_the_storm():
    # B has 4 calls, A has 3 -> diff 1.  A retry storm shows a larger diff.
    assert AD.count_diff(A, B) == 1
    # storm: 9 reads of a.py + 1 write = 10 calls; A has 3 -> diff 7
    storm = _trace([('r', 'read', 'a.py')] * 9 + [('a', 'write', 'c.md')])
    assert AD.count_diff(A, storm) == 7
    assert AD.count_diff(A, A) == 0


def test_retry_multiplicity():
    assert AD.retry_multiplicity(A) == 1      # no retries
    assert AD.retry_multiplicity(B) == 2      # 'a.py' read twice
    assert AD.retry_diff(A, B) == 1


def test_order_distance():
    # identical content, different order -> nonzero
    B2 = _trace([('a', 'write', 'c.md'), ('r', 'read', 'a.py'),
                 ('r', 'read', 'b.py')])
    d_same = AD.order_distance(A, A)
    d_diff = AD.order_distance(A, B2)
    assert d_same == 0.0
    assert d_diff > 0.0
    # neither side has calls -> None, never 0
    empty = {'events': []}
    assert AD.order_distance(empty, empty) is None
    assert AD.order_distance(empty, A) == 1.0


def test_failure_evidence_does_not_fabricate():
    fe = AD.failure_evidence(A)
    assert fe['n_recorded_failures'] == 0
    assert fe['recorded'] is False
    assert 'NOT' in fe['note'].upper() or 'not recorded' in fe['note']


def test_pair_reports_each_diyagnostic_separately():
    p = AD.pair(A, B)
    # each diagnostic is its own key; none is a combined score
    assert set(p) == {'count', 'retry', 'order_distance', 'failure'}
    assert p['count']['diff'] == 1
    assert p['retry']['diff'] == 1
    assert p['order_distance'] is not None
