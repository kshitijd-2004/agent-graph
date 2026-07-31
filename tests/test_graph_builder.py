"""Tests for the agentgraph.graph_builder module."""

import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import torch
from agentgraph.entity import EntityNode, EntityType
from agentgraph.graph_builder import EntityGraphBuilder, EntityGraph
from agentgraph.trace import Trace, TraceEvent, TraceEventType, TraceVariant


def _make_test_trace() -> Trace:
    """Create a test trace with known events."""
    events = [
        TraceEvent(
            trace_id="test123a",
            execution_id="test123",
            event_id=1,
            timestamp="2026-01-01T00:00:00+00:00",
            event_type=TraceEventType.USER_INPUT,
            source="user",
            target="multi_agent_system",
            input_summary="Test task input",
            output_summary="",
        ),
        TraceEvent(
            trace_id="test123a",
            execution_id="test123",
            event_id=2,
            timestamp="2026-01-01T00:00:01+00:00",
            event_type=TraceEventType.SYSTEM_INIT,
            source="system",
            target="multi_agent_system",
            input_summary="Initialize system",
            output_summary="Ready with 5 tools",
        ),
        TraceEvent(
            trace_id="test123a",
            execution_id="test123",
            event_id=3,
            timestamp="2026-01-01T00:00:02+00:00",
            event_type=TraceEventType.REASONING,
            source="agent_001",
            target="internal",
            input_summary="Task: test",
            output_summary="Need to list files first",
            agent_id="agent_001",
            agent_name="researcher",
            agent_role="Senior Research Analyst",
        ),
        TraceEvent(
            trace_id="test123a",
            execution_id="test123",
            event_id=4,
            timestamp="2026-01-01T00:00:03+00:00",
            event_type=TraceEventType.TOOL_CALL,
            source="agent_001",
            target="mcp_list_directory",
            input_summary='{"path": "."}',
            output_summary="",
            agent_id="agent_001",
            agent_name="researcher",
            tool_id="mcp_list_directory",
            tool_name="list_directory",
        ),
        TraceEvent(
            trace_id="test123a",
            execution_id="test123",
            event_id=5,
            timestamp="2026-01-01T00:00:04+00:00",
            event_type=TraceEventType.TOOL_RESULT,
            source="mcp_list_directory",
            target="agent_001",
            input_summary="list_directory input",
            output_summary="[DIR] documents [DIR] notes",
            agent_id="agent_001",
            agent_name="researcher",
            tool_id="mcp_list_directory",
            tool_name="list_directory",
        ),
        TraceEvent(
            trace_id="test123a",
            execution_id="test123",
            event_id=6,
            timestamp="2026-01-01T00:00:05+00:00",
            event_type=TraceEventType.AGENT_HANDOFF,
            source="agent_001",
            target="agent_002",
            input_summary="Handoff to analyst",
            output_summary="Control transferred",
            agent_id_from="agent_001",
            agent_name_from="researcher",
            agent_id_to="agent_002",
            agent_name_to="analyst",
        ),
        TraceEvent(
            trace_id="test123a",
            execution_id="test123",
            event_id=7,
            timestamp="2026-01-01T00:00:06+00:00",
            event_type=TraceEventType.FINAL_RESPONSE,
            source="agent_002",
            target="user",
            input_summary="Test task",
            output_summary="Task complete.",
            agent_id="agent_002",
            agent_name="analyst",
        ),
    ]
    return Trace(
        trace_id="test123a",
        execution_id="test123",
        variant=TraceVariant.BENIGN,
        events=events,
    )


class TestEntityGraphBuilder(unittest.TestCase):
    """Tests for the EntityGraphBuilder."""

    def setUp(self):
        self.builder = EntityGraphBuilder()
        self.trace = _make_test_trace()

    def test_build_creates_graph(self):
        graph = self.builder.build(self.trace)
        self.assertIsInstance(graph, EntityGraph)
        self.assertIsInstance(graph.nodes, list)
        self.assertIsInstance(graph.edge_index, torch.Tensor)

    def test_graph_has_correct_metadata(self):
        graph = self.builder.build(self.trace)
        self.assertEqual(graph.trace_id, "test123a")
        self.assertEqual(graph.execution_id, "test123")
        self.assertEqual(graph.variant, "a")

    def test_graph_has_nodes(self):
        graph = self.builder.build(self.trace)
        self.assertGreater(graph.num_nodes, 0)
        # Should have at least: user, system, agent_001, agent_002, mcp_list_directory, internal
        self.assertGreaterEqual(graph.num_nodes, 5)

    def test_graph_has_edges(self):
        graph = self.builder.build(self.trace)
        self.assertGreater(graph.num_edges, 0)

    def test_edge_features_shape(self):
        graph = self.builder.build(self.trace)
        expected_dim = EntityGraphBuilder.EDGE_FEATURE_DIM
        self.assertEqual(graph.edge_attr.shape[1], expected_dim)

    def test_edge_timestamps_shape(self):
        graph = self.builder.build(self.trace)
        self.assertEqual(graph.edge_timestamps.shape[0], graph.num_edges)

    def test_edge_event_types_shape(self):
        graph = self.builder.build(self.trace)
        self.assertEqual(graph.edge_event_types.shape[0], graph.num_edges)

    def test_node_id_map(self):
        graph = self.builder.build(self.trace)
        self.assertIsInstance(graph.node_id_map, dict)
        for entity_id, idx in graph.node_id_map.items():
            self.assertEqual(graph.nodes[idx].entity_id, entity_id)

    def test_stable_node_ids(self):
        """Same entity should always map to same node index."""
        graph1 = self.builder.build(self.trace)
        builder2 = EntityGraphBuilder()
        graph2 = builder2.build(self.trace)

        self.assertEqual(
            graph1.node_id_map.get("agent_001"),
            graph2.node_id_map.get("agent_001"),
        )

    def test_build_empty_trace(self):
        """Empty trace should produce empty graph."""
        empty_trace = Trace(
            trace_id="empty",
            execution_id="empty",
            variant=TraceVariant.BENIGN,
            events=[],
        )
        graph = self.builder.build(empty_trace)
        self.assertEqual(graph.num_nodes, 0)
        self.assertEqual(graph.num_edges, 0)

    def test_build_batch(self):
        """Building multiple traces should work."""
        trace2 = Trace(
            trace_id="test456a",
            execution_id="test456",
            variant=TraceVariant.BENIGN,
            events=self.trace.events,
        )
        graphs = self.builder.build_batch([self.trace, trace2])
        self.assertEqual(len(graphs), 2)
        self.assertEqual(graphs[0].trace_id, "test123a")
        self.assertEqual(graphs[1].trace_id, "test456a")


if __name__ == "__main__":
    unittest.main()
