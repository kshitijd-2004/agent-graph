"""agent-graph-v2: Agent trace generation with environmental LEP injection.

A redesign of the benchmark where LEPs enter through the environment
(poisoned files, corrupted tool results, misleading memory records)
rather than through the system prompt.

Architecture:
- memory/: TF-IDF based memory store with retrieval
- environment/: Workspace with file-based tools and LEP injection
- tasks/: Task definitions with natural-language prompts only
- backend/: LLM API backend
- trace.py: Trace data structures (copied from agentgraph with additions)
- graph_builder.py, exporter.py, encoder.py: Copied from agentgraph
- run_traces.py: Main entry point
"""
