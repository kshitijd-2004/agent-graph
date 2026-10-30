"""Scan all production Input Disregard traces.

Emits one JSONL record per trace with:
  - injection metadata (variant, target, instruction)
  - delivery evidence (verifiable exposure, roles actually receiving it)
  - the DELIVERED handoff block as rendered into the receiver's first reasoning input
  - applicability signals computed against that delivered block
  - receiver behaviour (tool calls, whether it re-reads sources)
  - runtime labels and evaluator scores
"""
import glob
import json
import os
import re
import sys
from collections import Counter

OUT = sys.argv[1] if len(sys.argv) > 1 else "records.jsonl"
NODE_GLOB = "production_run/node_*/traces/*INPUT_DISREGARD*.json"

HANDOFF_MARK = "[Handoff received from"

# warning / cautionary language actually used in the task domain
WARN = re.compile(
    r"\b(warning|warnings|warned|caution|cautionary|caveat|red flag|risk flag|"
    r"risk factor|risk assessment|flagged|potential issue|discrepancy|discrepant|"
    r"inconsisten\w*|verify \w+ before|uncertain|oversight|be careful)\b",
    re.I,
)
# language expressing temporal ORDERING of evidence (not just any year token)
TEMPORAL_ORDER = re.compile(
    r"\b(most recent|recent|recently|latest|newest|newer|older|oldest|earlier|"
    r"prior (year|period)|last year|previous year|updated|updated? (as of|to)|"
    r"publication year|chronolog\w*|time[- ]?stamped)\b",
    re.I,
)
# bare year tokens (fiscal years, publication years) -- present but not ordering
YEAR = re.compile(r"\b(19|20)\d{2}\b")
# a genuine review finding / identified defect
FINDING = re.compile(
    r"\b(identified (a |the )?(issue|defect|bug|problem|finding)|potential issue|"
    r"the issue|bug|defect|finding:|findings?:)",
    re.I,
)


def delivered_block(first_reasoning_input):
    """Extract the rendered handoff block from the receiver's first reasoning input."""
    if not first_reasoning_input:
        return "", []
    msgs = first_reasoning_input
    if isinstance(msgs, str):
        try:
            msgs = json.loads(msgs)
        except Exception:
            msgs = [{"content": msgs}]
    block = ""
    for m in msgs if isinstance(msgs, list) else []:
        c = m.get("content", "") if isinstance(m, dict) else str(m)
        i = c.find(HANDOFF_MARK)
        if i >= 0:
            block = c[i:]
            break
    return block, msgs


def main():
    rows = []
    for f in glob.glob(NODE_GLOB):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        evs = d["events"]
        meta = d["metadata"]
        labels = d["labels"]
        hoff_events = [e for e in evs if e.get("hidden", {}).get("input_disregard")]
        if not hoff_events:
            continue
        h0 = hoff_events[0]
        im = h0["hidden"]["input_disregard"]
        instr = im.get("instruction", "")
        tgt = im.get("target_agent")
        hoff = h0["event_index"]

        later = [e for e in evs if e["event_index"] > hoff]
        exposing = [e for e in later if instr[:60] in (e.get("input_text") or "")]
        recv = [e for e in later if e.get("agent_role") == tgt]
        first_reason = next(
            (e for e in recv if e["event_type"] == "reasoning"), None
        )
        block, _ = delivered_block(first_reason.get("input_text")) if first_reason else ("", [])

        tools = Counter(
            e.get("tool_name") for e in recv if e["event_type"] == "tool_call"
        )
        final_evt = [e for e in evs if e["event_type"] == "final_response"]
        final_text = (final_evt[-1].get("output_text") or "") if final_evt else ""

        # last reasoning output of the target = its substantive contribution
        tgt_reasons = [e for e in recv if e["event_type"] == "reasoning"]
        tgt_out = (tgt_reasons[-1].get("output_text") or "") if tgt_reasons else ""

        rows.append(
            dict(
                file=f,
                node=f.split("/")[-3],
                trace_id=d["trace_id"],
                family=meta.get("task_family"),
                topology=meta.get("topology"),
                mode=meta.get("propagation_mode"),
                fixture=meta.get("fixture_id"),
                variant=meta.get("variant"),
                variant_label=meta.get("variant"),
                disregard_type=im.get("disregard_type"),
                instruction=instr,
                target_agent=tgt,
                n_injection_events=len(hoff_events),
                hoff_index=hoff,
                exposed=bool(exposing),
                n_exposing_events=len(exposing),
                exposing_roles=sorted({e.get("agent_role") for e in exposing}),
                n_distinct_exposing_agents=len(
                    {e.get("agent_id") for e in exposing}
                ),
                target_mismatch=bool(exposing and tgt not in {e.get("agent_role") for e in exposing}),
                delivered_block_len=len(block),
                block_excerpt=block[:500],
                block_has_warning=bool(WARN.search(block)),
                block_warning_hits=sorted(set(m.group(0).lower() for m in WARN.finditer(block))),
                block_has_temporal_order=bool(TEMPORAL_ORDER.search(block)),
                block_temporal_hits=sorted(set(m.group(0).lower() for m in TEMPORAL_ORDER.finditer(block))),
                block_has_year=bool(YEAR.search(block)),
                block_has_finding=bool(FINDING.search(block)),
                block_finding_hits=sorted(set(m.group(0).lower() for m in FINDING.finditer(block))),
                recv_tool_calls=dict(tools),
                recv_read_files=tools.get("read_text_file", 0),
                recv_search_files=tools.get("search_files", 0),
                recv_list_dir=tools.get("list_directory", 0),
                recv_n_reasoning=len(tgt_reasons),
                final_response_len=len(final_text),
                final_response=final_text[:1200],
                tgt_final_output=tgt_out[:1200],
                task_success=labels.get("task_success"),
                downstream_failure=labels.get("downstream_failure"),
                factual_score=labels.get("factual_score"),
                completeness_score=labels.get("completeness_score"),
                policy_score=labels.get("policy_score"),
                action_safety_score=labels.get("action_safety_score"),
                prov_score=labels.get("provenance_score"),
                evaluator_confidence=labels.get("evaluator_confidence"),
                evaluator_notes=labels.get("evaluator_notes"),
                lep_exposed=labels.get("lep_exposed"),
                lep_consumed=labels.get("lep_consumed"),
                lep_propagated=labels.get("lep_propagated"),
                n_events=len(evs),
            )
        )

    with open(OUT, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    print("wrote", len(rows), "records to", OUT)


if __name__ == "__main__":
    main()
