"""Canonical injection-origin expectation for execution and trace auditing."""
from collections import defaultdict
from typing import Iterable

from generation.topology import get_topology


def expected_injection_origins(*, condition: str, lep_codes: Iterable[str],
                               propagation_mode: str = "single_origin",
                               topology: str = "review_loop") -> int:
    """Return the total origin budget, with one origin per active LEP/worker.

    Counterfactuals may retain LEP configuration but never inject. Many-to-one
    workers are the topology's intended handoff sources into its final stage.
    """
    codes = set(lep_codes)
    if condition in ("benign", "counterfactual") or not codes:
        return 0
    if propagation_mode in ("single_origin", "one_to_many"):
        return len(codes)
    if propagation_mode != "many_to_one":
        raise ValueError(f"Unknown propagation mode: {propagation_mode!r}")
    config = get_topology(topology, defaultdict(str))
    workers = {
        rule.from_stage for rule in config.handoff_rules
        if rule.to_stage == config.exit_stage
        and rule.from_stage != config.exit_stage
        and config.stage_by_role[rule.from_stage].can_handoff
    }
    if not workers:
        raise ValueError(f"many_to_one topology {topology!r} has no upstream workers")
    return len(codes) * len(workers)
