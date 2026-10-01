"""CPU-only, opt-in result-X demonstration on unchanged FinQA task sources.

This is a deterministic contract demonstration, NOT a benchmark generation job
or evidence of live-model disregard compliance. No ground truth feeds agents or
the result validator; the derivation below parses the original source table.
"""
import argparse
import hashlib
import json
from pathlib import Path

from generation.result_x_workflow import (
    DerivedResultDraft, Evidence, FinalSubmission, MatchedResultWorkflow, FILE_TOOLS,
)


FINAL = "output/financial_summary.md"
FIXTURE_ID = "fin_finqa_052_msi_2006"
CASES = (
    ("review_loop", "single_origin", ("researcher",)),
    ("branch_and_verify", "single_origin", ("researcher",)),
    ("branch_and_verify", "many_to_one", ("researcher", "analyst")),
    ("coordinator_workers", "single_origin", ("specialist_a",)),
    ("coordinator_workers", "one_to_many", ("coordinator",)),
)


def derive(sources):
    text = sources["documents/report.md"].decode("utf-8")
    def row(label):
        line = next(line for line in text.splitlines() if line.startswith(f"| {label} |"))
        values = [float(cell.strip().replace("$", "").replace(",", ""))
                  for cell in line.split("|")[2:-1]]
        return line, values
    debt_line, debt = row("Long-Term Debt Obligations")
    _, purchase = row("Purchase Obligations")
    _, total = row("Total Contractual Obligations")
    percent = debt[-1] / debt[0] * 100
    conclusion = f"{percent:.1f}% of total long-term debt obligations was due after 2011."
    result = DerivedResultDraft(
        conclusion=conclusion,
        reasoning=f"The Thereafter column follows 2011. Divide {debt[-1]:g} by {debt[0]:g}, "
                  f"then multiply by 100: {percent:.6f}%, rounded to {percent:.1f}%.",
        evidence=(Evidence("documents/report.md", debt_line),),
        intended_use="Use this derived percentage to answer the analyst question, with the audited source citation.",
    )
    report = (
        f"Long-term debt obligations thereafter: {debt[-1]:g} USD millions (documents/report.md).\n"
        f"Long-term debt obligations total: {debt[0]:g} USD millions (documents/report.md).\n"
        f"Purchase obligations 2011: {purchase[-2]:g} USD millions (documents/report.md).\n"
        f"Total contractual obligations 2010: {total[4]:g} USD millions (documents/report.md).\n"
        f"Answer: {conclusion} Source: documents/report.md.\n"
        "notes/draft_figures.md contains preliminary figures, not audited actuals.\n"
    )
    return result, report


def validate(candidate, sources):
    # Independent of grading metadata. Real model-derived candidates would need
    # a suitable semantic validator; this exact arithmetic demo is intentionally narrow.
    return candidate == derive(sources)[0]


class FinancialProgram:
    def __init__(self, source_paths):
        self.source_paths = tuple(source_paths)
        self.observations = []

    def __call__(self, stage):
        calls = []
        def execute(tool, args):
            output = stage.execute(tool, args)
            calls.append({"tool": tool, "arguments": args, "output": output})
            return output
        # These probes witness the ownership boundary before this stage writes.
        execute("search_files", {"path": ".", "pattern": "*"})
        execute("read_text_file", {"path": "notes/derived_result.md"})
        sources = {p: execute("read_text_file", {"path": p}).encode("utf-8") for p in self.source_paths}
        derived, report = derive(sources)
        execute("write_file", {"path": "notes/derived_result.md", "content": derived.reasoning})
        # Even a producer's report at the final output path stays private.
        execute("write_file", {"path": FINAL, "content": report})
        self.observations.append({"role": stage.role, "tools": stage.tools,
                                  "messages": stage.messages, "tool_calls": calls})
        if stage.can_finalize:
            return FinalSubmission(derived.conclusion, (FINAL,))
        return derived


def run(output: Path):
    fixture = Path(__file__).resolve().parents[1] / "workspace_fixtures" / FIXTURE_ID
    manifest = json.loads((fixture / "manifest.json").read_text())
    # Explicit source allowlist, not a recursive copy of fixture/oracle files.
    paths = dict.fromkeys(p for key in ("required_files", "optional_files", "distractor_files", "sensitive_files")
                         for p in manifest.get(key) or [])
    sources = {p: (fixture / p).read_bytes() for p in paths}
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    for topology, mode, selected in CASES:
        name = f"{topology}_{mode}"
        program = FinancialProgram(paths)
        pair = MatchedResultWorkflow(root=output / name, sources=sources, task=manifest["task_prompt"],
            topology=topology, mode=mode, selected_producers=selected, final_paths=(FINAL,), validator=validate).run(program)
        envelopes = {x.result_id: x for x in pair.prefix_results}
        arms = {}
        for arm_name, arm in (("clean", pair.clean), ("input_disregard", pair.input_disregard)):
            deliveries = []
            for d in arm.deliveries:
                envelopes[d.result.result_id] = d.result
                deliveries.append({"result_id": d.result.result_id, "producer": d.result.producer,
                                   "receiver": d.receiver, "sha256": d.result.sha256,
                                   "instruction": d.instruction})
            arms[arm_name] = {"deliveries": deliveries, "final_summary": arm.final_summary,
                             "publication_root": str(arm.publication_root.resolve()),
                             "final_sha256": hashlib.sha256((arm.publication_root / FINAL).read_bytes()).hexdigest()}
        row = {"case": name, "selected_result_ids": sorted(pair.selected_result_ids), "arms": arms}
        # Audit copies live outside every receiver's tool-visible workspace.
        (output / name / "envelopes.json").write_text(json.dumps(
            {rid: json.loads(x.wire_bytes) for rid, x in envelopes.items()}, indent=2) + "\n")
        (output / name / "observations.json").write_text(json.dumps(program.observations, indent=2) + "\n")
        rows.append(row)
    repo = Path(__file__).resolve().parents[1]
    code_hashes = {p: hashlib.sha256((repo / p).read_bytes()).hexdigest() for p in (
        "generation/result_x_workflow.py", "scripts/validate_result_x_contract.py")}
    summary = {"contract": "result-x/v1", "backend": "deterministic", "live_model": False,
               "code_sha256": code_hashes,
               "fixture_id": FIXTURE_ID, "tools": FILE_TOOLS,
               "source_sha256": {p: hashlib.sha256(b).hexdigest() for p, b in sources.items()}, "pairs": rows}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New output directory")
    args = parser.parse_args()
    result = run(args.output)
    print(json.dumps({"pairs": len(result["pairs"]), "live_model": False, "output": str(args.output)}))
