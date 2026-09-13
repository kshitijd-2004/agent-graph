"""Workflow topology types and strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class TopologyType(str, Enum):
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
    raise ValueError(
        f"Topology {config.topology_type} not yet implemented. "
        "The legacy linear_2 and linear_3 strategies have been removed. "
        "Use generation/topology.py for active topologies."
    )
