"""Ten-run generation smoke with deterministic or user-selected live vLLM backend.

Uses real fixtures, runner, mutations, memory, routing, admission, serialization,
and task evaluation. The backend never sees LEP identity or private labels.
"""
import argparse
import contextlib
import io
import hashlib
import json
import logging
from pathlib import Path

from backend.api_backend import ModelTurn, ToolCall
from benchmark.benchmark_runner import BenchmarkRunner
from generation.runner import ScenarioRunner
from generation.scenario_builder import ScenarioBuilder, ScenarioBuildConfig
from schemas import TraceEvent
from scripts.fixture_mock import FixtureBackend


CASES = [
    ("LEP_TOOL_RESULT_CORRUPTION", "branch_and_verify", "single_origin"),
    ("LEP_HANDOFF_CORRUPTION", "coordinator_workers", "one_to_many"),
    ("LEP_MEMORY_POISONING", "branch_and_verify", "many_to_one"),
    ("LEP_INDIRECT_PROMPT_INJECTION", "review_loop", "single_origin"),
    ("LEP_INPUT_DISREGARD", "branch_and_verify", "many_to_one"),
]


class CoordinationBackend(FixtureBackend):
    """Fixed coordination policy shared by clean/perturbed executions."""
    def generate(self, prompt="", tool_choice=None):
        if "read_memory" in self.tools and self.step == len(self.files) * 2 + 1:
            self.step += 1
            return ModelTurn(tool_call=ToolCall(id=f"{self.role}-{self.step}",
                name="read_memory", input={"query": "analysis_notes", "top_k": 3}),
                text="", stop_reason="tool_use")
        return super().generate(prompt, tool_choice)


def run(output, backend="deterministic", model=None, vllm_url=None):
    root = Path(__file__).resolve().parents[1] / "workspace_fixtures"
    fixture = "fin_finqa_052_msi_2006"
    manifest = json.loads((root / fixture / "manifest.json").read_text())
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    for code, topology, mode in CASES:
        for injected in (False, True):
            memory = code == "LEP_MEMORY_POISONING"
            lep = BenchmarkRunner._resolve_lep(None, code, "financial_analysis")
            spec = ScenarioBuilder().build_single(ScenarioBuildConfig(
                task_family="financial_analysis", fixture_id=fixture, task_variant="finqa",
                topology=topology, propagation_mode=mode, condition="single_lep" if injected else "benign",
                lep_configs=[lep] if injected else [], max_agent_turns=12,
                memory_mode="ephemeral_shared" if memory else "ephemeral_private"))
            name = f"{code}_{'perturbed' if injected else 'clean'}"
            print(f"Running {name}: {topology}/{mode}", flush=True)
            if backend == "vllm":
                from backend.hf_backend import HFBackend
                inference = HFBackend(model=model, base_url=vllm_url)
            else:
                inference = CoordinationBackend(manifest)
            with contextlib.redirect_stdout(io.StringIO()):
                result = ScenarioRunner(llm_backend=inference, dry_run=False,
                                        output_dir=output / name).run(spec, root)
            if not result.trace:
                raise AssertionError(result.error)
            result.trace.metadata["execution_variant"] = "memory_enabled" if memory else "standard"
            result.trace.metadata["smoke_backend"] = backend
            data = result.trace.to_dict()
            (output / f"{name}.json").write_text(json.dumps(data, indent=2) + "\n")
            restored = [TraceEvent.from_dict(e) for e in json.loads(json.dumps(data))["events"]]
            assert [e.to_dict() for e in restored] == [e.to_dict() for e in result.trace.events]
            rows.append({"case": name, "topology": topology, "mode": mode,
                "eligible": result.dataset_eligible, "termination": result.termination_reason,
                "runner_success": result.runner_success, "error": result.error,
                "task_success": result.trace.labels.task_success,
                "task_downstream_failure": result.trace.labels.downstream_failure,
                "trace_path": str((output / f"{name}.json").resolve()),
                "sequence": [{"id": e.event_id, "type": e.event_type.value,
                    "role": e.agent_role, "depends_on": e.depends_on,
                    "labels": {k: v for k, v in vars(e.event_labels).items() if v is True},
                    "delivered_origins": e.hidden.get("delivered_origins", []),
                    "processed": e.hidden.get("input_processing_evidence", [])}
                    for e in restored if e.event_type.value in {"agent_handoff", "memory_write",
                        "memory_retrieval", "topology_transition", "final_response"}
                    or e.event_labels.is_injection_origin or e.event_labels.consumes_perturbed_info]})
    repo = Path(__file__).resolve().parents[1]
    fingerprints = {name: hashlib.sha256((repo / name).read_bytes()).hexdigest()
                    for name in ["generation/stage_runner.py", "generation/runner.py",
                                 "generation/propagation_tracker.py", "memory/memory_store.py",
                                 "leps/indirect_prompt_injection.py", "training/pipeline.py"]}
    (output / "summary.json").write_text(json.dumps({"backend": backend,
        "code_sha256": fingerprints,
        "model": model, "live_model": backend == "vllm", "runs": rows}, indent=2) + "\n")
    print(json.dumps({"runs": len(rows), "eligible": sum(r["eligible"] for r in rows),
                      "summary": str(output / "summary.json")}))
    if not all(r["eligible"] for r in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--backend", choices=["deterministic", "vllm"], default="deterministic")
    parser.add_argument("--model", help="Model name served by the live endpoint")
    parser.add_argument("--vllm-url", default="http://127.0.0.1:8000/v1")
    args = parser.parse_args()
    if args.backend == "vllm" and not args.model:
        parser.error("--model is required for --backend vllm")
    logging.disable(logging.CRITICAL)
    run(args.output, args.backend, args.model, args.vllm_url)
