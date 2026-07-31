"""Tests for the agentgraph.encoder module."""

import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import torch
from agentgraph.encoder import (
    EntityGraphEncoder,
    GraphEncoder,
    StaticGraphData,
    TemporalGraphData,
)
from agentgraph.graph_builder import EntityGraphBuilder, EntityGraph
from agentgraph.trace import (
    Trace,
    TraceEvent,
    TraceEventType,
    TraceVariant,
)


def _make_test_trace() -> Trace:
    """Create a test trace."""
    events = [
        TraceEvent(
            trace_id="test123a",
            execution_id="test123",
            event_id=1,
            timestamp="2026-01-01T00:00:00+00:00",
            event_type=TraceEventType.USER_INPUT,
            source="user",
            target="multi_agent_system",
        ),
        TraceEvent(
            trace_id="test123a",
            execution_id="test123",
            event_id=2,
            timestamp="2026-01-01T00:00:01+00:00",
            event_type=TraceEventType.TOOL_CALL,
            source="agent_001",
            target="mcp_list_directory",
            agent_name="researcher",
            tool_name="list_directory",
        ),
        TraceEvent(
            trace_id="test123a",
            execution_id="test123",
            event_id=3,
            timestamp="2026-01-01T00:00:02+00:00",
            event_type=TraceEventType.TOOL_RESULT,
            source="mcp_list_directory",
            target="agent_001",
            agent_name="researcher",
            tool_name="list_directory",
        ),
        TraceEvent(
            trace_id="test123a",
            execution_id="test123",
            event_id=4,
            timestamp="2026-01-01T00:00:03+00:00",
            event_type=TraceEventType.AGENT_HANDOFF,
            source="agent_001",
            target="agent_002",
            agent_id_from="agent_001",
            agent_name_from="researcher",
            agent_id_to="agent_002",
            agent_name_to="analyst",
        ),
        TraceEvent(
            trace_id="test123a",
            execution_id="test123",
            event_id=5,
            timestamp="2026-01-01T00:00:04+00:00",
            event_type=TraceEventType.FINAL_RESPONSE,
            source="agent_002",
            target="user",
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


class TestGraphEncoder(unittest.TestCase):
    """Tests for the GraphEncoder."""

    def setUp(self):
        self.builder = EntityGraphBuilder()
        self.trace = _make_test_trace()
        self.graph = self.builder.build(self.trace)
        self.encoder = GraphEncoder()

    def test_encode_static_benign(self):
        """Encoding benign graph should produce label 0."""
        data_list = self.encoder.encode_static([self.graph])
        self.assertEqual(len(data_list), 1)
        self.assertEqual(data_list[0].y.item(), 0.0)

    def test_encode_static_malignant(self):
        """Encoding malignant graph should produce label 1."""
        malignant_graph = EntityGraph(
            trace_id="test123b",
            execution_id="test123",
            variant="b",
            nodes=self.graph.nodes,
            node_id_map=self.graph.node_id_map,
            edge_index=self.graph.edge_index,
            edge_attr=self.graph.edge_attr,
            edge_timestamps=self.graph.edge_timestamps,
            edge_event_types=self.graph.edge_event_types,
            num_nodes=self.graph.num_nodes,
            num_edges=self.graph.num_edges,
        )
        data_list = self.encoder.encode_static([malignant_graph])
        self.assertEqual(data_list[0].y.item(), 1.0)

    def test_encode_static_with_labels(self):
        """Explicit labels should override default."""
        data_list = self.encoder.encode_static([self.graph], labels=[0.5])
        self.assertEqual(data_list[0].y.item(), 0.5)

    def test_encode_temporal(self):
        """Temporal encoding should work."""
        data_list = self.encoder.encode_temporal([self.graph])
        self.assertEqual(len(data_list), 1)
        td = data_list[0]
        self.assertIsInstance(td, TemporalGraphData)
        self.assertEqual(len(td), self.graph.num_edges)
        self.assertEqual(td.label, 0.0)

    def test_encode_both(self):
        """Encoding both formats should work."""
        static, temporal = self.encoder.encode([self.graph])
        self.assertEqual(len(static), 1)
        self.assertEqual(len(temporal), 1)
        self.assertIsInstance(static[0], StaticGraphData)
        self.assertIsInstance(temporal[0], TemporalGraphData)

    def test_node_features_shape(self):
        """Node features should have correct shape."""
        data_list = self.encoder.encode_static([self.graph])
        sd = data_list[0]
        self.assertEqual(sd.x.shape[1], GraphEncoder.NODE_FEATURE_DIM)
        self.assertEqual(sd.x.shape[0], self.graph.num_nodes)

    def test_temporal_sorted_by_timestamp(self):
        """Temporal edges should be sorted by timestamp."""
        data_list = self.encoder.encode_temporal([self.graph])
        td = data_list[0]
        if len(td) > 1:
            timestamps = td.edge_timestamps.tolist()
            self.assertEqual(timestamps, sorted(timestamps))

    def test_static_data_to_device(self):
        """StaticGraphData should support .to(device)."""
        data_list = self.encoder.encode_static([self.graph])
        sd = data_list[0]
        moved = sd.to(torch.device("cpu"))
        self.assertEqual(moved.y.device, torch.device("cpu"))

    def test_encode_batch_mixed_labels(self):
        """Encoding mixed benign/malignant with labels."""
        malignant_graph = EntityGraph(
            trace_id="test123b",
            execution_id="test123",
            variant="b",
            nodes=self.graph.nodes,
            node_id_map=self.graph.node_id_map,
            edge_index=self.graph.edge_index,
            edge_attr=self.graph.edge_attr,
            edge_timestamps=self.graph.edge_timestamps,
            edge_event_types=self.graph.edge_event_types,
            num_nodes=self.graph.num_nodes,
            num_edges=self.graph.num_edges,
        )
        labels = [0.0, 1.0]
        static, temporal = self.encoder.encode([self.graph, malignant_graph], labels)
        self.assertEqual(static[0].y.item(), 0.0)
        self.assertEqual(static[1].y.item(), 1.0)
        self.assertEqual(temporal[0].label, 0.0)
        self.assertEqual(temporal[1].label, 1.0)


if __name__ == "__main__":
    unittest.main()
