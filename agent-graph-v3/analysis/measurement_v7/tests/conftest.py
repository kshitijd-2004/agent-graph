"""
v7 test-suite bootstrap.

Puts REPO and V6_DIR on sys.path (so ``extract_core``, ``extract_research``
etc. resolve to the v6 implementations) and registers the v7 extractor and
distance modules under distinct names so they cannot shadow each other.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
V7 = os.path.abspath(os.path.join(HERE, '..'))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))

sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, 'analysis', 'measurement_v6'))
sys.path.insert(0, os.path.join(V7, 'extractors'))
sys.path.insert(0, V7)

# the v7 distances package is loaded by explicit path under a unique name so
# the bare top-level 'distances' name stays the v6 module
import importlib.util as _ilu                       # noqa: E402


def _load(name, path):
    if name in sys.modules:
        return sys.modules[name]
    spec = _ilu.spec_from_file_location(name, path)
    mod = _ilu.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


load_v7_dist = _load
