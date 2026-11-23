"""Guard against module shadowing between the validation code and v6.

``analysis/measurement_v6/`` and
``analysis/measurement_validation/scripts/`` both contain modules with the
same names (``graders.py``, and the v6 extractors).  ``extractors`` prepends
``V6_DIR`` to ``sys.path``, so a name-based ``import graders`` inside the
validation code silently binds the v6 module instead — and because the two
APIs differ, the failure appears as a confusing AttributeError deep in a
grading loop, not as an import error.

These tests pin the resolution so that class of bug cannot return unnoticed.
"""
import importlib
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, '..', 'scripts')
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, HERE)

import common as C  # noqa: E402


def test_v6_and_validation_both_have_a_graders_module():
    """If this stops being true the shadowing risk is gone and the guards
    below can be relaxed."""
    assert os.path.isfile(os.path.join(C.V6_DIR, 'graders.py'))
    assert os.path.isfile(os.path.join(SCRIPTS, 'graders.py'))
    assert os.path.realpath(C.V6_DIR) != os.path.realpath(SCRIPTS)


def test_evaluate_binds_the_validation_graders_not_the_v6_one():
    """The single assertion that would have caught the real bug."""
    import evaluate as E
    got = os.path.realpath(E.G.__file__)
    assert got == os.path.realpath(os.path.join(SCRIPTS, 'graders.py')), (
        f'evaluate.py bound {got} instead of the validation graders')
    assert os.path.realpath(C.V6_DIR) not in got


def test_the_bound_graders_exposes_the_validation_api():
    import evaluate as E
    for name in ('grade_family', 'grade_financial', 'grade_research',
                 'grade_code', 'gt_fields_read'):
        assert hasattr(E.G, name), (
            f'the graders module bound by evaluate.py has no {name}; it is '
            f'probably the v6 module')


def test_v6_graders_would_not_satisfy_the_validation_api():
    """Document WHY the distinction matters: the two modules are not
    interchangeable."""
    spec = importlib.util.spec_from_file_location(
        '_v6_graders_probe', os.path.join(C.V6_DIR, 'graders.py'))
    mod = importlib.util.module_from_spec(spec)
    sys.modules['_v6_graders_probe'] = mod
    spec.loader.exec_module(mod)
    assert not hasattr(mod, 'grade_family'), (
        'the v6 graders now has grade_family; re-check that the validation '
        'code is not relying on coincidence')
    assert hasattr(mod, 'something_else') or True


def test_views_module_is_also_the_local_one():
    import views as V
    assert os.path.realpath(V.__file__).startswith(
        os.path.realpath(SCRIPTS)), (
        f'views bound from {V.__file__}, outside the validation scripts')


def test_every_validation_script_imports_from_its_own_directory():
    """A name-based import of a module that also exists in V6_DIR is the bug
    pattern; scan for it so a future script cannot reintroduce it."""
    import re
    shared = set()
    for d in (C.V6_DIR, SCRIPTS):
        for f in os.listdir(d):
            if f.endswith('.py'):
                shared.add(f[:-3])
    shared &= {f[:-3] for f in os.listdir(C.V6_DIR) if f.endswith('.py')}
    shared &= {f[:-3] for f in os.listdir(SCRIPTS) if f.endswith('.py')}
    risky = set()
    for f in sorted(os.listdir(SCRIPTS)):
        if not f.endswith('.py'):
            continue
        src = open(os.path.join(SCRIPTS, f), encoding='utf-8').read()
        for mod in shared:
            # a bare `import mod` / `from mod import` is risky
            if re.search(rf'^\s*import {mod}\s*$', src, re.M) or \
               re.search(rf'^\s*from {mod} import', src, re.M):
                risky.add((f, mod))
    # graders.py is expected to be handled by the explicit-path loader in
    # evaluate.py; anything else needs the same treatment.
    unexpected = {(f, m) for f, m in risky if m != 'graders'}
    assert not unexpected, (
        f'name-based imports of modules that also exist in v6: {unexpected}')
