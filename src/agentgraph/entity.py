"""Entity-node types for the AgentGraphs graph representation.

Design decision (from DESIGN.md §2): entities are nodes, events are edges.
This is critical because TGN's memory is per-persistent-node — if events were
nodes, each would be a one-shot with no history, so memory could not accumulate
an early injection and carry it forward.

Entity types:
    AGENT        — LLM agent instances (researcher, analyst, etc.)
    TOOL         — Tool/functions called by agents (MCP tools, APIs, etc.)
    USER         — The human user who initiates tasks
    SYSTEM       — System-level init/config events
    INTERNAL     — Agent internal reasoning events
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class EntityType(str, Enum):
    """Categories of entities that can appear in agent execution traces."""

    AGENT = "agent"
    TOOL = "tool"
    USER = "user"
    SYSTEM = "system"
    INTERNAL = "internal"

    @classmethod
    def from_string(cls, s: str) -> EntityType:
        """Parse an entity type from a string (case-insensitive)."""
        mapping = {
            "agent": cls.AGENT,
            "tool": cls.TOOL,
            "user": cls.USER,
            "system": cls.SYSTEM,
            "internal": cls.INTERNAL,
        }
        key = s.strip().lower()
        if key not in mapping:
            raise ValueError(f"Unknown entity type: {s!r}. Must be one of {list(mapping)}")
        return mapping[key]


# Human-readable names for display/logging
ENTITY_TYPE_NAMES: Dict[EntityType, str] = {
    EntityType.AGENT: "Agent",
    EntityType.TOOL: "Tool",
    EntityType.USER: "User",
    EntityType.SYSTEM: "System",
    EntityType.INTERNAL: "Internal",
}


def normalize_entity_id(raw: str) -> str:
    """Normalize an entity identifier to a canonical form.

    Converts to lowercase, replaces spaces/special chars with underscores,
    and strips leading/trailing whitespace.

    Usage:
        >>> normalize_entity_id("Agent_001")
        'agent_001'
        >>> normalize_entity_id("Read Text File")
        'read_text_file'
    """
    return "_".join(raw.lower().strip().split())


@dataclass
class EntityNode:
    """A persistent entity in the agent execution trace.

    In the entity-as-node graph model, each entity gets a stable node ID
    that persists across all events in a trace. This is what enables
    temporal GNNs (TGN, JODIE) to accumulate memory per entity.

    Attributes:
        entity_id:   Stable unique ID (e.g. "agent_001", "tool_read_file")
        entity_type: Category of entity
        name:        Human-readable name (e.g. "researcher", "read_text_file")
        role:        Optional role description (e.g. "Senior Research Analyst")
        capabilities: Optional list of capabilities (for agents) or
                      parameter schema (for tools)
        metadata:    Additional key-value pairs (model, version, etc.)
    """

    entity_id: str
    entity_type: EntityType
    name: str = ""
    role: str = ""
    capabilities: list[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            self.name = self.entity_id
        if isinstance(self.entity_type, str):
            self.entity_type = EntityType.from_string(self.entity_type)

    @property
    def display_name(self) -> str:
        """Human-readable name for logging/display."""
        type_name = ENTITY_TYPE_NAMES.get(self.entity_type, self.entity_type.value)
        if self.role:
            return f"{type_name}:{self.name} ({self.role})"
        return f"{type_name}:{self.name}"

    def to_feature_vector(self, num_types: int = len(EntityType)) -> list[float]:
        """One-hot encode the entity type for GNN input.

        Returns a list of length `num_types` with a 1 at the index
        corresponding to this entity's type.
        """
        vec = [0.0] * num_types
        try:
            idx = list(EntityType).index(self.entity_type)
            vec[idx] = 1.0
        except ValueError:
            pass
        return vec

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a dictionary (for JSON storage)."""
        return {
            "entity_id": self.entity_id,
            "entity_type": self.entity_type.value,
            "name": self.name,
            "role": self.role,
            "capabilities": self.capabilities,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> EntityNode:
        """Deserialize from a dictionary."""
        return cls(
            entity_id=d["entity_id"],
            entity_type=EntityType(d["entity_type"]),
            name=d.get("name", ""),
            role=d.get("role", ""),
            capabilities=d.get("capabilities", []),
            metadata=d.get("metadata", {}),
        )
