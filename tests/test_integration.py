"""Tests for the pipeline module."""

import unittest
import sys
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from agentgraph import (
    EntityGraph,
    EntityGraphBuilder,
    ExportManager,
    GraphEncoder,
    JSONLTraceParser,
    StaticGraphData,
    Trace,
    TraceEvent,
    TraceEventType,
    TraceVariant,
)
from pipeline.pipeline import (
    benchmark_to_graphs,
    benchmark_to_static_data,
    benchmark_to_temporal_data,
    validate_graph_correctness,
)
from pipeline.trace_analyzer import JaccardDistance, TraceAnalyzer, analyze_pairs
from benchmarks import FinancialTask, MockLLMBackend, TaskCategory


def _make_test_trace_pair() -> tuple:
    """Create a pair of test traces."""
    events = [
        TraceEvent(
            trace_id="test123a",
            execution_id="test123",
            event_id=1,
            timestamp="2026-01-01T00:00:00+00:00",
            event_type=TraceEventType.TOOL_CALL,
            source="agent_001",
            target="mcp_list_directory",
            agent_name="researcher",
            tool_name="list_directory",
        ),
        TraceEvent(
            trace_id="test123a",
            execution_id="test123",
            event_id=2,
            timestamp="2026-01-01T00:00:01+00:00",
            event_type=TraceEventType.TOOL_RESULT,
            source="mcp_list_directory",
            target="agent_001",
            agent_name="researcher",
            tool_name="list_directory",
        ),
        TraceEvent(
            trace_id="test123a",
            execution_id="test123",
            event_id=3,
            timestamp="2026-01-01T00:00:02+00:00",
            event_type=TraceEventType.FINAL_RESPONSE,
            source="agent_001",
            target="user",
            agent_name="researcher",
        ),
    ]

    benign = Trace(
        trace_id="test123a",
        execution_id="test123",
        variant=TraceVariant.BENIGN,
        events=events,
    )

    # Malignant with one LEP event
    mal_events = [
        TraceEvent(
            trace_id="test123b",
            execution_id="test123",
            event_id=1,
            timestamp="2026-01-01T00:00:10+00:00",
            event_type=TraceEventType.TOOL_CALL,
            source="agent_001",
            target="mcp_list_directory",
            agent_name="researcher",
            tool_name="list_directory",
        ),
        TraceEvent(
            trace_id="test123b",
            execution_id="test123",
            event_id=2,
            timestamp="2026-01-01T00:00:11+00:00",
            event_type=TraceEventType.TOOL_CALL,
            source="agent_001",
            target="mcp_list_directory",  # Repeated (FC1.3)
            agent_name="researcher",
            tool_name="list_directory",
            lep_injected=True,
            lep_type="FC1.3 Step Repetition",
        ),
        TraceEvent(
            trace_id="test123b",
            execution_id="test123",
            event_id=3,
            timestamp="2026-01-01T00:00:12+00:00",
            event_type=TraceEventType.FINAL_RESPONSE,
            source="agent_001",
            target="user",
            agent_name="researcher",
            lep_injected=True,
            lep_type="FC3.1 Premature Termination",
            downstream_failure=True,
            failure_type="premature_termination",
        ),
    ]

    malignant = Trace(
        trace_id="test123b",
        execution_id="test123",
        variant=TraceVariant.MALIGNANT,
        events=mal_events,
    )

    return benign, malignant


class TestValidateGraphCorrectness(unittest.TestCase):
    """Tests for graph validation."""

    def _make_valid_graph(self) -> EntityGraph:
        builder = EntityGraphBuilder()
        trace, _ = _make_test_trace_pair()
        return builder.build(trace)

    def test_valid_graph_passes(self):
        graph = self._make_valid_graph()
        self.assertTrue(validate_graph_correctness(graph))

    def test_invalid_num_nodes(self):
        graph = self._make_valid_graph()
        graph.num_nodes = 999
        with self.assertRaises(AssertionError):
            validate_graph_correctness(graph)


class TestTraceAnalyzer(unittest.TestCase):
    """Tests for trace analysis."""

    def test_compare_traces(self):
        benign, malignant = _make_test_trace_pair()
        analyzer = TraceAnalyzer()
        diff = analyzer.compare_traces(benign, malignant)

        self.assertEqual(diff.execution_id, "test123")
        self.assertGreater(diff.lep_events_malignant, 0)
        self.assertIn("FC1.3", diff.lep_codes)

    def test_compare_pairs(self):
        benign, malignant = _make_test_trace_pair()
        diffs, summary = analyze_pairs([(benign, malignant)])

        self.assertEqual(len(diffs), 1)
        self.assertIn("num_comparisons", summary)
        self.assertGreater(summary["traces_with_lep"], 0)

    def test_jaccard_distance_identical(self):
        set_a = {1, 2, 3}
        set_b = {1, 2, 3}
        self.assertEqual(JaccardDistance.compute(set_a, set_b), 0.0)

    def test_jaccard_distance_disjoint(self):
        set_a = {1, 2}
        set_b = {3, 4}
        self.assertEqual(JaccardDistance.compute(set_a, set_b), 1.0)

    def test_jaccard_distance_partial(self):
        set_a = {1, 2, 3}
        set_b = {2, 3, 4}
        # intersection = {2,3}, union = {1,2,3,4}
        # jaccard = 1 - 2/4 = 0.5
        self.assertAlmostEqual(JaccardDistance.compute(set_a, set_b), 0.5)

    def test_structural_similarity(self):
        benign, malignant = _make_test_trace_pair()
        analyzer = TraceAnalyzer()
        diff = analyzer.compare_traces(benign, malignant)
        # Same number of events -> similarity 1.0
        self.assertEqual(diff.structural_similarity, 1.0)


class TestBenchmarkToGraphs(unittest.TestCase):
    """Tests for benchmark pipeline functions."""

    def test_benchmark_to_graphs(self):
        task = FinancialTask(Path("/tmp/test_workspace_b2g"))
        llm = MockLLMBackend()
        benign_graphs, malignant_graphs = benchmark_to_graphs(task, llm)

        self.assertEqual(len(benign_graphs), 1)
        self.assertEqual(len(malignant_graphs), 1)
        self.assertIsInstance(benign_graphs[0], EntityGraph)

    def test_benchmark_to_static_data(self):
        task = FinancialTask(Path("/tmp/test_workspace_b2sd"))
        llm = MockLLMBackend()
        data_list = benchmark_to_static_data(task, llm)
        self.assertEqual(len(data_list), 2)  # benign + malignant
        self.assertEqual(data_list[0].y.item(), 0.0)  # benign
        self.assertEqual(data_list[1].y.item(), 1.0)  # malignant

    def test_benchmark_to_temporal_data(self):
        task = FinancialTask(Path("/tmp/test_workspace_b2td"))
        llm = MockLLMBackend()
        data_list = benchmark_to_temporal_data(task, llm)
        self.assertEqual(len(data_list), 2)
        self.assertEqual(data_list[0].label, 0.0)
        self.assertEqual(data_list[1].label, 1.0)


if __name__ == "__main__":
    unittest.main()
