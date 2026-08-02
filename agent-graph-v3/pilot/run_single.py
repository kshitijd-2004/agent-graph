"""Run a single scenario with real model for quick validation.

Usage:
    LLM_API_KEY=sk-... python -m pilot.run_single
    LLM_API_KEY=sk-... python -m pilot.run_single --task code_review --lep LEP_TOOL_RESULT_CORRUPTION
    LLM_API_KEY=sk-... python -m pilot.run_single --benign
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

_V3_ROOT = Path(__file__).resolve().parent.parent
if str(_V3_ROOT) not in sys.path:
    sys.path.insert(0, str(_V3_ROOT))

from schemas import ScenarioSpec, WorkflowConfig, LEPConfig, InjectionTrigger
from generation.runner import ScenarioRunner
from backend.api_backend import APIBackend
from pilot.config import LEP_BY_CODE, DEFAULT_WORKFLOW_CONFIG, PILOT_TASK_FAMILIES
from evaluators.base_evaluator import get_evaluator
from environment.workspace import Workspace

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("run_single")


def run_single(
    task_family: str = "code_review",
    condition: str = "benign",
    lep_code: str | None = None,
    fixture_id: str | None = None,
    output_dir: Path | None = None,
) -> dict:
    """Run a single scenario and print results."""
    v3_root = _V3_ROOT
    output_dir = output_dir or v3_root / "pilot_output"
    output_dir.mkdir(parents=True, exist_ok=True)
    fixture_root = v3_root / "workspace_fixtures"

    # Resolve fixture — map task family to a valid fixture ID
    _FIXTURE_MAP = {
        "code_review": "code_review_easy",
        "financial_analysis": "financial_clean",
        "research_synthesis": "research_conflicting",
    }
    if fixture_id is None:
        fixture_id = _FIXTURE_MAP.get(task_family, f"{task_family}_default")

    # Build workflow config
    wcfg = WorkflowConfig(
        topology=DEFAULT_WORKFLOW_CONFIG.topology,
        sharing_policy=DEFAULT_WORKFLOW_CONFIG.sharing_policy,
        memory_mode=DEFAULT_WORKFLOW_CONFIG.memory_mode,
        verification_mode=DEFAULT_WORKFLOW_CONFIG.verification_mode,
        max_events=40,
        max_agent_turns=20,
        timeout_seconds=300,
        model_name=DEFAULT_WORKFLOW_CONFIG.model_name,
        temperature=DEFAULT_WORKFLOW_CONFIG.temperature,
        seed=DEFAULT_WORKFLOW_CONFIG.seed,
        allow_parallel_agents=False,
        allow_retries=True,
    )

    # Build LEP configs
    lep_configs = []
    if condition == "single_lep" and lep_code:
        lep_configs = [LEP_BY_CODE[lep_code]]

    # Build scenario
    scenario_id = f"single_{condition}_{task_family}_{lep_code or 'none'}"
    spec = ScenarioSpec(
        scenario_id=scenario_id,
        task_family=task_family,
        task_variant="default",
        fixture_id=fixture_id,
        workflow_config=wcfg,
        lep_configs=lep_configs,
        condition=condition,
        repetition_index=0,
    )

    # Build backend and runner
    backend = APIBackend()
    runner = ScenarioRunner(llm_backend=backend, dry_run=False, output_dir=output_dir)

    logger.info("Running scenario: %s", scenario_id)
    logger.info("  Task: %s | Condition: %s | LEP: %s", task_family, condition, lep_code or "none")

    # Execute
    result = runner.run(spec, fixture_root)

    # Print trace summary
    logger.info("=" * 60)
    logger.info("TRACE SUMMARY")
    logger.info("=" * 60)
    logger.info("Trace ID: %s", result.trace.trace_id)
    logger.info("Events: %d", len(result.trace.events))
    logger.info("Runner success: %s", result.runner_success)
    logger.info("Task success: %s", result.task_success)
    logger.info("Termination reason: %s", result.termination_reason)
    if result.error:
        logger.info("Error: %s", result.error)

    # Print events
    for i, evt in enumerate(result.trace.events):
        labels = []
        if getattr(evt, "event_labels", None):
            if evt.event_labels.is_injection_origin:
                labels.append("INJECTION")
            if evt.event_labels.consumes_perturbed_info:
                labels.append("CONSUMED")
            if evt.event_labels.forwards_perturbed_info:
                labels.append("PROPAGATED")
            if evt.event_labels.introduces_downstream_failure:
                labels.append("FAILURE")
        label_str = f" [{', '.join(labels)}]" if labels else ""
        tool_str = f" ({evt.tool_name})" if evt.tool_name else ""
        logger.info(
            "  %3d. %s%s%s: %s",
            i,
            evt.event_type.value,
            tool_str,
            label_str,
            (evt.output_text or evt.input_text or "")[:100],
        )

    # Evaluate
    evaluator = get_evaluator(task_family)
    if evaluator:
        ws = Workspace(output_dir / f"ws_{scenario_id}")
        eval_result = evaluator.evaluate(result.trace, ws, spec)
        logger.info("=" * 60)
        logger.info("EVALUATION")
        logger.info("=" * 60)
        logger.info("Task success: %s", eval_result.task_success)
        logger.info("Factual score: %.2f", eval_result.factual_score)
        logger.info("Completeness: %.2f", eval_result.completeness_score)
        logger.info("Notes: %s", eval_result.evaluator_notes)

    # Save trace
    trace_path = output_dir / f"{scenario_id}_trace.json"
    with open(trace_path, "w") as f:
        json.dump(result.trace.to_dict(), f, indent=2, default=str)
    logger.info("Trace saved: %s", trace_path)

    return {
        "scenario_id": scenario_id,
        "runner_success": result.runner_success,
        "task_success": result.task_success,
        "termination_reason": result.termination_reason,
        "num_events": len(result.trace.events),
        "error": result.error,
    }


def main():
    parser = argparse.ArgumentParser(description="Run a single real-model scenario")
    parser.add_argument("--task", type=str, default="code_review",
                        choices=PILOT_TASK_FAMILIES,
                        help="Task family")
    parser.add_argument("--condition", type=str, default="benign",
                        choices=["benign", "single_lep", "counterfactual"],
                        help="Condition")
    parser.add_argument("--lep", type=str, default=None,
                        help="LEP code (for single_lep condition)")
    parser.add_argument("--fixture", type=str, default=None,
                        help="Fixture ID")
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()

    result = run_single(
        task_family=args.task,
        condition=args.condition,
        lep_code=args.lep,
        fixture_id=args.fixture,
        output_dir=args.output_dir,
    )

    if not result["runner_success"]:
        logger.warning("Runner failed: %s", result.get("error"))
        sys.exit(1)
    elif not result["task_success"]:
        logger.warning("Task not completed: termination=%s", result.get("termination_reason"))
        sys.exit(2)
    else:
        logger.info("Scenario completed successfully!")
        sys.exit(0)


if __name__ == "__main__":
    main()
