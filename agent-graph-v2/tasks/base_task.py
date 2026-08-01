"""Base task class for agent-graph-v2.

Defines the interface for all tasks. Subclasses provide:
- Natural language prompts (no strict plans)
- LEP configuration (which LEPs are supported, where they inject)
- LEP injection/cleanup via the environment
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from environment.workspace import Workspace
from memory.memory_store import MemoryStore


@dataclass
class LEPConfig:
    code: str
    name: str
    target_agent: str
    injection_steps: List[int]
    description: str
    category: str = ""


class BaseTask(ABC):
    TASK_NAME: str = ""
    TASK_CATEGORY: str = "research"
    DESCRIPTION: str = ""
    REQUIRED_TOOLS: List[str] = [
        "list_directory", "read_text_file", "write_file",
        "search_files", "create_directory",
    ]

    BENIGN_PROMPTS: Dict[str, str] = {}
    TASK_PROMPTS: List[str] = []

    def __init__(self, workspace_base: Path):
        self.workspace_base = Path(workspace_base)

    @abstractmethod
    def get_lep_configs(self) -> Dict[str, LEPConfig]:
        """Return available LEPs for this task."""
        ...

    @abstractmethod
    def get_tasks(self) -> List[str]:
        """Return task prompts."""
        ...

    def get_prompt(self, agent: str) -> str:
        """Get the natural-language prompt for an agent."""
        return self.BENIGN_PROMPTS.get(agent, f"You are {agent}. Complete the task.")

    def inject_lep(self, workspace: Workspace, memory: MemoryStore, lep_code: str) -> None:
        """Inject LEP artifact into the environment."""
        from environment.lep_injector import LEPInjector
        injector = LEPInjector(workspace, memory, task=self)
        injector.inject(lep_code)

    def cleanup_lep(self, workspace: Workspace, memory: MemoryStore, lep_code: str) -> None:
        """Remove LEP artifact after trace generation."""
        from environment.lep_injector import LEPInjector
        injector = LEPInjector(workspace, memory)
        injector.cleanup(lep_code)

    def setup_workspace(self, workspace: Workspace) -> None:
        """Copy base workspace files to the working workspace."""
        workspace.reset_from_base(self.workspace_base)
