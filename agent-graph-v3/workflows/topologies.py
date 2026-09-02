"""Workflow topology types and strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class TopologyType(str, Enum):
    LINEAR_2 = "linear_2"
    LINEAR_3 = "linear_3"
    COORDINATOR_STAR = "coordinator_star"
    REVIEW_LOOP = "review_loop"
    SHARED_MEMORY = "shared_memory_collaboration"
    BRANCH_AND_VERIFY = "branch_and_verify"
    COORDINATOR_WORKERS = "coordinator_workers"


@dataclass
class TopologyConfig:
    """Configuration for a workflow topology."""
    topology_type: TopologyType
    agents: List[str] = field(default_factory=list)
    max_iterations: int = 1
    handoff_sequence: List[str] = field(default_factory=list)
    parallel_groups: List[List[str]] = field(default_factory=list)
    review_threshold: int = 1  # Max review iterations


class WorkflowTopology(ABC):
    """Abstract base for workflow topology strategies.

    Each topology defines:
    - Agent execution order
    - Handoff behavior
    - When the workflow terminates
    """

    def __init__(self, config: TopologyConfig):
        self.config = config

    @abstractmethod
    def next_agent(
        self,
        current_agent: str,
        action: str,
        handoff_content: Optional[str] = None,
        iteration: int = 0,
    ) -> Optional[str]:
        """Determine which agent runs next.

        Returns None to signal workflow termination.
        """
        ...

    @abstractmethod
    def is_terminal(self, action: str, iteration: int) -> bool:
        """Whether the workflow should terminate given the current action."""
        ...

    @abstractmethod
    def initialize(self) -> str:
        """Return the starting agent ID."""
        ...


class Linear2Topology(WorkflowTopology):
    """Two-agent linear: A → B → done.

    Preserves the current two-agent behavior from v2.
    Agent A (e.g. researcher) hands off to Agent B (e.g. analyst).
    Agent B calls final to terminate.
    """

    def __init__(self, config: TopologyConfig):
        super().__init__(config)
        if len(config.agents) < 2:
            config.agents = ["agent_001", "agent_002"]
        self._handoff_done = False

    def initialize(self) -> str:
        self._handoff_done = False
        return self.config.agents[0]

    def next_agent(
        self,
        current_agent: str,
        action: str,
        handoff_content: Optional[str] = None,
        iteration: int = 0,
    ) -> Optional[str]:
        if action == "handoff_to_analyst" and not self._handoff_done:
            self._handoff_done = True
            return self.config.agents[1]
        return None

    def is_terminal(self, action: str, iteration: int) -> bool:
        return action == "final"


class Linear3Topology(WorkflowTopology):
    """Three-agent linear: A → B → C → done.

    Agent A (researcher) hands off to Agent B (analyst),
    who hands off to Agent C (verifier), who calls final.
    """

    def __init__(self, config: TopologyConfig):
        super().__init__(config)
        if len(config.agents) < 3:
            config.agents = ["agent_001", "agent_002", "agent_003"]
        self._handoff_1_done = False
        self._handoff_2_done = False

    def initialize(self) -> str:
        self._handoff_1_done = False
        self._handoff_2_done = False
        return self.config.agents[0]

    def next_agent(
        self,
        current_agent: str,
        action: str,
        handoff_content: Optional[str] = None,
        iteration: int = 0,
    ) -> Optional[str]:
        if action == "handoff_to_analyst" and not self._handoff_1_done:
            self._handoff_1_done = True
            return self.config.agents[1]
        if action == "handoff_to_verifier" and not self._handoff_2_done:
            self._handoff_2_done = True
            return self.config.agents[2]
        return None

    def is_terminal(self, action: str, iteration: int) -> bool:
        return action == "final"


# Placeholder stubs for topologies deferred to Milestone 2
class CoordinatorStarTopology(WorkflowTopology):
    def __init__(self, config: TopologyConfig):
        raise NotImplementedError("coordinator_star deferred to Milestone 2")

    def next_agent(self, *args, **kwargs):
        raise NotImplementedError

    def is_terminal(self, *args, **kwargs):
        raise NotImplementedError

    def initialize(self) -> str:
        raise NotImplementedError


class ParallelMergeTopology(WorkflowTopology):
    def __init__(self, config: TopologyConfig):
        raise NotImplementedError("parallel_merge deferred to Milestone 2")

    def next_agent(self, *args, **kwargs):
        raise NotImplementedError

    def is_terminal(self, *args, **kwargs):
        raise NotImplementedError

    def initialize(self) -> str:
        raise NotImplementedError


class ReviewLoopTopology(WorkflowTopology):
    def __init__(self, config: TopologyConfig):
        raise NotImplementedError("review_loop deferred to Milestone 2")

    def next_agent(self, *args, **kwargs):
        raise NotImplementedError

    def is_terminal(self, *args, **kwargs):
        raise NotImplementedError

    def initialize(self) -> str:
        raise NotImplementedError


def create_topology(config: TopologyConfig) -> WorkflowTopology:
    """Factory function for creating topology strategies."""
    mapping = {
        TopologyType.LINEAR_2: Linear2Topology,
        TopologyType.LINEAR_3: Linear3Topology,
    }
    cls = mapping.get(config.topology_type)
    if cls is None:
        raise ValueError(f"Topology {config.topology_type} not yet implemented")
    return cls(config)
