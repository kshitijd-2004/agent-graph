#!/usr/bin/env python3
"""Print the fixed seven-pair plan, or execute it only with explicit --run.

Uses an existing vLLM endpoint. Does not manage servers or modify fixtures.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.hf_backend import HFBackend
from benchmark.benchmark_runner import BenchmarkRunner
from generation.result_x_live_review import JSONModelReviewer, SourceReviewValidator
from generation.runner import ScenarioRunner
from generation.scenario_builder import ScenarioBuildConfig, ScenarioBuilder

REPO = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class LivePair:
    name: str
    family: str
    fixture: str
    variant: str
    domain: str
    topology: str
    mode: str
    final_path: str
    source_agent: str | None = None
    occurrence: int = 1
    topology_target: str | None = None


PAIRS = (
    LivePair("01_financial_forward", "financial_analysis", "fin_finqa_052_msi_2006", "finqa", "finqa",
             "review_loop", "single_origin", "output/financial_summary.md", "extractor"),
    LivePair("02_swe_reverse", "code_review", "code_review_swe_003_django_django_11880", "swe_bench_verified", "swe",
             "review_loop", "single_origin", "output/code_review_report.md", "reviewer"),
    LivePair("03_financial_revision", "financial_analysis", "fin_finqa_052_msi_2006", "finqa", "finqa",
             "review_loop", "single_origin", "output/financial_summary.md", "extractor", 2),
    LivePair("04_research_branch", "research_synthesis", "research_conflicting", "conflicting", "research",
             "branch_and_verify", "single_origin", "output/research_synthesis.md", topology_target="branch:synthesizer"),
    LivePair("05_financial_merge", "financial_analysis", "fin_finqa_052_msi_2006", "finqa", "finqa",
             "branch_and_verify", "many_to_one", "output/financial_summary.md"),
    LivePair("06_swe_worker", "code_review", "code_review_swe_003_django_django_11880", "swe_bench_verified", "swe",
             "coordinator_workers", "single_origin", "output/code_review_report.md", topology_target="worker:specialist_b"),
    LivePair("07_research_fanout", "research_synthesis", "research_conflicting", "conflicting", "research",
             "coordinator_workers", "one_to_many", "output/research_synthesis.md", topology_target="upstream:coordinator"),
)


def scenario_for(pair, model, temperature):
    lep = deepcopy(BenchmarkRunner._resolve_lep(None, "LEP_INPUT_DISREGARD", pair.family))
    lep.trigger.source_agent = pair.source_agent
    lep.trigger.occurrence = pair.occurrence
    lep.topology_target = pair.topology_target
    return ScenarioBuilder().build_single(ScenarioBuildConfig(
        task_family=pair.family, fixture_id=pair.fixture, task_variant=pair.variant,
        topology=pair.topology, propagation_mode=pair.mode, condition="single_lep", lep_configs=[lep],
        memory_mode="ephemeral_private", max_events=300, max_agent_turns=40,
        model_name=model, temperature=temperature,
    ))


def backend_factory(model, base_url, temperature, max_tokens):
    def create():
        backend = HFBackend(model=model, base_url=base_url, temperature=temperature, max_tokens=max_tokens)
        # The existing backend honors environment overrides. Explicit CLI values
        # define this experiment, so fix both arms and record exactly these values.
        backend.temperature, backend.max_tokens = temperature, max_tokens
        return backend
    return create


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    settings = {"model": args.model, "base_url": args.base_url, "temperature": args.temperature,
                "max_tokens": args.max_tokens, "review_model": args.review_model or args.model,
                "review_temperature": 0.0, "review_max_tokens": args.review_max_tokens,
                "memory_mode": "ephemeral_private", "max_events": 300, "max_agent_turns": 40}
    write_json(output / "plan.json", {"settings": settings, "pairs": [asdict(p) for p in PAIRS]})
    actor = backend_factory(args.model, args.base_url, args.temperature, args.max_tokens)
    reviewer_factory = backend_factory(args.review_model or args.model, args.base_url, 0.0, args.review_max_tokens)
    rows = []
    for pair in PAIRS:
        print(f"Starting {pair.name}: {pair.topology}/{pair.mode}", flush=True)
        started = time.monotonic()
        pair_dir = output / pair.name
        # Reviews are outside private stage workspaces; never fed back to agents.
        review_log = output / f"{pair.name}_reviews.jsonl"
        def log_review(record):
            with review_log.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        reviewer = JSONModelReviewer(reviewer_factory)
        validator = SourceReviewValidator(pair.domain, reviewer, audit=log_review)
        row = {"pair": pair.name, "status": "failed", "review_log": review_log.name}
        try:
            runner = ScenarioRunner(dry_run=False, max_events=300, output_dir=output)
            result = runner.run_result_x_pair(
                scenario_for(pair, args.model, args.temperature), args.fixture_root,
                backend_factory=actor, validator=validator, final_path=pair.final_path, pair_name=pair.name,
            )
            row["status"] = "completed" if all(r.dataset_eligible for r in (result.clean, result.input_disregard)) else "ineligible"
        except Exception as exc:
            # Keep production's original traces/audit even when an admission
            # rejection stops a pair. Never retry/select only favorable pairs.
            row["error"] = f"{type(exc).__name__}: {exc}"
        row.update(runtime_seconds=time.monotonic() - started, review_model_calls=reviewer.calls,
                   reviews_accepted=sum(r["accepted"] for r in validator.records),
                   review_cache_hits=sum(r["cache_hit"] for r in validator.records))
        files = ("result_x_audit.json", "clean_trace.json", "input_disregard_trace.json")
        row["artifacts"] = {name: str(pair_dir / name) if (pair_dir / name).exists() else None for name in files}
        # A failed clean prefix can cause the accepted runner to skip ID entirely.
        # Record that explicitly; do not manufacture a trace for an unexecuted arm.
        if not all(row["artifacts"].values()):
            row["missing_artifacts"] = [name for name, path in row["artifacts"].items() if path is None]
        audit_path = pair_dir / "result_x_audit.json"
        if audit_path.exists():
            audit = json.loads(audit_path.read_text())
            turns = [turn for arm in audit.get("arms", {}).values() for stage in arm["stages"] for turn in stage["turns"]]
            row["recorded_actor_model_calls"] = sum(not t["replayed"] for t in turns)
            row["replayed_actor_turns"] = sum(t["replayed"] for t in turns)
        rows.append(row)
        write_json(output / "summary.json", {"settings": settings, "pairs": rows,
                    "semantic_review_is_not_ID_compliance_validation": True})
        print(f"{pair.name}: {row['status']}; reviewer calls={reviewer.calls}", flush=True)
    return 0 if all(r["status"] == "completed" for r in rows) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Actually execute the seven pairs; default only prints the plan")
    parser.add_argument("--base-url", help="Existing vLLM base URL including /v1")
    parser.add_argument("--model", help="Exact served actor model ID")
    parser.add_argument("--review-model", help="Optional independently served reviewer model on the same endpoint")
    parser.add_argument("--output", type=Path, help="New output directory; existing directories are refused")
    parser.add_argument("--fixture-root", type=Path, default=REPO / "workspace_fixtures")
    parser.add_argument("--temperature", type=float, default=0.4)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--review-max-tokens", type=int, default=4096)
    args = parser.parse_args(argv)
    if not args.run:
        print(json.dumps({"will_execute": False, "pairs": [asdict(p) for p in PAIRS],
                          "memory_mode": "ephemeral_private", "max_events": 300, "max_agent_turns": 40}, indent=2))
        return 0
    if not args.base_url or not args.model or args.output is None:
        parser.error("--run requires --base-url, --model and --output")
    if not math.isfinite(args.temperature) or args.temperature < 0 or min(args.max_tokens, args.review_max_tokens) < 1:
        parser.error("Decoding settings must be finite and nonnegative, with positive token limits")
    if not args.base_url.startswith(("http://", "https://")):
        parser.error("--base-url must be an HTTP(S) endpoint")
    if args.output.exists():
        parser.error("Output directory already exists; use a fresh path")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
