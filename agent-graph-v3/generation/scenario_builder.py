"""Scenario builder — constructs ScenarioSpec objects from configuration.

Supports building scenarios from YAML/JSON configs, generating
the full experimental matrix including benign, single-LEP,
counterfactual, and convergence conditions.
"""

from __future__ import annotations

import itertools
import json
import logging
import random
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from schemas import (
    LEPConfig, ScenarioSpec, WorkflowConfig,
    TOPOLOGIES, SHARING_POLICIES, MEMORY_MODES, VERIFICATION_MODES,
)

logger = logging.getLogger(__name__)

# LEPs implemented and safe for dry-run execution
REGISTERED_LEP_CODES: set[str] = set()


def _load_registered_lep_codes() -> set[str]:
    """Load LEP codes from the registry without importing (avoids circular deps)."""
    global REGISTERED_LEP_CODES
    if REGISTERED_LEP_CODES:
        return REGISTERED_LEP_CODES
    try:
        from leps.registry import LEP_REGISTRY
        REGISTERED_LEP_CODES = set(LEP_REGISTRY.keys())
    except ImportError:
        REGISTERED_LEP_CODES = {
            "LEP_TOOL_RESULT_CORRUPTION",
            "LEP_INDIRECT_PROMPT_INJECTION",
            "LEP_MEMORY_POISONING",
            "LEP_HANDOFF_CORRUPTION",
            "LEP_INPUT_DISREGARD",
        }
    return REGISTERED_LEP_CODES


def validate_lep_code(code: str, task_family: str = "") -> None:
    """Raise ValueError if LEP code is not registered."""
    registered = _load_registered_lep_codes()
    if code not in registered:
        raise ValueError(
            f"Unregistered LEP code '{code}' for task '{task_family}'. "
            f"Registered codes: {sorted(registered)}"
        )

# Default configurations
DEFAULT_TOPOLOGIES = ["linear_2", "linear_3", "coordinator_star", "parallel_merge"]
DEFAULT_SHARING_POLICIES = ["full_state", "handoff_summary_only", "selective_artifacts"]
DEFAULT_MEMORY_MODES = ["none", "ephemeral_shared", "persistent_shared"]
DEFAULT_VERIFICATION_MODES = ["none", "self_check", "independent_verifier"]

# Task families with their default configs
TASK_CONFIGS: Dict[str, Dict[str, Any]] = {
    "code_review": {
        "default_topology": "linear_2",
        "supported_topologies": ["linear_2", "review_loop", "branch_and_verify"],
        "default_agents": ["inspector", "reviewer"],
    },
    "financial_analysis": {
        "default_topology": "linear_2",
        "supported_topologies": ["linear_2", "parallel_merge", "branch_and_verify"],
        "default_agents": ["extractor", "analyst"],
    },
    "research_synthesis": {
        "default_topology": "linear_3",
        "supported_topologies": ["linear_3", "coordinator_star", "review_loop"],
        "default_agents": ["researcher", "synthesizer", "verifier"],
    },
    "competitive_intelligence": {
        "default_topology": "linear_3",
        "supported_topologies": ["linear_3", "parallel_merge", "coordinator_star"],
        "default_agents": ["researcher", "analyst", "reviewer"],
    },
}


@dataclass
class ScenarioBuildConfig:
    """Configuration for building a batch of scenarios."""
    task_family: str
    fixture_id: str
    task_variant: str = "default"

    # Workflow config
    topology: str = "linear_2"
    sharing_policy: str = "full_state"
    memory_mode: str = "none"
    verification_mode: str = "none"
    max_events: int = 80
    max_agent_turns: int = 40
    timeout_seconds: int = 300

    # Model config
    model_name: str = "mock"
    temperature: float = 0.0
    seed: int | None = None

    # LEP configs to apply
    lep_configs: list[LEPConfig] = field(default_factory=list)

    # Condition
    condition: str = "benign"

    # Repetition
    repetition_index: int = 0
    num_repetitions: int = 1


class ScenarioBuilder:
    """Builds ScenarioSpec objects from configuration.

    Supports:
    - Single scenario generation
    - Batch generation with balanced design
    - Counterfactual pairs (with/without LEP)
    - Convergence scenarios (multiple LEPs)
    """

    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)

    def build_single(self, config: ScenarioBuildConfig) -> ScenarioSpec:
        """Build a single ScenarioSpec from config."""
        scenario_id = self._make_scenario_id(config)

        wcfg = WorkflowConfig(
            topology=config.topology,
            sharing_policy=config.sharing_policy,
            memory_mode=config.memory_mode,
            verification_mode=config.verification_mode,
            max_events=config.max_events,
            max_agent_turns=config.max_agent_turns,
            timeout_seconds=config.timeout_seconds,
            model_name=config.model_name,
            temperature=config.temperature,
            seed=config.seed,
        )

        return ScenarioSpec(
            scenario_id=scenario_id,
            task_family=config.task_family,
            task_variant=config.task_variant,
            fixture_id=config.fixture_id,
            workflow_config=wcfg,
            lep_configs=list(config.lep_configs),
            condition=config.condition,
            repetition_index=config.repetition_index,
        )

    def build_benign(self, config: ScenarioBuildConfig) -> ScenarioSpec:
        """Build a benign (no LEP) scenario."""
        config = ScenarioBuildConfig(
            **{k: v for k, v in vars(config).items()
               if k not in ("lep_configs", "condition")},
            lep_configs=[],
            condition="benign",
        )
        return self.build_single(config)

    def build_single_lep(
        self,
        config: ScenarioBuildConfig,
        lep_config: LEPConfig,
    ) -> ScenarioSpec:
        """Build a single-LEP scenario. Validates LEP code is registered."""
        from validation.lep_validator import validate_lep_config
        from leps.canonical_operators import get_canonical_operator

        validate_lep_config(lep_config, config.task_family)

        # Resolve and record the canonical operator for this (task_family, LEP).
        # This is fixed at scenario-build time — no runtime variant selection.
        canonical_operator = get_canonical_operator(config.task_family, lep_config.code)
        if canonical_operator is None:
            raise ValueError(
                f"No canonical operator registered for {lep_config.code} "
                f"in task family '{config.task_family}'. "
                f"Add the mapping in leps/canonical_operators.py."
            )
        lep_config.canonical_operator = canonical_operator

        config = ScenarioBuildConfig(
            **{k: v for k, v in vars(config).items()
               if k not in ("lep_configs", "condition")},
            lep_configs=[lep_config],
            condition="single_lep",
        )
        return self.build_single(config)

    def build_counterfactual(
        self,
        lep_scenario: ScenarioSpec,
        remove_lep_code: str | None = None,
    ) -> ScenarioSpec:
        """Build counterfactual: same scenario but without specified LEP.

        If remove_lep_code is None, removes all LEPs.
        """
        remaining = [l for l in lep_scenario.lep_configs
                     if remove_lep_code and l.code != remove_lep_code]
        condition = "counterfactual_no_lep" if not remaining else "counterfactual"

        return ScenarioSpec(
            scenario_id=f"{lep_scenario.scenario_id}_cf",
            task_family=lep_scenario.task_family,
            task_variant=lep_scenario.task_variant,
            fixture_id=lep_scenario.fixture_id,
            workflow_config=lep_scenario.workflow_config,
            lep_configs=remaining,
            condition=condition,
            repetition_index=lep_scenario.repetition_index,
        )

    def build_convergence(
        self,
        config: ScenarioBuildConfig,
        lep_configs: list[LEPConfig],
    ) -> ScenarioSpec:
        """Build a convergence scenario with multiple LEPs."""
        from validation.lep_validator import validate_lep_configs
        validate_lep_configs(lep_configs, config.task_family)
        config = ScenarioBuildConfig(
            **{k: v for k, v in vars(config).items()
               if k not in ("lep_configs", "condition")},
            lep_configs=lep_configs,
            condition="convergence",
        )
        return self.build_single(config)

    def build_convergence_matrix(
        self,
        config: ScenarioBuildConfig,
        lep_configs: list[LEPConfig],
    ) -> list[ScenarioSpec]:
        """Build the full convergence matrix:
        no LEPs, LEP A only, LEP B only, LEP A + B.
        """
        scenarios = []
        a, b = lep_configs[0], lep_configs[1] if len(lep_configs) > 1 else None

        # No LEPs
        scenarios.append(self.build_benign(config))

        # LEP A only
        if a:
            scenarios.append(self.build_single_lep(config, a))

        # LEP B only
        if b:
            scenarios.append(self.build_single_lep(config, b))

        # Both
        if a and b:
            scenarios.append(self.build_convergence(config, [a, b]))

        return scenarios

    def build_pilot_batch(
        self,
        task_families: list[str],
        fixture_ids: list[str],
        lep_configs_per_task: Dict[str, list[LEPConfig]],
        num_repetitions: int = 5,
        seed: int | None = None,
        model_name: str = "claude-sonnet-5",
    ) -> list[ScenarioSpec]:
        """Build a pilot batch of scenarios.

        For each task x fixture x LEP combination, generate:
        - 1 benign
        - 1 single-LEP
        - 1 counterfactual (no LEP, but with LEP's trigger context)
        Repeated num_repetitions times with different seeds.
        """
        scenarios = []
        rng = random.Random(seed)

        for rep in range(num_repetitions):
            for task_family in task_families:
                for fixture_id in fixture_ids:
                    leps = lep_configs_per_task.get(task_family, [])
                    task_cfg = TASK_CONFIGS.get(task_family, {})
                    topology = task_cfg.get("default_topology", "linear_2")

                    # Benign
                    base_config = ScenarioBuildConfig(
                        task_family=task_family,
                        fixture_id=fixture_id,
                        task_variant="default",
                        topology=topology,
                        repetition_index=rep,
                        model_name=model_name,
                        seed=rng.randint(0, 100000) if seed else None,
                    )
                    scenarios.append(self.build_benign(base_config))

                    # One LEP per task (pilot uses 1 LEP per scenario)
                    for lep in leps[:1]:  # Pilot: 1 LEP per scenario
                        from validation.lep_validator import validate_lep_config
                        try:
                            validate_lep_config(lep, task_family)
                        except ValueError as e:
                            logger.warning("Skipping unregistered LEP %s for %s: %s",
                                         lep.code, task_family, e)
                            continue
                        lep_config = ScenarioBuildConfig(
                            task_family=task_family,
                            fixture_id=fixture_id,
                            task_variant="default",
                            topology=topology,
                            lep_configs=[lep],
                            condition="single_lep",
                            repetition_index=rep,
                            model_name=model_name,
                            seed=rng.randint(0, 100000) if seed else None,
                        )
                        scenarios.append(self.build_single(lep_config))

                        # Counterfactual
                        cf = self.build_counterfactual(scenarios[-1])
                        cf.scenario_id = f"{cf.scenario_id}_cf"
                        scenarios.append(cf)

        return scenarios

    def _make_scenario_id(self, config: ScenarioBuildConfig) -> str:
        """Generate a unique scenario ID."""
        parts = [
            config.task_family[:4],
            config.fixture_id[:6],
            config.topology[:4],
            config.condition[:3],
            str(config.repetition_index),
        ]
        return "_".join(parts)
