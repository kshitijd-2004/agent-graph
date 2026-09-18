"""Canonical feature-schema constants for detector-visible graph representations.

This module is the single source of truth for the observable feature layout
produced by DependsOnGraphBuilder and consumed by GraphEncoder and all
heuristic / learned detectors. Perturbation-flag columns are defined here
so that consumers can explicitly exclude them without hard-coding indices.

Layout (29 total columns in raw EventGraph.node_features):
    [ event_type one-hot ]      columns 0–10   (11 dims)
    [ agent_role one-hot ]       columns 11–23  (13 dims, 12 roles + OTHER)
    [ perturbation flags ]       columns 24–28  (5 dims)  ← NOT detector-visible

Detector-visible subset: 24 columns (event_type + agent_role only).
"""

from typing import FrozenSet, Tuple

# ── Raw EventGraph feature layout (29 dims) ─────────────────────────────────

EVENT_TYPE_SLICE = slice(0, 11)          # 11 event types
AGENT_ROLE_SLICE = slice(11, 24)         # 12 roles + 1 OTHER
PERTURBATION_FLAG_SLICE = slice(24, 29)  # 5 perturbation flags

# ── Detector-visible subset (24 dims) ───────────────────────────────────────

OBSERVABLE_NODE_FEATURE_DIM = 24

# Slices within the detector-visible 24-dim space
OBS_EVENT_TYPE_SLICE = slice(0, 11)       # same as EVENT_TYPE_SLICE
OBS_AGENT_ROLE_SLICE = slice(11, 24)      # same as AGENT_ROLE_SLICE

# ── Column indices for perturbation flags (excluded from detector input) ─────

PERTURBATION_FLAG_COLUMNS: Tuple[int, ...] = (24, 25, 26, 27, 28)

# Names for the perturbation flags (index-aligned with PERTURBATION_FLAG_COLUMNS)
PERTURBATION_FLAG_NAMES: Tuple[str, ...] = (
    "is_injection_origin",
    "consumes_perturbed_info",
    "transforms_perturbed_info",
    "stores_perturbed_info",
    "recovers_from_perturbation",
)

# Set of column indices that must be stripped before any detector sees node_features
LEAKAGE_COLUMNS: FrozenSet[int] = frozenset(PERTURBATION_FLAG_COLUMNS)
