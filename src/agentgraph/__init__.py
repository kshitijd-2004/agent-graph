"""AgentGraphs - Local Execution Perturbation Detection in Multi-Agent Traces.

This package provides tools for:
- Parsing JSONL trace files from multi-agent systems
- Building entity-node graphs from execution traces
- Encoding graphs for GNN training (static and temporal)
- Exporting to DyGLib-compatible formats
- Training temporal GNN models for perturbation detection
"""

from agentgraph.encoder import (
    EntityGraphEncoder,
    GraphEncoder,
    StaticGraphData,
    TemporalGraphData,
)
from agentgraph.entity import (
    ENTITY_TYPE_NAMES,
    EntityNode,
    EntityType,
    normalize_entity_id,
)
from agentgraph.graph_builder import EntityGraph, EntityGraphBuilder
from agentgraph.exporter import ExportManager
from agentgraph.parser import JSONLTraceParser
from agentgraph.trace import (
    Trace,
    TraceEvent,
    TraceEventType,
    TraceVariant,
)

__version__ = "1.0.0"

__all__ = [
    # Core data structures
    "Trace",
    "TraceEvent",
    "TraceEventType",
    "TraceVariant",
    "EntityType",
    "EntityNode",
    "ENTITY_TYPE_NAMES",
    "EntityGraph",
    # Parsing
    "JSONLTraceParser",
    # Building
    "EntityGraphBuilder",
    # Encoding
    "GraphEncoder",
    "EntityGraphEncoder",
    "StaticGraphData",
    "TemporalGraphData",
    # Export
    "ExportManager",
    # Utilities
    "normalize_entity_id",
]
