"""Entity-node graph builder for AgentGraphs.

Converts parsed Trace objects into entity-node graphs where:
- Nodes are persistent entities (agents, tools, user, system, internal)
- Edges are events (directed, timestamped, with features)

This is the key design choice from DESIGN.md §2: entity-as-node (not event-as-node)
so that temporal GNNs can accumulate per-entity memory.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import torch

from agentgraph.entity import (
    ENTITY_TYPE_NAMES,
    EntityNode,
    EntityType,
)
from agentgraph.trace import (
    Trace,
    TraceEvent,
    TraceEventType,
)


@dataclass
class EntityGraph:
    """An entity-node graph representation of one trace.

    Nodes are persistent entities (agents, tools, etc.). Edges are events
    with timestamps, types, and features.

    Attributes:
        trace_id:       ID of the source trace
        execution_id:   Execution ID (links paired traces)
        variant:        TraceVariant (benign/malignant)
        nodes:          List of EntityNode objects
        node_id_map:    Maps entity_id -> node index
        edge_index:     PyG-style edge index [2, num_edges] (source_idx, target_idx)
        edge_attr:      Edge features [num_edges, edge_feature_dim]
        edge_timestamps: Edge timestamps [num_edges]
        edge_event_types: Event type IDs [num_edges]
        edge_inputs:    Edge input summaries (for temporal models)
        num_nodes:      Number of nodes
        num_edges:      Number of edges
        metadata:       Additional metadata
    """

    trace_id: str
    execution_id: str
    variant: str  # "a" or "b"
    nodes: List[EntityNode]
    node_id_map: Dict[str, int]
    edge_index: torch.Tensor  # [2, num_edges]
    edge_attr: torch.Tensor   # [num_edges, edge_feature_dim]
    edge_timestamps: torch.Tensor  # [num_edges]
    edge_event_types: torch.Tensor  # [num_edges]
    edge_inputs: List[str] = field(default_factory=list)
    num_nodes: int = 0
    num_edges: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.num_nodes == 0:
            self.num_nodes = len(self.nodes)
        if self.num_edges == 0:
            self.num_edges = self.edge_index.shape[1] if self.edge_index.numel() > 0 else 0


class EntityGraphBuilder:
    """Build entity-node graphs from Trace objects.

    The builder:
    1. Extracts unique entities from trace events
    2. Assigns stable node IDs to each entity
    3. Creates directed edges for each event (source -> target)
    4. Encodes edge features (event type, timestamps, text summaries)

    Usage:
        builder = EntityGraphBuilder()
        graph = builder.build(trace)
        static_data = builder.to_static_data(graph)
        temporal_stream = builder.to_temporal_stream(graph)
    """

    # Edge feature dimension breakdown:
    # 5 entity type one-hot + 8 event type one-hot + 1 timestamp (normalized) = 14
    EDGE_FEATURE_DIM = 14

    def __init__(self) -> None:
        self._entity_registry: Dict[str, EntityNode] = {}
        self._node_counter = 0

    def _get_or_create_entity(
        self,
        entity_id: str,
        entity_type: Union[EntityType, str],
        name: str = "",
        role: str = "",
    ) -> EntityNode:
        """Get existing entity or create a new one.

        Ensures stable node IDs: the same entity_id always maps to the
        same node index within a builder instance.
        """
        if entity_id in self._entity_registry:
            return self._entity_registry[entity_id]

        if isinstance(entity_type, str):
            entity_type = EntityType.from_string(entity_type)

        entity = EntityNode(
            entity_id=entity_id,
            entity_type=entity_type,
            name=name or entity_id,
            role=role,
        )
        self._entity_registry[entity_id] = entity
        self._node_counter += 1
        return entity

    def _classify_entity_type(self, source: str, target: str, event_type: TraceEventType) -> Tuple[str, EntityType]:
        """Classify the entity type for source/target of an event."""
        # Agent handoffs have explicit agent IDs
        if event_type == TraceEventType.AGENT_HANDOFF:
            return ("agent", EntityType.AGENT)

        # Tool calls/result
        if event_type in (TraceEventType.TOOL_CALL, TraceEventType.TOOL_RESULT):
            return ("tool", EntityType.TOOL)

        # Reasoning/LLM output from internal
        if target == "internal" or source == "internal":
            return ("internal", EntityType.INTERNAL)

        # Source classification
        if source.startswith("agent_") or source in ("researcher", "analyst", "coder", "reviewer", "planner"):
            return ("agent", EntityType.AGENT)
        if source == "user":
            return ("user", EntityType.USER)
        if source == "system":
            return ("system", EntityType.SYSTEM)

        # Target classification
        if target.startswith("mcp_") or target.startswith("tool_"):
            return ("tool", EntityType.TOOL)
        if target == "user":
            return ("user", EntityType.USER)

        return ("internal", EntityType.INTERNAL)

    def _encode_edge(
        self,
        event: TraceEvent,
        src_idx: int,
        tgt_idx: int,
        first_timestamp: float,
        last_timestamp: float,
    ) -> torch.Tensor:
        """Encode a TraceEvent into a feature vector.

        Feature vector (14 dims):
        - 5 dims: source entity type one-hot
        - 5 dims: target entity type one-hot
        - 4 dims: event type one-hot (tool_call, tool_result, reasoning, handoff)
        """
        src_type = self._entity_registry.get(event.source)
        tgt_type = self._entity_registry.get(event.target)

        src_vec = src_type.to_feature_vector(5) if src_type else [0.0] * 5
        tgt_vec = tgt_type.to_feature_vector(5) if tgt_type else [0.0] * 5

        # Event type one-hot (4 categories)
        evt_vec = [0.0] * 4
        if event.event_type == TraceEventType.TOOL_CALL:
            evt_vec[0] = 1.0
        elif event.event_type == TraceEventType.TOOL_RESULT:
            evt_vec[1] = 1.0
        elif event.event_type == TraceEventType.REASONING:
            evt_vec[2] = 1.0
        elif event.event_type == TraceEventType.AGENT_HANDOFF:
            evt_vec[3] = 1.0

        return torch.tensor(src_vec + tgt_vec + evt_vec, dtype=torch.float)

    def _parse_timestamp(self, timestamp_str: str) -> float:
        """Parse ISO timestamp to Unix float."""
        try:
            dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            return dt.timestamp()
        except (ValueError, AttributeError):
            return 0.0

    def build(self, trace: Trace) -> EntityGraph:
        """Build an entity-node graph from a Trace.

        Args:
            trace: Parsed trace object

        Returns:
            EntityGraph with nodes, edges, and features
        """
        # Reset for fresh build
        self._entity_registry = {}
        self._node_counter = 0

        # First pass: extract all entities from events
        for event in trace.events:
            # Source entity
            if event.source:
                entity_type = EntityType.INTERNAL
                if event.event_type == TraceEventType.AGENT_HANDOFF:
                    entity_type = EntityType.AGENT
                elif event.event_type in (TraceEventType.TOOL_CALL, TraceEventType.TOOL_RESULT):
                    entity_type = EntityType.TOOL
                elif event.source == "user":
                    entity_type = EntityType.USER
                elif event.source == "system":
                    entity_type = EntityType.SYSTEM
                elif event.agent_id:
                    entity_type = EntityType.AGENT

                self._get_or_create_entity(
                    entity_id=event.source,
                    entity_type=entity_type,
                    name=event.agent_name or event.source,
                    role=event.agent_role or "",
                )

            # Target entity
            if event.target and event.target not in self._entity_registry:
                target_type = EntityType.INTERNAL
                if event.target.startswith("mcp_") or event.target.startswith("tool_"):
                    target_type = EntityType.TOOL
                elif event.target == "user":
                    target_type = EntityType.USER
                elif event.target == "internal":
                    target_type = EntityType.INTERNAL

                self._get_or_create_entity(
                    entity_id=event.target,
                    entity_type=target_type,
                    name=event.target,
                )

        # Build node list (ordered by entity_id for stability)
        nodes = sorted(self._entity_registry.values(), key=lambda e: e.entity_id)
        node_id_map = {e.entity_id: i for i, e in enumerate(nodes)}

        # Second pass: build edges from events
        edge_src: List[int] = []
        edge_tgt: List[int] = []
        edge_features: List[torch.Tensor] = []
        edge_timestamps: List[float] = []
        edge_event_types: List[int] = []
        edge_inputs: List[str] = []

        # Compute timestamp range for normalization
        timestamps = [self._parse_timestamp(e.timestamp) for e in trace.events]
        timestamps = [t for t in timestamps if t > 0]
        t_min = min(timestamps) if timestamps else 0.0
        t_max = max(timestamps) if timestamps else 1.0
        t_range = max(t_max - t_min, 1.0)

        event_type_to_id = {
            TraceEventType.USER_INPUT: 0,
            TraceEventType.SYSTEM_INIT: 1,
            TraceEventType.REASONING: 2,
            TraceEventType.TOOL_CALL: 3,
            TraceEventType.TOOL_RESULT: 4,
            TraceEventType.AGENT_HANDOFF: 5,
            TraceEventType.LLM_OUTPUT: 6,
            TraceEventType.FINAL_RESPONSE: 7,
        }

        for event in trace.events:
            if not event.source or not event.target:
                continue
            if event.source not in node_id_map or event.target not in node_id_map:
                continue

            src_idx = node_id_map[event.source]
            tgt_idx = node_id_map[event.target]

            # Encode edge features
            feat = self._encode_edge(event, src_idx, tgt_idx, t_min, t_max)

            # Parse and normalize timestamp
            ts = self._parse_timestamp(event.timestamp)
            ts_norm = (ts - t_min) / t_range if ts > 0 else 0.0

            edge_src.append(src_idx)
            edge_tgt.append(tgt_idx)
            edge_features.append(feat)
            edge_timestamps.append(ts_norm)
            edge_event_types.append(event_type_to_id.get(event.event_type, 0))
            edge_inputs.append(event.input_summary[:200])

        num_edges = len(edge_src)
        if num_edges == 0:
            # Empty graph — single node, no edges
            edge_index = torch.zeros((2, 0), dtype=torch.long)
            edge_attr = torch.zeros((0, self.EDGE_FEATURE_DIM), dtype=torch.float)
            edge_timestamps_t = torch.zeros(0, dtype=torch.float)
            edge_event_types_t = torch.zeros(0, dtype=torch.long)
        else:
            edge_index = torch.tensor([edge_src, edge_tgt], dtype=torch.long)
            edge_attr = torch.stack(edge_features) if edge_features else torch.zeros((0, self.EDGE_FEATURE_DIM))
            edge_timestamps_t = torch.tensor(edge_timestamps, dtype=torch.float)
            edge_event_types_t = torch.tensor(edge_event_types, dtype=torch.long)

        return EntityGraph(
            trace_id=trace.trace_id,
            execution_id=trace.execution_id,
            variant=trace.variant.value,
            nodes=nodes,
            node_id_map=node_id_map,
            edge_index=edge_index,
            edge_attr=edge_attr,
            edge_timestamps=edge_timestamps_t,
            edge_event_types=edge_event_types_t,
            edge_inputs=edge_inputs,
            num_nodes=len(nodes),
            num_edges=num_edges,
            metadata={
                "event_feature_dim": self.EDGE_FEATURE_DIM,
                "t_min": t_min,
                "t_max": t_max,
                "num_event_types": len(event_type_to_id),
            },
        )

    def build_batch(self, traces: List[Trace]) -> List[EntityGraph]:
        """Build entity-node graphs for multiple traces.

        Creates a fresh builder instance per trace to avoid entity ID collisions.

        Args:
            traces: List of parsed Trace objects

        Returns:
            List of EntityGraph objects
        """
        return [self.build(trace) for trace in traces]
