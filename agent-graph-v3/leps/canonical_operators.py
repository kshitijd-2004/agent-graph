"""Canonical operator registry.

Maps (task_family, LEP_code) → canonical operator name.

Each LEP has exactly one operator per task family. No runtime variant
selection — the operator is fixed at scenario-build time and recorded
in the manifest/trace.
"""

from __future__ import annotations

from typing import Dict, Optional

# ── Canonical operator mapping ─────────────────────────────────────────────
#
# Each key is a (task_family, LEP_code) tuple.
# Each value is the canonical operator name.
#
# If a LEP has no natural injection opportunity for a given task family,
# it is simply absent from the mapping — the scenario builder will skip it
# rather than fabricate an intervention.

CANONICAL_INJECTIONS: Dict[str, Dict[str, str]] = {
    # ── code_review ──────────────────────────────────────────────────────
    "code_review": {
        "LEP_TOOL_RESULT_CORRUPTION":       "material_omission",
        "LEP_MEMORY_POISONING":             "false_fact_insertion",
        "LEP_HANDOFF_CORRUPTION":           "material_finding_omission",
        "LEP_INPUT_DISREGARD":              "start_scratch",
        "LEP_INDIRECT_PROMPT_INJECTION":    "trust_this",
        "LEP_VERIFICATION_FAILURE":         "false_acceptance",
        "LEP_PREMATURE_TERMINATION":        "early_finalize",
    },
    # ── financial_analysis ───────────────────────────────────────────────
    "financial_analysis": {
        "LEP_TOOL_RESULT_CORRUPTION":       "numeric_corruption",
        "LEP_MEMORY_POISONING":             "false_fact_insertion",
        "LEP_HANDOFF_CORRUPTION":           "swap_attribution",
        "LEP_INPUT_DISREGARD":              "discard_warnings",
        "LEP_INDIRECT_PROMPT_INJECTION":    "skip_verification",
        "LEP_VERIFICATION_FAILURE":         "false_acceptance",
        "LEP_PREMATURE_TERMINATION":        "early_finalize",
    },
    # ── competitive_intelligence ───────────────────────────────────────────
    "competitive_intelligence": {
        "LEP_TOOL_RESULT_CORRUPTION":       "stale_pricing",
        "LEP_MEMORY_POISONING":             "false_fact_insertion",
        "LEP_HANDOFF_CORRUPTION":           "omission_in_handoff",
        "LEP_INPUT_DISREGARD":              "discard_evidence",
        "LEP_INDIRECT_PROMPT_INJECTION":    "false_competitor_claim",
        "LEP_VERIFICATION_FAILURE":         "false_acceptance",
        "LEP_PREMATURE_TERMINATION":        "early_finalize",
    },
    # ── research_synthesis ───────────────────────────────────────────────
    "research_synthesis": {
        "LEP_TOOL_RESULT_CORRUPTION":       "source_swap",
        "LEP_MEMORY_POISONING":             "false_fact_insertion",
        "LEP_HANDOFF_CORRUPTION":           "swap_attribution",
        "LEP_INPUT_DISREGARD":              "recency_bias",
        "LEP_INDIRECT_PROMPT_INJECTION":    "trust_this",
        "LEP_VERIFICATION_FAILURE":         "false_acceptance",
        "LEP_PREMATURE_TERMINATION":        "early_finalize",
    },
}


def get_canonical_operator(task_family: str, lep_code: str) -> Optional[str]:
    """Return the canonical operator for (task_family, lep_code).

    Returns None if no operator is defined — the scenario builder should
    skip this LEP for this task family rather than fabricate an intervention.
    """
    family_map = CANONICAL_INJECTIONS.get(task_family, {})
    return family_map.get(lep_code)


def get_all_operators(task_family: str) -> Dict[str, str]:
    """Return all canonical operators for a task family."""
    return dict(CANONICAL_INJECTIONS.get(task_family, {}))
