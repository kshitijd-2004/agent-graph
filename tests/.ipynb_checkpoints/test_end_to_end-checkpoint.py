"""
End-to-end pipeline test: run the benchmark suite and validate outputs.

This test:
1. Generates traces using FM2Benchmark
2. Converts traces to graphs using the pipeline
3. Validates graph correctness
4. Runs all sanity checks
"""

import json
import sys
import unittest
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from agentgraph import (
    EntityGraphBuilder,
    EntityGraph,
    ExportManager,
    GraphEncoder,
    JSONLTraceParser,
    StaticGraphData,
    TemporalGraphData,
    Trace,
    TraceEvent,
    TraceEventType,
    TraceVariant,
)
from benchmarks import (
    FinancialTask,
    CodeReviewTask,
    ResearchTask,
    FM2Benchmark,
    MockLLMBackend,
    TaskCategory,
    TraceConfig,
)
from pipeline import (
    benchmark_to_static_data,
    benchmark_to_temporal_data,
    run_benchmark_suite,
    validate_graph_correctness,
)


class TestEndToEnd(unittest.TestCase):
    """End-to-end tests for the complete pipeline."""

    def setUp(self):
        """Set up test fixtures."""
        self.output_dir = Path("/tmp/agentgraphs_test_output")
        self.output_dir.mkdir(exist_ok=True, parents=True)

        # Clean up previous test artifacts
        for f in self.output_dir.glob("*"):
            if f.is_file():
                f.unlink()
            elif f.is_dir():
                import shutil
                shutil.rmtree(f)

        # Create minimal workspace with mock files
        self.workspace = self.output_dir / "workspace"
        self.workspace.mkdir(exist_ok=True)

        # Create mock documents
        (self.workspace / "documents").mkdir(exist_ok=True)
        (self.workspace / "notes").mkdir(exist_ok=True)
        (self.workspace / "output").mkdir(exist_ok=True)

        (self.workspace / "documents" / "financial_report.md").write_text(
            "# Q3 Financial Report\n"
            "- Revenue: $1,500,000 (+15% YoY)\n"
            "- Operating costs: $800,000\n"
            "- Net profit: $700,000 (47% margin)\n"
            "- Key risk: Supply chain delays\n"
        )

        (self.workspace / "notes" / "meeting_notes.md").write_text(
            "# Q3 Team Sync\n"
            "- Budget approved for Q4 expansion\n"
            "- Action: Review risk assessment by end of month\n"
        )

    def test_full_pipeline_single_task(self):
        """Test the complete pipeline for a single task."""
        # 1. Create benchmark
        task = FinancialTask()
        llm = MockLLMBackend()
        config = TraceConfig(
            task_name="test_financial",
            max_events_per_run=20,
        )

        # 2. Generate trace
        traces = task.generate_traces(llm, config)

        # 3. Validate traces
        self.assertEqual(len(traces), 2)  # benign + malignant
        self.assertIn("benign", traces)
        self.assertIn("malignant", traces)

        benign = traces["benign"]
        malignant = traces["malignant"]

        # Verify basic trace properties
        self.assertGreaterEqual(benign.num_events, 1)
        self.assertGreaterEqual(malignant.num_events, 1)

        # 4. Build graphs
        builder = EntityGraphBuilder()
        benign_graph = builder.build(benign)
        malignant_graph = builder.build(malignant)

        # 5. Validate graphs
        self.assertTrue(validate_graph_correctness(benign_graph))
        self.assertTrue(validate_graph_correctness(malignant_graph))

        # 6. Encode for ML
        encoder = GraphEncoder()
        static_data, temporal_data = encoder.encode(
            [benign_graph, malignant_graph],
            labels=[0.0, 1.0]
        )

        self.assertEqual(len(static_data), 2)
        self.assertEqual(len(temporal_data), 2)

        # 7. Verify static data
        for sd in static_data:
            self.assertIsInstance(sd, StaticGraphData)
            self.assertIsInstance(sd.x, torch.Tensor)
            self.assertIsInstance(sd.edge_index, torch.Tensor)
            self.assertIsInstance(sd.y, torch.Tensor)
            self.assertEqual(sd.y.item(), 0.0 if sd.trace_id.endswith('a') else 1.0)

        # 8. Verify temporal data
        for td in temporal_data:
            self.assertIsInstance(td, TemporalGraphData)
            self.assertIsInstance(td.edges_u, torch.Tensor)
            self.assertIsInstance(td.edge_timestamps, torch.Tensor)

    def test_pipeline_batch(self):
        """Test running the full benchmark suite."""
        results = run_benchmark_suite(
            output_dir=self.output_dir,
            num_runs_per_task=1,
            task_filter=["financial"],
        )

        self.assertIn("summary", results)
        self.assertIn("benchmarks", results)
        self.assertGreaterEqual(results["summary"]["total_graphs"], 2)

    def test_jsonl_roundtrip(self):
        """Test saving and loading traces via JSONL."""
        task = FinancialTask()
        llm = MockLLMBackend()
        config = TraceConfig(task_name="test_roundtrip")

        traces = task.generate_traces(llm, config)

        # Save to JSONL
        parser = JSONLTraceParser(self.output_dir)
        for variant, trace in traces.items():
            trace_path = self.output_dir / f"roundtrip_{trace.trace_id}.jsonl"
            with open(trace_path, "w") as f:
                for event in trace.events:
                    f.write(json.dumps(event.to_dict()) + "\n")

        # Load back
        loaded = parser.parse_file(
            self.output_dir / f"roundtrip_{traces['benign'].trace_id}.jsonl"
        )

        self.assertEqual(loaded.num_events, traces["benign"].num_events)
        self.assertEqual(loaded.variant, traces["benign"].variant)
        self.assertEqual(loaded.execution_id, traces["benign"].execution_id)

    def test_export_manager(self):
        """Test the export manager with DyGLib format."""
        task = FinancialTask()
        llm = MockLLMBackend()
        config = TraceConfig(task_name="test_export")

        traces = task.generate_traces(llm, config)
        builder = EntityGraphBuilder()
        graphs = [builder.build(t) for t in traces.values()]

        exporter = ExportManager(self.output_dir)

        # Export DyGLib CSV
        csv_path = exporter.export_dyglib_dataset(graphs, "test_dataset")
        self.assertTrue(csv_path.exists())

        # Verify CSV content
        with open(csv_path) as f:
            lines = f.readlines()
            self.assertGreater(len(lines), 1)  # header + data

        # Save/load torch format
        encoder = GraphEncoder()
        static_data, _ = encoder.encode(graphs)
        pt_path = exporter.save_torch(static_data, "test_graphs.pt")
        loaded = exporter.load_torch("test_graphs.pt")
        self.assertEqual(len(loaded), len(static_data))


if __name__ == "__main__":
    unittest.main(verbosity=2)
