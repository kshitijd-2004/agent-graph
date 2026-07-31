"""High-level pipeline for AgentGraphs.

Orchestrates the full workflow: benchmark generation -> trace parsing ->
graph building -> encoding -> export.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agentgraph import (
    EntityGraphBuilder,
    EntityGraph,
    ExportManager,
    GraphEncoder,
    JSONLTraceParser,
    StaticGraphData,
    TemporalGraphData,
    Trace,
    TraceVariant,
)
from benchmarks import (
    BenchmarkSuite,
    BenchmarkTask,
    CompetitiveIntelligenceTask,
    CodeReviewTask,
    FinancialTask,
    MockLLMBackend,
    ResearchTask,
    TaskCategory,
    TraceConfig,
)

logger = logging.getLogger(__name__)


def build_graphs_from_traces(
    parser: JSONLTraceParser,
    builder: Optional[EntityGraphBuilder] = None,
) -> Tuple[List[EntityGraph], List[EntityGraph]]:
    """Parse traces and build entity-node graphs.

    Args:
        parser: JSONL trace parser.
        builder: Optional graph builder (creates new one if not provided).

    Returns:
        Tuple of (benign_graphs, malignant_graphs).
    """
    if builder is None:
        builder = EntityGraphBuilder()

    pairs = parser.get_pairs()

    benign_graphs: List[EntityGraph] = []
    malignant_graphs: List[EntityGraph] = []

    for benign_trace, malignant_trace in pairs:
        benign_graphs.append(builder.build(benign_trace))
        malignant_graphs.append(builder.build(malignant_trace))

    return benign_graphs, malignant_graphs


def benchmark_to_graphs(
    task: BenchmarkTask,
    llm: MockLLMBackend,
    config: Optional[TraceConfig] = None,
) -> Tuple[List[EntityGraph], List[EntityGraph]]:
    """Run a benchmark task and convert traces to graphs.

    Args:
        task: Benchmark task to run.
        llm: Mock LLM backend.
        config: Optional trace configuration.

    Returns:
        Tuple of (benign_graphs, malignant_graphs).
    """
    traces = task.generate_traces(llm, config)

    benign = traces.get("benign")
    malignant = traces.get("malignant")

    if benign is None or malignant is None:
        raise ValueError("Benchmark did not produce both benign and malignant traces")

    builder = EntityGraphBuilder()
    benign_graph = builder.build(benign)
    malignant_graph = builder.build(malignant)

    return [benign_graph], [malignant_graph]


def benchmark_to_static_data(
    task: BenchmarkTask,
    llm: MockLLMBackend,
    config: Optional[TraceConfig] = None,
    labels: Optional[List[float]] = None,
) -> List[StaticGraphData]:
    """Run a benchmark and return PyG-compatible data.

    Args:
        task: Benchmark task.
        llm: Mock LLM backend.
        config: Optional trace configuration.
        labels: Optional labels (0.0=benign, 1.0=malignant).

    Returns:
        List of StaticGraphData objects.
    """
    benign_graphs, malignant_graphs = benchmark_to_graphs(task, llm, config)
    all_graphs = benign_graphs + malignant_graphs

    if labels is None:
        labels = [0.0] * len(benign_graphs) + [1.0] * len(malignant_graphs)

    encoder = GraphEncoder()
    return encoder.encode_static(all_graphs, labels)


def benchmark_to_temporal_data(
    task: BenchmarkTask,
    llm: MockLLMBackend,
    config: Optional[TraceConfig] = None,
    labels: Optional[List[float]] = None,
) -> List[TemporalGraphData]:
    """Run a benchmark and return DyGLib-compatible data.

    Args:
        task: Benchmark task.
        llm: Mock LLM backend.
        config: Optional trace configuration.
        labels: Optional labels.

    Returns:
        List of TemporalGraphData objects.
    """
    benign_graphs, malignant_graphs = benchmark_to_graphs(task, llm, config)
    all_graphs = benign_graphs + malignant_graphs

    if labels is None:
        labels = [0.0] * len(benign_graphs) + [1.0] * len(malignant_graphs)

    encoder = GraphEncoder()
    return encoder.encode_temporal(all_graphs, labels)


def validate_graph_correctness(graph: EntityGraph) -> bool:
    """Validate that an EntityGraph is well-formed.

    Checks:
    - num_nodes matches len(nodes)
    - num_edges matches edge_index.shape[1]
    - edge_attr has correct shape
    - edge_timestamps has correct shape
    - All node IDs are unique
    - All edge indices are within bounds

    Args:
        graph: EntityGraph to validate.

    Returns:
        True if valid, raises AssertionError otherwise.
    """
    assert graph.num_nodes == len(graph.nodes), \
        f"num_nodes ({graph.num_nodes}) != len(nodes) ({len(graph.nodes)})"
    assert graph.num_edges == graph.edge_index.shape[1], \
        f"num_edges mismatch: {graph.num_edges} vs {graph.edge_index.shape[1]}"
    assert graph.edge_attr.shape[0] == graph.num_edges, \
        f"edge_attr rows ({graph.edge_attr.shape[0]}) != num_edges ({graph.num_edges})"
    assert graph.edge_timestamps.shape[0] == graph.num_edges, \
        f"edge_timestamps length ({graph.edge_timestamps.shape[0]}) != num_edges ({graph.num_edges})"
    assert graph.edge_event_types.shape[0] == graph.num_edges, \
        f"edge_event_types length mismatch"

    node_ids = [n.entity_id for n in graph.nodes]
    assert len(node_ids) == len(set(node_ids)), "Duplicate node IDs"

    if graph.num_edges > 0:
        max_idx = graph.edge_index.max().item()
        assert max_idx < graph.num_nodes, \
            f"Edge index {max_idx} out of bounds for {graph.num_nodes} nodes"

    return True


def run_benchmark_suite(
    output_dir: Path,
    num_runs_per_task: int = 1,
    task_filter: Optional[List[TaskCategory]] = None,
) -> Dict[str, Any]:
    """Run the full benchmark suite.

    Args:
        output_dir: Directory to save outputs.
        num_runs_per_task: Number of runs per task.
        task_filter: Optional list of task categories to run.

    Returns:
        Summary dictionary with results.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    tasks: List[BenchmarkTask] = []
    task_map = {
        TaskCategory.FINANCIAL: FinancialTask,
        TaskCategory.CODE_REVIEW: CodeReviewTask,
        TaskCategory.RESEARCH: ResearchTask,
        TaskCategory.COMPETITIVE_INTELLIGENCE: CompetitiveIntelligenceTask,
    }

    categories = task_filter or list(TaskCategory)
    for cat in categories:
        if cat in task_map:
            tasks.append(task_map[cat](output_dir / "workspace"))

    llm = MockLLMBackend()
    suite = BenchmarkSuite(tasks, llm)

    results = suite.run_all(num_runs_per_task=num_runs_per_task)

    # Save results
    results_path = output_dir / "benchmark_results.json"
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2, default=str)

    return results
