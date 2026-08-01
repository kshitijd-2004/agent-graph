#!/usr/bin/env python3
"""Generate traces using an API-based LLM (Claude Sonnet, GPT-4o, etc.).

Usage:
    # Uses defaults from environment:
    #   LLM_API_KEY, LLM_BASE_URL (default: https://api.opusmax.pro/v1), LLM_MODEL (default: claude-sonnet-5)
    python3 run_api_traces.py

    # Or with explicit args:
    LLM_API_KEY=sk-... LLM_MODEL=claude-sonnet-5 python3 run_api_traces.py

    # Override per-run values:
    LLM_API_KEY=sk-... LLM_BASE_URL=https://api.opusmax.pro/v1 LLM_MODEL=claude-sonnet-5 \\
        python3 run_api_traces.py --tasks financial_analysis --runs 3
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from benchmarks import (
    BenchmarkSuite,
    FinancialTask,
    CodeReviewTask,
    ResearchTask,
    CompetitiveIntelligenceTask,
)
from benchmarks.api_backend import APIBackend
from agentgraph import EntityGraphBuilder, ExportManager, TraceEvent, TraceVariant


OUTPUT_DIR = Path("output")
TRACE_DIR = Path("traces")
WORKSPACE = Path("workspace")


def setup_dirs():
    for d in [OUTPUT_DIR, TRACE_DIR, WORKSPACE]:
        d.mkdir(exist_ok=True)
    docs = WORKSPACE / "documents"
    notes = WORKSPACE / "notes"
    docs.mkdir(exist_ok=True)
    notes.mkdir(exist_ok=True)


def create_workspace_files():
    """Create workspace files if they don't already exist."""
    files = {
        "documents/financial_report.md": (
            "# Q3 Financial Report\n"
            "- Revenue: $1,500,000 (+15% YoY)\n"
            "- Operating costs: $800,000\n"
            "- Net profit: $700,000 (47% margin)\n"
            "- Key risk: Supply chain delays\n"
        ),
        "documents/quarterly_earnings.md": (
            "# Q3 Quarterly Earnings\n"
            "- EPS: $2.45\n"
            "- Revenue beat: 3% above estimates\n"
            "- Guidance raised for Q4\n"
        ),
        "notes/meeting_notes.md": (
            "# Q3 Team Sync\n"
            "- Budget approved for Q4 expansion\n"
            "- Action: Review risk assessment by end of month\n"
        ),
        "documents/research_report.md": (
            "# AI Agents Research Report\n"
            "- Autonomous agents can perform complex tasks\n"
            "- Multi-agent collaboration shows promise\n"
            "- Key challenge: coordination overhead\n"
        ),
        "documents/literature_review.md": (
            "# Literature Review\n"
            "- 2024: Agent frameworks proliferate\n"
            "- 2025: Memory-augmented agents become standard\n"
            "- Gap: Limited evaluation of failure modes\n"
        ),
        "documents/competitor_data.md": (
            "# Competitor Data\n"
            "- Competitor A: Market leader, 35% share\n"
            "- Competitor B: Price challenger, 18% share\n"
            "- Our position: 22% share, growing\n"
        ),
        "documents/pricing_history.md": (
            "# Pricing History\n"
            "- Our price: $99/month (stable 12 months)\n"
            "- Competitor A: $149/month (premium)\n"
            "- Competitor B: $49/month (undercutting)\n"
        ),
        "src/main.py": "# main.py\ndef process_data(data):\n    return [x * 2 for x in data]\n",
        "src/utils.py": "# utils.py\ndef hash_password(pw):\n    import hashlib\n    return hashlib.md5(pw.encode()).hexdigest()\n",
        "tests/test_main.py": (
            "# test_main.py\n"
            "import unittest\n"
            "from main import process_data\n"
            "class TestMain(unittest.TestCase):\n"
            "    def test_process(self):\n"
            "        self.assertEqual(process_data([1, 2]), [2, 4])\n"
        ),
    }

    for rel_path, content in files.items():
        p = WORKSPACE / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_text(content)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Generate agent traces via API")
    parser.add_argument("--tasks", nargs="+",
                        default=["financial_analysis", "code_review", "research", "competitive_intelligence"],
                        help="Tasks to run")
    parser.add_argument("--runs", type=int, default=1, help="Runs per task")
    parser.add_argument("--model", default=os.environ.get("LLM_MODEL", "claude-sonnet-5"))
    parser.add_argument("--base-url", default=os.environ.get("LLM_BASE_URL", "https://api.opusmax.pro/v1"))
    parser.add_argument("--api-key", default=os.environ.get("LLM_API_KEY", ""))
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--temperature", type=float, default=0.1)
    args = parser.parse_args()

    setup_dirs()
    create_workspace_files()

    print(f"\nAPI Backend: {args.model}")
    print(f"Base URL:   {args.base_url}")
    print(f"Max tokens: {args.max_tokens}, Temperature: {args.temperature}\n")

    llm = APIBackend(
        api_key=args.api_key,
        base_url=args.base_url,
        model=args.model,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
    )

    task_map = {
        "financial_analysis": FinancialTask(WORKSPACE),
        "code_review": CodeReviewTask(WORKSPACE),
        "research": ResearchTask(WORKSPACE),
        "competitive_intelligence": CompetitiveIntelligenceTask(WORKSPACE),
    }

    tasks = [task_map[t] for t in args.tasks if t in task_map]
    if not tasks:
        print(f"No valid tasks. Available: {list(task_map.keys())}")
        sys.exit(1)

    suite = BenchmarkSuite(tasks, llm)

    # Open per-trace JSONL writers for real-time output
    trace_writers = {}  # key -> file handle

    def get_writer(task_name, variant, trace_id):
        key = f"{task_name}_{variant}"
        if key not in trace_writers:
            task_dir = TRACE_DIR / task_name
            task_dir.mkdir(exist_ok=True)
            path = task_dir / f"{task_name}_{variant}_{trace_id}.jsonl"
            fh = open(path, "w", buffering=1)
            trace_writers[key] = fh
            print(f"  Streaming trace to: {path}")
        return trace_writers[key]

    def trace_writer_factory(task_name, variant, trace_id):
        """Factory: called by generate_trace with its own IDs, returns a writer."""
        fh = get_writer(task_name, variant, trace_id)
        def writer(event_dict):
            fh.write(json.dumps(event_dict) + "\n")
        return writer

    print(f"\n=== Generating traces ({args.runs} runs per task) ===\n")
    results = suite.run_all(num_runs_per_task=args.runs, trace_writer_factory=trace_writer_factory)

    # Close all trace files
    for fh in trace_writers.values():
        fh.close()

    results_path = OUTPUT_DIR / "benchmark_results.json"
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to {results_path}")

    # Build graphs from saved traces
    print("\n=== Building graphs ===\n")
    builder = EntityGraphBuilder()
    exporter = ExportManager(OUTPUT_DIR)
    all_graphs = []
    all_labels = []

    for task in tasks:
        task_trace_dir = TRACE_DIR / task.TASK_NAME
        for variant, variant_suffix in [("benign", "a"), ("malignant", "b")]:
            for run_idx in range(args.runs):
                # Match both old (benign/malignant) and new (a/b) naming
                patterns = [
                    f"{task.TASK_NAME}_{variant_suffix}_*.jsonl",
                    f"{task.TASK_NAME}_{variant}_*.jsonl",
                ]
                matching = []
                for pat in patterns:
                    matching.extend(sorted(task_trace_dir.glob(pat)))
                # Deduplicate and sort
                matching = sorted(set(matching), key=lambda p: p.name)
                if run_idx < len(matching):
                    path = matching[run_idx]
                else:
                    print(f"  WARNING: no trace found for {task.TASK_NAME} {variant} run {run_idx}")
                    continue

                from agentgraph.trace import Trace
                events = []
                with open(path) as f:
                    for line in f:
                        events.append(TraceEvent.from_dict(json.loads(line)))
                trace = Trace(
                    trace_id=path.stem,  # full stem is the trace_id
                    execution_id=path.stem.rsplit("_", 1)[-1][:10],
                    variant=TraceVariant.BENIGN if variant == "benign" else TraceVariant.MALIGNANT,
                    events=events, file_path=str(path),
                )

                graph = builder.build(trace)
                label = 0.0 if variant == "benign" else 1.0
                all_graphs.append(graph)
                all_labels.append(label)

                csv_path = exporter.export_dyglib_dataset([graph], f"{task.TASK_NAME}_{variant}_r{run_idx}")
                print(f"  {path.name}: {trace.num_events} events → {csv_path}")

    # Save PyG graphs
    from agentgraph import GraphEncoder
    encoder = GraphEncoder()
    static_data, _ = encoder.encode(all_graphs, all_labels)
    pt_path = exporter.save_torch(static_data, "all_graphs.pt")
    print(f"\nPyTorch graphs saved: {pt_path}")

    # Summary
    print("\n=== Summary ===")
    for task_name, data in results.items():
        for run in data["runs"]:
            print(f"  {task_name}: benign={run['benign_events']} events, "
                  f"malignant={run['malignant_events']} events")
    print("\nDone.")


if __name__ == "__main__":
    main()
