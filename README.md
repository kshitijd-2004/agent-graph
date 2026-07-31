# AgentGraphs

**Local Execution Perturbation (LEP) Detection in Multi-Agent Traces using GNNs.**

AgentGraphs converts agent execution traces into entity-node graphs and trains temporal graph neural networks to detect LEP injection attacks. LEP attacks embed malicious instructions inside trace events (agent names, tool names, reasoning steps) that bypass CLI layer defenses — GNNs operating on the graph structure and temporal dynamics can detect them.

## Architecture

```
traces/                  traces/
├─ benign/               ├─ benign/
│  └─ trace_XXXXa.jsonl   │  └─ trace_XXXXa.jsonl
└─ malicious/            └─ malicious/
   └─ trace_XXXXb.jsonl      └─ trace_XXXXb.jsonl
        ↓                       ↓
    JSONLTraceParser  →  EntityGraphBuilder
        ↓                       ↓
   Trace objects      →  EntityGraph (nodes+edges)
        ↓                       ↓
    GraphEncoder      →  StaticGraphData / TemporalGraphData
        ↓                       ↓
   GCN/GAT/SAGE       →  TGN/JODIE/TGAT
        ↓                       ↓
   Training loop ←─── Anomaly detection
```

### Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Graph structure | Entity-as-node | Persistent entities accumulate temporal memory |
| Format | JSONL | Streaming, append-only, MCP-compatible |
| Labeling | Paired (benign/malignant) share execution_id | Controls for setup variance |
| Static GNN | PyG | Standard GCN/GAT/GraphSAGE |
| Temporal GNN | DyGLib | TGN/JODIE/TGAT for event streams |

## Setup

```bash
# Install package
pip install -e ".[all]"

# Install all extras
pip install -e ".[pyg,dyglib,dev,notebook]"
```

## Quick Start

```python
from agentgraph import JSONLTraceParser, EntityGraphBuilder, GraphEncoder
from pathlib import Path

# 1. Parse JSONL traces
parser = JSONLTraceParser(Path("traces"))
pairs = parser.get_pairs()  # [(benign, malignant), ...]

# 2. Build entity-node graphs
builder = EntityGraphBuilder()
graphs = [builder.build(b) for b, _ in pairs] + [builder.build(m) for _, m in pairs]
labels = [0.0] * len(pairs) + [1.0] * len(pairs)

# 3. Encode for ML
encoder = GraphEncoder()
static_data, temporal_data = encoder.encode(graphs, labels)

# 4. Train a GNN
from torch_geometric.loader import DataLoader
loader = DataLoader(static_data, batch_size=32, shuffle=True)
# ... standard PyG training loop
```

## Benchmark Suite

```python
from benchmarks import run_benchmark_suite, FinancialTask, TaskCategory

results = run_benchmark_suite(
    output_dir=Path("./data"),
    num_runs_per_task=10,
    task_filter=[TaskCategory.FINANCIAL, TaskCategory.CODE_REVIEW],
)
print(f"Generated {results['summary']['total_graphs']} graphs")
```

## CLI Commands

```bash
# Generate benchmark traces
python -m agentgraphs.benchmarks generate --tasks financial --runs 10

# Convert traces to graphs
python -m agentgraphs.parser traces/ --output graphs/

# Analyze paired traces
python -m agentgraphs.pipeline analyze --pairs traces/

# Run end-to-end benchmark suite
python -m agentgraphs.pipeline suite --tasks all --runs 10
```

## Project Structure

```
agentgraphs/
├─ src/agentgraph/
│  ├─ __init__.py
│  ├─ trace.py          # Trace data structures (TraceEvent, Trace, TraceVariant)
│  ├─ entity.py         # Entity classification and encoding
│  ├─ parser.py         # JSONL trace parser
│  ├─ graph_builder.py  # Entity-node graph construction
│  ├─ encoder.py        # PyG / DyGLib encoding
│  └─ exporter.py       # CSV, torch, JSON export
├─ src/benchmarks/
│  ├─ __init__.py
│  ├─ benchmark.py      # Base classes and mock LLM
│  ├─ lep_injector.py   # LEP injection strategies
│  └─ tasks/            # Task implementations
│     ├─ financial.py
│     ├─ code_review.py
│     ├─ research.py
│     └─ competitive_intel.py
├─ src/pipeline/
│  ├─ __init__.py
│  ├─ pipeline.py       # High-level pipeline orchestration
│  └─ trace_analyzer.py # Graph analysis and comparison tools
├─ tests/
│  ├─ test_trace.py
│  ├─ test_entity.py
│  ├─ test_graph_builder.py
│  ├─ test_encoder.py
│  ├─ test_parser.py
│  ├─ test_exporter.py
│  ├─ test_benchmarks.py
│  ├─ test_integration.py
│  └─ test_end_to_end.py
├─ notebooks/
├─ pyproject.toml
└─ README.md
```

## Development

```bash
# Run tests
pytest tests/ -v

# Type checking
mypy src/

# Linting
ruff check src/

# Run all validators
python -c "from tests.validators import *; run_all_validators()"
```

## License

MIT
