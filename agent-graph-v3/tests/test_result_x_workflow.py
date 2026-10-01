"""CPU contract tests; identical final answers are deliberately acceptable."""
import json
from dataclasses import replace

import pytest

from generation.result_x_workflow import (
    ContractError, DerivedResultDraft, Evidence, FinalSubmission,
    MatchedResultWorkflow, ResultDelivery,
)


SOURCES = {
    "documents/revenue.txt": b"2024 revenue: 100\n2025 revenue: 120\n",
    # A fixture-provided report is an original source, NOT private scratch.
    "reports/reference.md": b"Use year-over-year growth.\n",
    "notes/context.txt": b"Both years use the same accounting basis.\n",
}
FINAL = "output/financial_summary.md"
CASES = [
    ("review_loop", "single_origin", ("researcher",)),
    ("branch_and_verify", "single_origin", ("analyst",)),
    ("branch_and_verify", "many_to_one", ("researcher", "analyst")),
    ("coordinator_workers", "single_origin", ("specialist_b",)),
    ("coordinator_workers", "one_to_many", ("coordinator",)),
]


def draft():
    return DerivedResultDraft(
        conclusion="Revenue grew by 20% from 2024 to 2025.",
        reasoning="(120 - 100) / 100 = 0.20; multiply by 100 to get 20%.",
        evidence=(Evidence("documents/revenue.txt", "2024 revenue: 100\n2025 revenue: 120"),),
        intended_use="Use the computed growth rate in the final financial summary.",
    )


def validate(candidate, sources):
    # This domain validator checks a meaningful derived claim, not length/keywords.
    amounts = [int(line.split(": ")[1]) for line in sources["documents/revenue.txt"].decode().splitlines()]
    growth = (amounts[1] - amounts[0]) / amounts[0] * 100
    return (candidate == draft() and growth == 20)


def make_workflow(tmp_path, topology="review_loop", mode="single_origin", selected=("researcher",), **kwargs):
    return MatchedResultWorkflow(
        root=tmp_path / "experiment", sources=SOURCES, task="Compute revenue growth and write " + FINAL,
        topology=topology, mode=mode, selected_producers=selected,
        final_paths=(FINAL,), validator=validate, **kwargs,
    )


class Program:
    """Trusted test backend; no condition flag or sibling workspace is supplied."""
    def __init__(self):
        self.inputs = []
        self.observations = []

    def __call__(self, stage):
        self.inputs.append(stage)
        before = stage.execute("search_files", {"path": ".", "pattern": "*"})
        # Probe report, arbitrary notes, nested outputs and attempted traversal.
        missing = [stage.execute("read_text_file", {"path": p}) for p in (
            "reports/derived.md", "notes/private.txt", "nested/cache/result.txt",
            "../researcher/reports/derived.md", "/producer/report.md",
        )]
        source_values = {p: stage.execute("read_text_file", {"path": p}) for p in SOURCES}
        local_write = stage.execute("write_file", {"path": "documents/revenue.txt", "content": "copy of X"})
        assert stage.execute("read_text_file", {"path": "documents/revenue.txt"}) == "copy of X"
        self.observations.append((stage.role, before, missing, source_values, local_write, stage.tools))
        for p in ("reports/derived.md", "notes/private.txt", "nested/cache/result.txt"):
            stage.execute("write_file", {"path": p, "content": "DERIVED-SENTINEL: " + draft().reasoning})
        if stage.can_finalize:
            stage.execute("write_file", {"path": FINAL, "content": "Revenue growth: 20%.\n"})
            return FinalSubmission("Revenue growth is 20%.", (FINAL,))
        return draft()


@pytest.mark.parametrize("topology,mode,selected", CASES)
def test_matched_workflow_contract(tmp_path, topology, mode, selected):
    program = Program()
    workflow = make_workflow(tmp_path, topology, mode, selected)
    result = workflow.run(program)
    # Prefix ran once, so byte identity does not depend on deterministic LLMs.
    prefix = result.prefix_results
    assert {x.producer for x in prefix} == set(result.prefix_producers)
    assert len({x.result_id for x in prefix}) == len(prefix)
    selected_ids = {x.result_id for x in prefix if x.producer in selected}
    assert result.selected_result_ids == selected_ids
    paired_count = 0
    for clean, perturbed in zip(result.clean.deliveries, result.input_disregard.deliveries):
        if clean.result.result_id not in {x.result_id for x in prefix}:
            continue  # Worker replies may legitimately differ after the intervention.
        paired_count += 1
        assert clean.receiver == perturbed.receiver
        assert clean.result.producer == perturbed.result.producer
        assert clean.result.result_id == perturbed.result.result_id
        assert clean.result.wire_bytes == perturbed.result.wire_bytes
        assert clean.result.sha256 == perturbed.result.sha256
        assert clean.instruction == ""
        assert bool(perturbed.instruction) == (clean.result.result_id in selected_ids)
        assert clean.messages()[0] == perturbed.messages()[0]
        if perturbed.instruction:
            assert clean.result.result_id in perturbed.instruction
            assert clean.result.producer in perturbed.instruction
            assert "independently" in perturbed.instruction
            assert len(perturbed.messages()) == 2
        else:
            assert clean.messages() == perturbed.messages()
        decoded = json.loads(clean.result.wire_bytes)
        assert decoded["reasoning"] == draft().reasoning
        assert decoded["intended_use"] == draft().intended_use
        assert decoded["evidence"][0]["path"] == "documents/revenue.txt"
    assert paired_count == (3 if mode == "one_to_many" else len(prefix))
    assert len(result.clean.deliveries) == len(result.input_disregard.deliveries)

    # Apart from the separate result-scoped instruction, paired messages match.
    continuation_stages = program.inputs[len(prefix):]
    mid = len(continuation_stages) // 2
    for clean, perturbed in zip(continuation_stages[:mid], continuation_stages[mid:]):
        assert clean.role == perturbed.role
        instructions = {d.instruction for d in perturbed.incoming if d.instruction}
        assert clean.messages == tuple(m for m in perturbed.messages if m["content"] not in instructions)

    # No incoming attachments are materialized as another tool-readable copy.
    # Every stage, including branch B and fan-out workers, starts from sources only.
    for role, listing, misses, values, local_write, tools in program.observations:
        assert "derived.md" not in listing
        assert "private.txt" not in listing
        assert "result.txt" not in listing
        assert all(v.startswith("Error:") for v in misses)
        assert values == {p: b.decode() for p, b in SOURCES.items()}
        assert local_write.startswith("Write completed successfully.")
        assert tools == program.observations[0][-1]
    assert len(program.inputs) == len(prefix) + (8 if mode == "one_to_many" else 2)
    for run in (result.clean, result.input_disregard):
        assert run.final_summary == "Revenue growth is 20%."
        assert (run.publication_root / FINAL).read_bytes() == b"Revenue growth: 20%.\n"
        assert sorted(p.relative_to(run.publication_root).as_posix()
                      for p in run.publication_root.rglob("*") if p.is_file()) == [FINAL]
    assert SOURCES["documents/revenue.txt"] == b"2024 revenue: 100\n2025 revenue: 120\n"


def test_fan_in_keeps_each_result_and_scopes_only_selected_branch(tmp_path):
    program = Program()
    result = make_workflow(tmp_path, "branch_and_verify", "single_origin", ("analyst",)).run(program)
    receiver = program.inputs[-1]
    assert receiver.role == "verifier"
    assert [d.result.producer for d in receiver.incoming] == ["researcher", "analyst"]
    assert receiver.incoming[0].instruction == ""
    assert receiver.incoming[1].result.result_id in receiver.incoming[1].instruction
    assert len(result.input_disregard.deliveries) == 2
    assert "researcher+analyst" not in str(receiver.messages)


def test_fan_out_reuses_coordinator_identity_and_returns_distinct_worker_results(tmp_path):
    program = Program()
    result = make_workflow(tmp_path, "coordinator_workers", "one_to_many", ("coordinator",)).run(program)
    deliveries = result.input_disregard.deliveries
    assert {d.receiver for d in deliveries[:3]} == {"specialist_a", "specialist_b", "synthesizer"}
    assert len({d.result.result_id for d in deliveries[:3]}) == 1
    assert all(d.instruction for d in deliveries[:3])
    replies = deliveries[3:]
    assert {d.result.producer for d in replies} == {"specialist_a", "specialist_b", "synthesizer"}
    assert all(d.receiver == "coordinator" and not d.instruction for d in replies)
    # Scope is explicitly the targeted receiving stage, not a permanent ban.
    assert all(d.result.input_result_ids == (result.prefix_results[0].result_id,) for d in replies)


@pytest.mark.parametrize("bad", [
    replace(draft(), conclusion=""),
    replace(draft(), reasoning=""),
    replace(draft(), intended_use=""),
    replace(draft(), evidence=()),
    replace(draft(), conclusion="Analysis complete."),
    replace(draft(), conclusion="Revenue fell by 20%."),
    replace(draft(), evidence=(Evidence("reports/derived.md", "DERIVED-SENTINEL"),)),
    replace(draft(), evidence=(Evidence("documents/revenue.txt", "revenue: 900"),)),
])
def test_invalid_or_administrative_result_cannot_be_an_id_target(tmp_path, bad):
    def program(stage):
        stage.execute("read_text_file", {"path": "documents/revenue.txt"})
        return bad
    with pytest.raises(ContractError):
        make_workflow(tmp_path).run(program)
    assert not list((tmp_path / "experiment").glob("published/*/output/*"))


def test_evidence_requires_actual_source_read(tmp_path):
    with pytest.raises(ContractError, match="read"):
        make_workflow(tmp_path).run(lambda stage: draft())


def test_reading_a_private_source_overwrite_is_not_original_evidence(tmp_path):
    def program(stage):
        stage.execute("write_file", {"path": "documents/revenue.txt", "content": "Invented facts"})
        stage.execute("read_text_file", {"path": "documents/revenue.txt"})
        return draft()
    with pytest.raises(ContractError, match="read"):
        make_workflow(tmp_path).run(program)


def test_semantic_validator_required_and_failure_cannot_silently_pass(tmp_path):
    with pytest.raises(ContractError, match="validator"):
        MatchedResultWorkflow(root=tmp_path, sources=SOURCES, task="task", topology="review_loop",
            mode="single_origin", selected_producers=("researcher",), final_paths=(FINAL,), validator=None)


@pytest.mark.parametrize("topology,mode,selected", [
    ("coordinator_workers", "many_to_one", ("specialist_a",)),
    ("branch_and_verify", "single_origin", ("verifier",)),
    ("branch_and_verify", "single_origin", ("researcher", "analyst")),
    ("branch_and_verify", "many_to_one", ("researcher",)),
])
def test_invalid_scope_rejected(tmp_path, topology, mode, selected):
    with pytest.raises(ContractError):
        make_workflow(tmp_path, topology, mode, selected)


def test_no_publication_until_final_submission_and_no_resume_after_publication(tmp_path):
    workflow = make_workflow(tmp_path)
    delegate = Program()
    def program(stage):
        assert not list((tmp_path / "experiment" / "published").rglob("*.md")) or stage.can_finalize
        return delegate(stage)
    result = workflow.run(program)
    with pytest.raises(ContractError, match="once"):
        workflow.run(program)
    assert (result.clean.publication_root / FINAL).exists()


@pytest.mark.parametrize("paths", [(), ("reports/derived.md",), ("../escape.md",)])
def test_final_publication_requires_declared_artifact_set(tmp_path, paths):
    delegate = Program()
    def program(stage):
        answer = delegate(stage)
        return replace(answer, artifact_paths=paths) if stage.can_finalize else answer
    with pytest.raises(ContractError):
        make_workflow(tmp_path).run(program)


def test_result_identity_survives_readdressing_without_rewriting_x(tmp_path):
    result = make_workflow(tmp_path).run(Program())
    original = result.clean.deliveries[0]
    forwarded = ResultDelivery(original.result, "another_receiver")
    assert forwarded.result.wire_bytes == original.result.wire_bytes
    assert forwarded.result.producer == "researcher"
    assert forwarded.result.result_id == original.result.result_id
    assert forwarded.receiver != original.receiver


def test_retained_stage_tool_handle_is_closed(tmp_path):
    program = Program()
    make_workflow(tmp_path).run(program)
    assert all(stage.execute("read_text_file", {"path": "reports/derived.md"})
               == "Error: this stage has completed." for stage in program.inputs)


def test_search_cannot_discover_sibling_artifacts(tmp_path):
    delegate = Program()
    def program(stage):
        for pattern in ("../*", "../../**/*", "/tmp/*"):
            assert stage.execute("search_files", {"pattern": pattern}).startswith("Error:")
        return delegate(stage)
    make_workflow(tmp_path).run(program)


def test_final_artifact_must_exist_before_publication(tmp_path):
    delegate = Program()
    def program(stage):
        if stage.can_finalize:
            return FinalSubmission("Completed", (FINAL,))
        return delegate(stage)
    with pytest.raises(ContractError, match="not written"):
        make_workflow(tmp_path).run(program)
    assert not (tmp_path / "experiment" / "published").exists()


def test_semantic_validator_exceptions_fail_closed(tmp_path):
    workflow = make_workflow(tmp_path)
    def unavailable(*args):
        raise RuntimeError("No validation result")
    workflow.validator = unavailable
    with pytest.raises(ContractError, match="validator failed"):
        workflow.run(Program())


def test_real_finqa_demo_keeps_existing_evaluator_publication_contract(tmp_path):
    from pathlib import Path
    from types import SimpleNamespace
    from environment.workspace import Workspace
    from evaluators.task_evaluators.financial_evaluator import FinancialEvaluator
    from scripts.validate_result_x_contract import run, FIXTURE_ID

    fixture = Path(__file__).resolve().parents[1] / "workspace_fixtures" / FIXTURE_ID
    evaluator = FinancialEvaluator(fixture_path=fixture)
    trace = SimpleNamespace(events=[])
    summary = run(tmp_path / "demo")
    assert len(summary["pairs"]) == 5
    for pair in summary["pairs"]:
        outputs = []
        for arm in pair["arms"].values():
            workspace = Workspace(Path(arm["publication_root"]))
            # Existing collector sees the exact final-path report, not private scratch.
            collected = evaluator._collect_output_text(trace, workspace)
            assert collected == (workspace.root / FINAL).read_text()
            assert "35.1%" in collected
            extracted = evaluator._extract_figures(collected)
            assert extracted == {"fact_1": 1451.0, "fact_2": 4134.0,
                                 "fact_3": 12.0, "fact_4": 724.0, "answer": 35.1}
            grading = evaluator.evaluate(trace, workspace, SimpleNamespace(fixture_id=FIXTURE_ID))
            assert grading.task_success
            assert grading.factual_score == 1.0
            assert not grading.downstream_failure
            outputs.append(collected)
        # Equal successful task answers are an allowed outcome of the contract.
        assert outputs[0] == outputs[1]


def test_delivered_result_is_not_truncated_or_normalized(tmp_path):
    candidate = replace(draft(), reasoning="Derivation with Unicode Δ — café.\n" + draft().reasoning * 300)
    workflow = make_workflow(tmp_path)
    workflow.validator = lambda result, sources: result == candidate
    delegate = Program()
    def program(stage):
        answer = delegate(stage)
        return answer if stage.can_finalize else candidate
    pair = workflow.run(program)
    clean = pair.clean.deliveries[0].messages()[0]["content"]
    perturbed = pair.input_disregard.deliveries[0].messages()[0]["content"]
    assert clean.encode("utf-8") == perturbed.encode("utf-8")
    assert json.loads(clean)["reasoning"] == candidate.reasoning


def test_source_path_and_final_path_cannot_escape_or_overlap(tmp_path):
    for sources, finals in [({"../hidden": b"data"}, (FINAL,)), (SOURCES, ("documents/revenue.txt",))]:
        with pytest.raises(ContractError):
            MatchedResultWorkflow(root=tmp_path, sources=sources, task="task", topology="review_loop",
                mode="single_origin", selected_producers=("researcher",), final_paths=finals, validator=validate)
