"""Canonical feature-schema constants for detector-visible graph representations.

This module is the single source of truth for the observable feature layout
produced by DependsOnGraphBuilder and consumed by GraphEncoder and all
heuristic / learned detectors. Perturbation-flag columns are defined here
so that consumers can explicitly exclude them without hard-coding indices.

Layout (30 total columns in raw EventGraph.node_features):
    [ event_type one-hot ]      columns 0–10   (11 dims)
    [ agent_role one-hot ]       columns 11–24  (14 dims, 13 roles + OTHER)
    [ perturbation flags ]       columns 25–29  (5 dims)  ← NOT detector-visible

Detector-visible subset: 25 columns (event_type + agent_role only).
"""

from typing import FrozenSet, Tuple

# ── Raw EventGraph feature layout (30 dims) ───────────────────────────────────

EVENT_TYPE_SLICE = slice(0, 11)

# 13 named roles + OTHER
AGENT_ROLE_SLICE = slice(11, 25)

# hidden benchmark-only features
PERTURBATION_FLAG_SLICE = slice(25, 30)

OBSERVABLE_NODE_FEATURE_DIM = 25

OBS_EVENT_TYPE_SLICE = slice(0, 11)
OBS_AGENT_ROLE_SLICE = slice(11, 25)

PERTURBATION_FLAG_COLUMNS: Tuple[int, ...] = (25, 26, 27, 28, 29)
LEAKAGE_COLUMNS: FrozenSet[int] = frozenset(PERTURBATION_FLAG_COLUMNS)


# ── Invariants (fail at import time if layout drifts) ────────────────────────

assert EVENT_TYPE_SLICE == slice(0, 11)
assert AGENT_ROLE_SLICE == slice(11, 25)
assert PERTURBATION_FLAG_SLICE == slice(25, 30)

assert OBSERVABLE_NODE_FEATURE_DIM == 25
assert OBS_EVENT_TYPE_SLICE == slice(0, 11)
assert OBS_AGENT_ROLE_SLICE == slice(11, 25)

assert PERTURBATION_FLAG_COLUMNS == (25, 26, 27, 28, 29)
