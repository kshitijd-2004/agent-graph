"""Workflow and scenario configuration types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .lep_config import LEPConfig


# ── WorkflowConfig ──────────────────────────────────────────────────────────


@dataclass
class WorkflowConfig:
    """Execution configuration for a workflow run.

    Attributes:
        topology:               Workflow topology name
        sharing_policy:         How agents share information
        memory_mode:            Memory configuration
        verification_mode:      Whether/how verification is performed
        max_events:             Hard limit on events per trace
        max_agent_turns:        Hard limit on agent turns
        timeout_seconds:        Wall-clock timeout
        model_name:             LLM model identifier
        temperature:            Sampling temperature
        seed:                   Random seed (None = nondeterministic)
        allow_parallel_agents:  Whether agents can run in parallel
        allow_retries:          Whether failed actions can be retried
    """
    topology: str = "linear_2"
    sharing_policy: str = "handoff_summary_only"
    memory_mode: str = "ephemeral_private"
    verification_mode: str = "none"
    max_events: int = 120
    max_agent_turns: int = 40
    timeout_seconds: int = 300
    model_name: str = "claude-sonnet-5"
    temperature: float = 0.1
    seed: Optional[int] = None
    allow_parallel_agents: bool = False
    allow_retries: bool = True


# Supported enum values for validation
TOPOLOGIES = [
    "linear_2",
    "linear_3",
    "coordinator_star",
    "parallel_merge",
    "review_loop",
    "shared_memory_collaboration",
    "branch_and_verify",
    "fanout_2",
    "merge_2",
]

SHARING_POLICIES = [
    "full_state",
    "handoff_summary_only",
    "selective_artifacts",
    "shared_workspace_only",
    "shared_memory_only",
]

MEMORY_MODES = [
    "none",
    "ephemeral_private",
    "ephemeral_shared",
    "persistent_shared",
]

VERIFICATION_MODES = [
    "none",
    "self_check",
    "independent_verifier",
    "consensus",
]

CONDITIONS = ["benign", "single_lep", "convergence", "containment"]


# ── ScenarioSpec ────────────────────────────────────────────────────────────


@dataclass
class ScenarioSpec:
    """One complete experimental condition.

    A ScenarioSpec defines everything needed to reproduce one execution:
    which task, which fixture, which workflow, which LEPs, and which
    stochastic repetition.

    Attributes:
        scenario_id:        Unique scenario identifier
        task_family:        Task family name (e.g. "code_review")
        task_variant:       Prompt variant name
        fixture_id:         Fixture identifier
        workflow_config:    Workflow execution settings
        lep_configs:        LEPs to inject (empty list = benign)
        condition:          "benign", "single_lep", "convergence", "containment"
        repetition_index:   Which stochastic repetition (0-based)
        random_seed:        Seed for reproducibility (None = random)
    """
    scenario_id: str
    task_family: str
    task_variant: str
    fixture_id: str
    workflow_config: WorkflowConfig
    lep_configs: List[LEPConfig] = field(default_factory=list)
    condition: str = "benign"
    repetition_index: int = 0
    random_seed: Optional[int] = None

    def is_benign(self) -> bool:
        return self.condition == "benign" or len(self.lep_configs) == 0

    def is_perturbed(self) -> bool:
        return not self.is_benign()

    def lep_codes(self) -> List[str]:
        return [lep.code for lep in self.lep_configs]

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a dictionary for JSON storage."""
        return {
            "scenario_id": self.scenario_id,
            "task_family": self.task_family,
            "task_variant": self.task_variant,
            "fixture_id": self.fixture_id,
            "workflow_config": {
                "topology": self.workflow_config.topology,
                "sharing_policy": self.workflow_config.sharing_policy,
                "memory_mode": self.workflow_config.memory_mode,
                "verification_mode": self.workflow_config.verification_mode,
                "max_events": self.workflow_config.max_events,
                "max_agent_turns": self.workflow_config.max_agent_turns,
                "timeout_seconds": self.workflow_config.timeout_seconds,
                "model_name": self.workflow_config.model_name,
                "temperature": self.workflow_config.temperature,
                "seed": self.workflow_config.seed,
            },
            "lep_configs": [
                {
                    "code": l.code,
                    "name": l.name,
                    "category": l.category,
                    "description": l.description,
                    "target_agent": l.target_agent,
                    "severity": l.severity,
                    "injection_surface": l.injection_surface,
                    "task_family": l.task_family,
                }
                for l in self.lep_configs
            ],
            "condition": self.condition,
            "repetition_index": self.repetition_index,
            "random_seed": self.random_seed,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> ScenarioSpec:
        """Deserialize from a dictionary."""
        wcfg_data = d.get("workflow_config", {})
        wcfg = WorkflowConfig(**wcfg_data) if wcfg_data else WorkflowConfig()

        lep_configs = []
        for l in d.get("lep_configs", []):
            lep_configs.append(LEPConfig(
                code=l.get("code", ""),
                name=l.get("name", ""),
                category=l.get("category", ""),
                description=l.get("description", ""),
                target_agent=l.get("target_agent"),
                severity=l.get("severity", "medium"),
                injection_surface=l.get("injection_surface", ""),
                task_family=l.get("task_family", ""),
            ))

        return cls(
            scenario_id=d.get("scenario_id", ""),
            task_family=d.get("task_family", ""),
            task_variant=d.get("task_variant", ""),
            fixture_id=d.get("fixture_id", ""),
            workflow_config=wcfg,
            lep_configs=lep_configs,
            condition=d.get("condition", "benign"),
            repetition_index=d.get("repetition_index", 0),
            random_seed=d.get("random_seed"),
        )
