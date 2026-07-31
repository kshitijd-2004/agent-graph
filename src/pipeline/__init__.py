"""Pipeline module for AgentGraphs.

High-level pipeline: benchmark generation -> trace parsing -> graph building -> encoding -> export.
"""

from pipeline.pipeline import (
    benchmark_to_graphs,
    benchmark_to_static_data,
    benchmark_to_temporal_data,
    build_graphs_from_traces,
    run_benchmark_suite,
    validate_graph_correctness,
)
from pipeline.trace_analyzer import (
    JaccardDistance,
    TraceAnalyzer,
    analyze_graphs,
    analyze_pairs,
)

__all__ = [
    "run_benchmark_suite",
    "build_graphs_from_traces",
    "benchmark_to_graphs",
    "benchmark_to_static_data",
    "benchmark_to_temporal_data",
    "validate_graph_correctness",
    "TraceAnalyzer",
    "analyze_pairs",
    "analyze_graphs",
    "JaccardDistance",
]
