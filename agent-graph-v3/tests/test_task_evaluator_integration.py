"""Tests for task evaluator integration in ScenarioRunner."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from generation.runner import ScenarioRunner, RunResult, DryRunEvaluator
from schemas import ScenarioSpec, WorkflowConfig, Trace, TraceVariant
from schemas.trace_event import TraceEvent, TraceEventType
from evaluators.evaluation_result import EvaluationResult


def _make_spec(task_family="code_review", fixture_id="code_review_easy",
               condition="benign") -> ScenarioSpec:
    return ScenarioSpec(
        scenario_id=f"s_{task_family}_{fixture_id}_{condition}",
        task_family=task_family,
        task_variant="default",
        fixture_id=fixture_id,
        workflow_config=WorkflowConfig(topology="review_loop"),
        condition=condition,
        lep_configs=[],
    )


def _make_trace(scenario_id: str) -> Trace:
    event = TraceEvent(
        trace_id=f"{scenario_id}_a",
        event_id=f"{scenario_id}_e1",
        event_index=0,
        timestamp="0.0",
        event_type=TraceEventType.FINAL_RESPONSE,
        source_entity_id="agent_1",
        agent_id="agent_1",
        agent_role="inspector",
        event_labels=None,
        observable={},
        hidden={},
        depends_on=[],
    )
    return Trace(
        trace_id=f"{scenario_id}_a",
        execution_id=f"exec_{scenario_id}",
        variant=TraceVariant.BENIGN,
        events=[event],
    )


def _mock_task_evaluator(task_success: bool = True) -> MagicMock:
    """Create a mock task evaluator instance with .evaluate() returning EvaluationResult."""
    m = MagicMock()
    m.evaluate.return_value = EvaluationResult(
        task_success=task_success,
        downstream_failure=not task_success,
        failure_types=[],
        factual_score=1.0 if task_success else 0.0,
        completeness_score=1.0 if task_success else 0.0,
        provenance_score=0.0,
        policy_score=0.0,
        action_safety_score=0.0,
        required_items_found=["i1"] if task_success else [],
        required_items_missing=[] if task_success else ["i1"],
        forbidden_items_present=[],
        evaluator_confidence=0.0,
        evaluator_notes=[],
    )
    return m


def _dry_run(passed=True) -> dict:
    return {
        "condition": "benign",
        "passed": passed,
        "errors": [] if passed else ["propagation failed"],
        "injection_count": 0, "exposure_count": 0,
        "consumption_count": 0, "propagation_count": 0, "failure_count": 0,
    }


# ── Evaluation structure tests ─────────────────────────────────────────


def test_evaluation_has_three_tiers():
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ScenarioRunner(dry_run=True, output_dir=Path(tmpdir))
        trace = _make_trace("t1")
        spec = _make_spec("unknown_family", "some_fixture")
        with patch.object(runner.propagation_evaluator, "evaluate_benign",
                          return_value=_dry_run()):
            result = runner._evaluate(trace, spec, Path(tmpdir))
        assert "propagation" in result
        assert "task" in result
        assert "overall_passed" in result


def test_propagation_tier_keys():
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ScenarioRunner(dry_run=True, output_dir=Path(tmpdir))
        trace = _make_trace("t2")
        spec = _make_spec("unknown_family", "some_fixture")
        with patch.object(runner.propagation_evaluator, "evaluate_benign",
                          return_value=_dry_run()):
            result = runner._evaluate(trace, spec, Path(tmpdir))
        p = result["propagation"]
        for key in ("passed", "injection_count", "consumption_count",
                    "propagation_count", "failure_count", "errors"):
            assert key in p, f"missing key: {key}"


def test_task_tier_keys():
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ScenarioRunner(dry_run=True, output_dir=Path(tmpdir))
        trace = _make_trace("t3")
        spec = _make_spec("code_review", "code_review_easy")
        mock_eval = _mock_task_evaluator(True)
        with patch.object(runner.propagation_evaluator, "evaluate_benign",
                          return_value=_dry_run()):
            with patch("generation.runner.get_evaluator", return_value=mock_eval):
                result = runner._evaluate(trace, spec, Path(tmpdir))
        t = result["task"]
        for key in ("task_success", "downstream_failure", "factual_score",
                    "completeness_score", "required_items_found",
                    "required_items_missing", "forbidden_items_present", "errors"):
            assert key in t, f"missing key: {key}"


# ── Rename tests ───────────────────────────────────────────────────────


def test_propagation_evaluator_attribute_exists():
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ScenarioRunner(dry_run=True, output_dir=Path(tmpdir))
        assert hasattr(runner, "propagation_evaluator")
        assert isinstance(runner.propagation_evaluator, DryRunEvaluator)


def test_no_evaluator_attribute():
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ScenarioRunner(dry_run=True, output_dir=Path(tmpdir))
        assert not hasattr(runner, "evaluator"), (
            "runner.evaluator should be renamed to runner.propagation_evaluator"
        )


# ── Cache tests ────────────────────────────────────────────────────────


def test_cache_key_uses_family_and_fixture():
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ScenarioRunner(dry_run=True, output_dir=Path(tmpdir))
        fixture_root = Path(tmpdir)
        calls = [0]
        def mock_get(family, fixture_path=None):
            calls[0] += 1
            return _mock_task_evaluator()
        with patch("generation.runner.get_evaluator", side_effect=mock_get):
            a = runner._get_task_evaluator("code_review", "fixture_A", fixture_root)
            b = runner._get_task_evaluator("code_review", "fixture_B", fixture_root)
            a2 = runner._get_task_evaluator("code_review", "fixture_A", fixture_root)
        assert a is a2, "same fixture should be cached"
        assert calls[0] == 2, f"expected 2 calls, got {calls[0]}"


def test_unknown_family_returns_none():
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ScenarioRunner(dry_run=True, output_dir=Path(tmpdir))
        with patch("generation.runner.get_evaluator", return_value=None):
            result = runner._get_task_evaluator("nonexistent", "fx", Path(tmpdir))
        assert result is None


# ── Task evaluator invocation tests ───────────────────────────────────


def test_task_evaluator_invoked_for_known_family():
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ScenarioRunner(dry_run=True, output_dir=Path(tmpdir))
        trace = _make_trace("te1")
        spec = _make_spec("code_review", "code_review_easy")
        mock_eval = _mock_task_evaluator(True)
        with patch.object(runner.propagation_evaluator, "evaluate_benign",
                          return_value=_dry_run()):
            with patch("generation.runner.get_evaluator", return_value=mock_eval):
                result = runner._evaluate(trace, spec, Path(tmpdir))
        mock_eval.evaluate.assert_called_once()
        assert result["task"]["task_success"] is True


def test_task_evaluator_skipped_for_unknown_family():
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ScenarioRunner(dry_run=True, output_dir=Path(tmpdir))
        trace = _make_trace("te2")
        spec = _make_spec("unknown_family", "some_fixture")
        with patch.object(runner.propagation_evaluator, "evaluate_benign",
                          return_value=_dry_run()):
            result = runner._evaluate(trace, spec, Path(tmpdir))
        assert result["task"].get("note") == "no task evaluator for this family"


# ── Test matrix: overall_passed combinations ──────────────────────────


def test_pass_pass_overall_pass():
    """Propagation PASS + Task PASS → overall_passed=True."""
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ScenarioRunner(dry_run=True, output_dir=Path(tmpdir))
        trace = _make_trace("m1")
        spec = _make_spec("code_review", "code_review_easy")
        mock_eval = _mock_task_evaluator(True)
        with patch.object(runner.propagation_evaluator, "evaluate_benign",
                          return_value=_dry_run(True)):
            with patch("generation.runner.get_evaluator", return_value=mock_eval):
                result = runner._evaluate(trace, spec, Path(tmpdir))
        assert result["overall_passed"] is True


def test_pass_fail_overall_fail():
    """Propagation PASS + Task FAIL → overall_passed=False."""
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ScenarioRunner(dry_run=True, output_dir=Path(tmpdir))
        trace = _make_trace("m2")
        spec = _make_spec("code_review", "code_review_easy")
        mock_eval = _mock_task_evaluator(False)
        with patch.object(runner.propagation_evaluator, "evaluate_benign",
                          return_value=_dry_run(True)):
            with patch("generation.runner.get_evaluator", return_value=mock_eval):
                result = runner._evaluate(trace, spec, Path(tmpdir))
        assert result["overall_passed"] is False
        assert result["propagation"]["passed"] is True
        assert result["task"]["task_success"] is False


def test_fail_pass_overall_fail():
    """Propagation FAIL + Task PASS → overall_passed=False."""
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ScenarioRunner(dry_run=True, output_dir=Path(tmpdir))
        trace = _make_trace("m3")
        spec = _make_spec("code_review", "code_review_easy")
        mock_eval = _mock_task_evaluator(True)
        with patch.object(runner.propagation_evaluator, "evaluate_benign",
                          return_value=_dry_run(False)):
            with patch("generation.runner.get_evaluator", return_value=mock_eval):
                result = runner._evaluate(trace, spec, Path(tmpdir))
        assert result["overall_passed"] is False
        assert result["propagation"]["passed"] is False
        assert result["task"]["task_success"] is True


# ── RunResult field tests ──────────────────────────────────────────────


def test_run_result_propagation_and_task_fields():
    result = RunResult(
        scenario_id="test",
        trace=_make_trace("test"),
        success=True,
        propagation_passed=True,
        task_evaluator_passed=False,
    )
    assert result.propagation_passed is True
    assert result.task_evaluator_passed is False


def test_run_result_fields_default_none():
    result = RunResult(
        scenario_id="test",
        trace=_make_trace("test"),
        success=True,
    )
    assert result.propagation_passed is None
    assert result.task_evaluator_passed is None


if __name__ == "__main__":
    tests = [
        test_evaluation_has_three_tiers,
        test_propagation_tier_keys,
        test_task_tier_keys,
        test_propagation_evaluator_attribute_exists,
        test_no_evaluator_attribute,
        test_cache_key_uses_family_and_fixture,
        test_unknown_family_returns_none,
        test_task_evaluator_invoked_for_known_family,
        test_task_evaluator_skipped_for_unknown_family,
        test_pass_pass_overall_pass,
        test_pass_fail_overall_fail,
        test_fail_pass_overall_fail,
        test_run_result_propagation_and_task_fields,
        test_run_result_fields_default_none,
    ]
    passed = failed = 0
    for test in tests:
        try:
            test()
            print(f"  PASS  {test.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {test.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed out of {len(tests)} tests")
    sys.exit(1 if failed else 0)
