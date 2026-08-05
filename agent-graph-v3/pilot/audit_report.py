"""Pilot audit report generator.

Produces a comprehensive Markdown report covering:
- Firing: did each LEP's trigger fire?
- Exposure: was the corrupted value visible to the target agent?
- Consumption: did downstream agents use the corrupted data?
- Propagation: did the effect spread to more than one event?
- Task outcome: did the agent succeed or fail at the task?
- Evaluator correctness: does the evaluator agree with task success?
- Label correctness: are event labels (injection, consumption, propagation)
  correct and consistent?
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditReport:
    """Generates and writes a manual-audit-ready Markdown report."""

    def __init__(self, records: list[dict[str, Any]], output_dir: Path):
        self.records = records
        self.output_dir = output_dir
        self.issues: list[dict[str, Any]] = []

    # ── Firing audit ─────────────────────────────────────────────────────────

    def audit_firing(self) -> dict[str, Any]:
        """Check whether each LEP's trigger fired."""
        results = {"pass": 0, "fail": 0, "details": []}
        for rec in self.records:
            if rec["condition"] == "benign":
                continue
            lep_codes = rec.get("lep_codes", [])
            fired = rec.get("injection_fired", False)
            injection_events = rec.get("injection_events", [])

            if not lep_codes:
                continue  # counterfactual with no LEPs

            if fired and len(injection_events) == len(lep_codes):
                results["pass"] += 1
                results["details"].append({
                    "execution_id": rec["execution_id"],
                    "lep_codes": lep_codes,
                    "status": "PASS",
                    "injection_events": injection_events,
                })
            else:
                results["fail"] += 1
                issue = {
                    "execution_id": rec["execution_id"],
                    "lep_codes": lep_codes,
                    "status": "FAIL",
                    "expected_fire": lep_codes,
                    "actual_injection_events": injection_events,
                    "issue": f"Expected {len(lep_codes)} injection event(s), got {len(injection_events)}",
                }
                results["details"].append(issue)
                self.issues.append({"category": "firing", **issue})
        return results

    # ── Exposure audit ───────────────────────────────────────────────────────

    def audit_exposure(self) -> dict[str, Any]:
        """Check whether the perturbed information was exposed to downstream agents."""
        results = {"pass": 0, "fail": 0, "details": []}
        for rec in self.records:
            if rec["condition"] != "single_lep":
                continue
            injection = rec.get("injection_events", [])
            propagation = rec.get("propagation_events", [])
            consumed = rec.get("consumption_events", [])

            exposed = len(propagation) > 0 or len(consumed) > 0
            if exposed:
                results["pass"] += 1
                results["details"].append({
                    "execution_id": rec["execution_id"],
                    "status": "PASS",
                    "propagation_events": propagation,
                    "consumption_events": consumed,
                })
            else:
                results["fail"] += 1
                issue = {
                    "execution_id": rec["execution_id"],
                    "status": "FAIL",
                    "injection_events": injection,
                    "issue": "Injection fired but no downstream propagation or consumption recorded",
                }
                results["details"].append(issue)
                self.issues.append({"category": "exposure", **issue})
        return results

    # ── Consumption audit ────────────────────────────────────────────────────

    def audit_consumption(self) -> dict[str, Any]:
        """Check whether downstream agents consumed the perturbed data."""
        results = {"pass": 0, "fail": 0, "details": []}
        for rec in self.records:
            if rec["condition"] != "single_lep":
                continue
            consumed = rec.get("consumption_events", [])

            if consumed:
                results["pass"] += 1
                results["details"].append({
                    "execution_id": rec["execution_id"],
                    "status": "PASS",
                    "consumption_events": consumed,
                })
            else:
                results["fail"] += 1
                issue = {
                    "execution_id": rec["execution_id"],
                    "status": "FAIL",
                    "issue": "No consumption events recorded despite injection",
                }
                results["details"].append(issue)
                self.issues.append({"category": "consumption", **issue})
        return results

    # ── Propagation audit ────────────────────────────────────────────────────

    def audit_propagation(self) -> dict[str, Any]:
        """Check whether the LEP effect propagated through the trace."""
        results = {"pass": 0, "fail": 0, "details": []}
        for rec in self.records:
            if rec["condition"] != "single_lep":
                continue
            propagation = rec.get("propagation_events", [])
            consumed = rec.get("consumption_events", [])
            total = len(propagation) + len(consumed)

            if total > 1:
                results["pass"] += 1
                results["details"].append({
                    "execution_id": rec["execution_id"],
                    "status": "PASS",
                    "total_labeled_events": total,
                    "propagation": len(propagation),
                    "consumption": len(consumed),
                })
            else:
                results["fail"] += 1
                issue = {
                    "execution_id": rec["execution_id"],
                    "status": "FAIL",
                    "total_labeled_events": total,
                    "issue": f"Expected multi-hop propagation, only {total} event(s) labeled",
                }
                results["details"].append(issue)
                self.issues.append({"category": "propagation", **issue})
        return results

    # ── Task outcome audit ───────────────────────────────────────────────────

    def audit_task_outcome(self) -> dict[str, Any]:
        """Check whether task outcome matches the condition."""
        results = {"pass": 0, "fail": 0, "details": []}
        for rec in self.records:
            condition = rec["condition"]
            task_success = rec.get("task_success", False)
            downstream_failure = rec.get("downstream_failure", False)

            if condition == "benign":
                expected_success = True
            elif condition == "single_lep":
                # Perturbed: task should fail (due to injected anomaly)
                expected_success = False
            elif condition == "counterfactual":
                expected_success = True
            else:
                expected_success = True

            if task_success == expected_success:
                results["pass"] += 1
                results["details"].append({
                    "execution_id": rec["execution_id"],
                    "status": "PASS",
                    "condition": condition,
                    "task_success": task_success,
                    "expected": expected_success,
                })
            else:
                results["fail"] += 1
                issue = {
                    "execution_id": rec["execution_id"],
                    "status": "FAIL",
                    "condition": condition,
                    "task_success": task_success,
                    "expected": expected_success,
                    "issue": f"Expected task_success={expected_success} for condition={condition}, got {task_success}",
                }
                results["details"].append(issue)
                self.issues.append({"category": "task_outcome", **issue})
        return results

    # ── Evaluator correctness audit ──────────────────────────────────────────

    def audit_evaluator_correctness(self) -> dict[str, Any]:
        """Check whether evaluator results align with task outcomes."""
        results = {"pass": 0, "fail": 0, "details": []}
        for rec in self.records:
            evaluator_passed = rec.get("evaluator_passed", True)
            task_success = rec.get("task_success", False)

            # Evaluator should pass when task succeeds, fail otherwise
            expected_eval = task_success
            if evaluator_passed == expected_eval:
                results["pass"] += 1
                results["details"].append({
                    "execution_id": rec["execution_id"],
                    "status": "PASS",
                    "task_success": task_success,
                    "evaluator_passed": evaluator_passed,
                })
            else:
                results["fail"] += 1
                issue = {
                    "execution_id": rec["execution_id"],
                    "status": "FAIL",
                    "task_success": task_success,
                    "evaluator_passed": evaluator_passed,
                    "issue": f"Evaluator says passed={evaluator_passed} but task_success={task_success}",
                }
                results["details"].append(issue)
                self.issues.append({"category": "evaluator_correctness", **issue})
        return results

    # ── Label correctness audit ──────────────────────────────────────────────

    def audit_label_correctness(self) -> dict[str, Any]:
        """Check consistency of event-level labels within each trace."""
        results = {"pass": 0, "fail": 0, "details": []}
        for rec in self.records:
            issues = []
            trace = rec.get("trace")
            if trace is None:
                results["fail"] += 1
                results["details"].append({
                    "execution_id": rec["execution_id"],
                    "status": "FAIL",
                    "issue": "No trace data available",
                })
                continue

            events = trace.events
            injection_ids = {e.event_index for e in events
                            if getattr(e, "event_labels", None) and e.event_labels.is_injection_origin}
            consumption_ids = {e.event_index for e in events
                              if getattr(e, "event_labels", None) and e.event_labels.consumes_perturbed_info}
            propagation_ids = {e.event_index for e in events
                              if getattr(e, "event_labels", None) and e.event_labels.forwards_perturbed_info}

            # Rule 1: every consumption must follow an injection
            for cid in consumption_ids:
                if not any(cid > iid for iid in injection_ids if iid is not None):
                    issues.append(f"Consumption event {cid} has no prior injection")

            # Rule 2: propagation events should not outnumber injection
            if len(propagation_ids) > len(injection_ids) * 3:
                issues.append(
                    f"Propagation count ({len(propagation_ids)}) suspiciously high "
                    f"vs injection count ({len(injection_ids)})"
                )

            # Rule 3: benign traces should have no injection labels
            if rec["condition"] == "benign":
                if injection_ids or consumption_ids or propagation_ids:
                    issues.append(
                        f"Benign trace has LEP labels: injection={injection_ids}, "
                        f"consumption={consumption_ids}, propagation={propagation_ids}"
                    )

            # Rule 4: counterfactual should have no LEP labels
            if rec["condition"] == "counterfactual":
                if injection_ids or consumption_ids or propagation_ids:
                    issues.append(
                        f"Counterfactual trace has LEP labels: "
                        f"injection={injection_ids}, consumption={consumption_ids}, "
                        f"propagation={propagation_ids}"
                    )

            if not issues:
                results["pass"] += 1
                results["details"].append({
                    "execution_id": rec["execution_id"],
                    "status": "PASS",
                    "injection_count": len(injection_ids),
                    "consumption_count": len(consumption_ids),
                    "propagation_count": len(propagation_ids),
                })
            else:
                results["fail"] += 1
                issue = {
                    "execution_id": rec["execution_id"],
                    "status": "FAIL",
                    "issues": issues,
                }
                results["details"].append(issue)
                self.issues.append({"category": "label_correctness", **issue})
        return results

    # ── Full report ──────────────────────────────────────────────────────────

    def full_report(self) -> str:
        """Generate complete Markdown audit report."""
        lines = []
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        total = len(self.records)
        benign = sum(1 for r in self.records if r["condition"] == "benign")
        perturbed = sum(1 for r in self.records if r["condition"] == "single_lep")
        counter = sum(1 for r in self.records if r["condition"] == "counterfactual")
        task_families = sorted(set(r["task_family"] for r in self.records))
        lep_codes = sorted(set(
            c for r in self.records for c in r.get("lep_codes", [])
        ))

        # ── Header ────────────────────────────────────────────────────────────
        lines.append("# AgentGraph V3 Pilot Audit Report")
        lines.append("")
        lines.append(f"**Generated:** {ts}")
        lines.append(f"**Pilot ID:** agent-graph-v3-pilot-2026-08")
        lines.append(f"**Schema Version:** 3.0.0")
        lines.append("")
        lines.append("## Summary")
        lines.append("")
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Total executions | {total} |")
        lines.append(f"| Benign baselines | {benign} |")
        lines.append(f"| Perturbed (LEP) | {perturbed} |")
        lines.append(f"| Counterfactuals | {counter} |")
        lines.append(f"| Task families | {', '.join(task_families)} |")
        lines.append(f"| LEPs tested | {', '.join(lep_codes) if lep_codes else 'none (benign/counterfactual)'} |")
        lines.append(f"| Execution mode | {'dry-run' if all(r.get('success') and not r.get('error') and not r.get('trace') for r in self.records) else 'real-model'} |")
        lines.append("")

        # ── Firing ───────────────────────────────────────────────────────────
        firing = self.audit_firing()
        lines.append("## 1. Trigger Firing")
        lines.append("")
        lines.append(f"**Pass: {firing['pass']} / {firing['pass'] + firing['fail']}**")
        lines.append("")
        lines.append("| Execution | LEP | Status | Injection Event |")
        lines.append("|-----------|-----|--------|-----------------|")
        for d in firing["details"]:
            lep_str = ", ".join(d.get("lep_codes", []))
            evt_str = ", ".join(d.get("injection_events", [])) or "N/A"
            lines.append(f"| {d['execution_id']} | {lep_str} | {d['status']} | {evt_str} |")
        lines.append("")

        # ── Exposure ─────────────────────────────────────────────────────────
        exposure = self.audit_exposure()
        lines.append("## 2. Perturbation Exposure")
        lines.append("")
        lines.append(f"**Pass: {exposure['pass']} / {exposure['pass'] + exposure['fail']}**")
        lines.append("")
        if exposure["details"]:
            lines.append("| Execution | Status | Propagation | Consumption |")
            lines.append("|-----------|--------|-------------|-------------|")
            for d in exposure["details"]:
                lines.append(f"| {d['execution_id']} | {d['status']} | "
                           f"{len(d.get('propagation_events', []))} | "
                           f"{len(d.get('consumption_events', []))} |")
        lines.append("")

        # ── Consumption ──────────────────────────────────────────────────────
        consumption = self.audit_consumption()
        lines.append("## 3. Perturbation Consumption")
        lines.append("")
        lines.append(f"**Pass: {consumption['pass']} / {consumption['pass'] + consumption['fail']}**")
        lines.append("")
        lines.append("Did downstream agents actually use the corrupted/poisoned data?")
        lines.append("")

        # ── Propagation ──────────────────────────────────────────────────────
        propagation = self.audit_propagation()
        lines.append("## 4. Propagation Depth")
        lines.append("")
        lines.append(f"**Pass: {propagation['pass']} / {propagation['pass'] + propagation['fail']}**")
        lines.append("")
        lines.append("Multi-hop propagation indicates the perturbation spread beyond "
                    "the immediately affected agent.")
        lines.append("")

        # ── Task outcome ─────────────────────────────────────────────────────
        outcome = self.audit_task_outcome()
        lines.append("## 5. Task Outcome")
        lines.append("")
        lines.append(f"**Pass: {outcome['pass']} / {outcome['pass'] + outcome['fail']}**")
        lines.append("")
        lines.append("| Execution | Condition | Task Success | Expected | Status |")
        lines.append("|-----------|-----------|-------------|----------|--------|")
        for d in outcome["details"]:
            lines.append(f"| {d['execution_id']} | {d['condition']} | "
                       f"{d['task_success']} | {d['expected']} | {d['status']} |")
        lines.append("")

        # ── Evaluator correctness ────────────────────────────────────────────
        eval_correct = self.audit_evaluator_correctness()
        lines.append("## 6. Evaluator Correctness")
        lines.append("")
        lines.append(f"**Pass: {eval_correct['pass']} / {eval_correct['pass'] + eval_correct['fail']}**")
        lines.append("")
        lines.append("Does the task evaluator agree with the observed task outcome?")
        lines.append("")
        lines.append("| Execution | Task Success | Evaluator Pass | Status |")
        lines.append("|-----------|-------------|----------------|--------|")
        for d in eval_correct["details"]:
            lines.append(f"| {d['execution_id']} | {d['task_success']} | "
                       f"{d['evaluator_passed']} | {d['status']} |")
        lines.append("")

        # ── Label correctness ────────────────────────────────────────────────
        label_audit = self.audit_label_correctness()
        lines.append("## 7. Label Correctness")
        lines.append("")
        lines.append(f"**Pass: {label_audit['pass']} / {label_audit['pass'] + label_audit['fail']}**")
        lines.append("")
        lines.append("Checks:")
        lines.append("- Consumption events follow injection events")
        lines.append("- Propagation count is not unreasonably high")
        lines.append("- Benign traces have no LEP labels")
        lines.append("- Counterfactual traces have no LEP labels")
        lines.append("")

        # ── Per-execution summary table ──────────────────────────────────────
        lines.append("## 8. Per-Execution Summary")
        lines.append("")
        lines.append("| ID | Task | Condition | LEPs | Events | Injected | "
                    "Consumed | Propagated | Failure | Success | Eval Pass |")
        lines.append("|----|------|-----------|------|--------|----------|"
                    "----------|------------|---------|---------|-----------|")
        for rec in self.records:
            lep_str = ", ".join(rec.get("lep_codes", [])) or "—"
            inj = "✓" if rec.get("injection_fired") else "✗"
            cons = len(rec.get("consumption_events", []))
            prop = len(rec.get("propagation_events", []))
            fail = "✓" if rec.get("downstream_failure") else "✗"
            succ = "✓" if rec.get("task_success") else "✗"
            evp = "✓" if rec.get("evaluator_passed") else "✗"
            lines.append(
                f"| {rec['execution_id']} | {rec['task_family']} | "
                f"{rec['condition']} | {lep_str} | {rec.get('num_events', '?')} | "
                f"{inj} | {cons} | {prop} | {fail} | {succ} | {evp} |"
            )
        lines.append("")

        # ── Issues ───────────────────────────────────────────────────────────
        if self.issues:
            lines.append("## 9. Issues Requiring Fixes")
            lines.append("")
            lines.append(f"**Total issues: {len(self.issues)}**")
            lines.append("")
            lines.append("| # | Category | Execution | Issue |")
            lines.append("|---|----------|-----------|-------|")
            for i, issue in enumerate(self.issues, 1):
                lines.append(f"| {i} | {issue['category']} | {issue['execution_id']} | "
                           f"{issue.get('issue', str(issue))} |")
            lines.append("")
        else:
            lines.append("## 9. Issues")
            lines.append("")
            lines.append("**No issues detected.**")
            lines.append("")

        # ── Recommendations ──────────────────────────────────────────────────
        lines.append("## 10. Recommendations")
        lines.append("")
        if not self.issues:
            lines.append("- All checks passed. Pilot is ready to scale.")
            lines.append("- Proceed to Milestone 2 (additional task families and LEPs).")
        else:
            lines.append("### Required fixes before scaling:")
            lines.append("")
            for issue in self.issues:
                lines.append(f"- [{issue['category']}] {issue['execution_id']}: "
                           f"{issue.get('issue', str(issue))}")
            lines.append("")
            lines.append("### Next steps:")
            lines.append("1. Address each identified issue")
            lines.append("2. Re-run the pilot")
            lines.append("3. Verify all checks pass")
            lines.append("4. Scale to full benchmark (100+ executions)")
            lines.append("5. Proceed to Milestone 2")

        return "\n".join(lines)

    def write_report(self) -> Path:
        """Write the audit report to a Markdown file."""
        report_path = self.output_dir / "pilot_audit_report.md"
        report = self.full_report()
        report_path.write_text(report)
        return report_path

    def write_records_json(self) -> Path:
        """Write all records as a JSON file for further analysis."""
        json_path = self.output_dir / "pilot_records.json"
        json_path.write_text(json.dumps(self.records, indent=2, default=str))
        return json_path
