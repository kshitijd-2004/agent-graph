#!/usr/bin/env python3
"""Run all benchmark tasks and generate paired traces using Llama 3.2 1B Instruct.

Usage:
    # From the project root (installs deps if needed):
    PYTHONPATH=src python3 run_traces.py

    # Or after pip install -e .:
    python3 run_traces.py
"""

import json
import sys
from pathlib import Path

# Ensure src/ is on the path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from benchmarks import (
    BenchmarkSuite,
    FinancialTask,
    CodeReviewTask,
    ResearchTask,
    CompetitiveIntelligenceTask,
)
from benchmarks.llama_backend import LlamaBackend
from agentgraph import EntityGraphBuilder, ExportManager


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
        p.write_text(content)

    print(f"Workspace ready: {WORKSPACE.resolve()}")


def main():
    setup_dirs()

    tasks = [
        FinancialTask(WORKSPACE),
        CodeReviewTask(WORKSPACE),
        ResearchTask(WORKSPACE),
        CompetitiveIntelligenceTask(WORKSPACE),
    ]

    print("\n=== Loading Llama 3.2 1B Instruct (this takes ~30s first time) ===\n")
    llm = LlamaBackend()

    suite = BenchmarkSuite(tasks, llm)

    print("\n=== Generating traces (this will take several minutes) ===\n")
    results = suite.run_all(num_runs_per_task=1)

    # Save results summary
    results_path = OUTPUT_DIR / "benchmark_results.json"
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to {results_path}")

    # Print summary
    for task_name, data in results.items():
        for run in data["runs"]:
            print(f"  {task_name}: benign={run['benign_events']} events, "
                  f"malignant={run['malignant_events']} events")

    # Save traces and build graphs
    print("\n=== Saving traces and graphs ===\n")
    builder = EntityGraphBuilder()
    exporter = ExportManager(OUTPUT_DIR)
    all_graphs = []
    all_labels = []

    for task in tasks:
        traces = task.generate_traces(llm)
        for variant, trace in traces.items():
            # Save JSONL
            path = TRACE_DIR / f"{task.TASK_NAME}_{variant}_{trace.trace_id}.jsonl"
            with open(path, "w") as f:
                for event in trace.events:
                    f.write(json.dumps(event.to_dict()) + "\n")
            print(f"  Saved: {path} ({trace.num_events} events)")

            # Build and export graph
            graph = builder.build(trace)
            label = 0.0 if variant == "benign" else 1.0
            all_graphs.append(graph)
            all_labels.append(label)

            # Export DyGLib CSV
            csv_path = exporter.export_dyglib_dataset([graph], f"{task.TASK_NAME}_{variant}")
            print(f"    DyGLib CSV: {csv_path}")

    # Save PyG graphs
    from agentgraph import GraphEncoder
    encoder = GraphEncoder()
    static_data, _ = encoder.encode(all_graphs, all_labels)
    pt_path = exporter.save_torch(static_data, "all_graphs.pt")
    print(f"\nPyTorch graphs saved: {pt_path}")
    print("\nDone.")


if __name__ == "__main__":
    main()
