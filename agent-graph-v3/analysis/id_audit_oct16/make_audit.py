"""Input Disregard audit: scan traces, cross-check the analyzer, emit report data."""
import collections
import glob
import json
import os
import re
import sys

ROOT = "/u/kdhande/agent-graph/agent-graph-v3"
OUTDIR = os.path.join(ROOT, "analysis", "id_audit_oct16")
NODE_GLOB = os.path.join(ROOT, "production_run", "node_*", "traces", "*INPUT_DISREGARD*.json")

WARN_RE = re.compile(
    r"\b(warning|warnings|warned|caution|cautionary|caveat|red flag|risk flag|"
    r"risk factor|risk assessment|flagged|potential issue|discrepancy|discrepant|"
    r"inconsisten\w*|uncertain|oversight|be careful)\b", re.I)
ORDER_RE = re.compile(
    r"\b(most recent|recent|recently|latest|newest|newer|older|oldest|earlier|"
    r"prior year|prior period|last year|previous year|chronolog\w*|time[- ]?stamped)\b", re.I)
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
FIND_RE = re.compile(
    r"\b(identified (a |the )?(issue|defect|bug|problem|finding)|potential issue|"
    r"the issue|bug|defect|findings?)\b", re.I)
MARK = "[Handoff received from"


def block_of(input_text):
    if not input_text:
        return ""
    msgs = input_text
    if isinstance(msgs, str):
        try:
            msgs = json.loads(msgs)
        except Exception:
            return input_text
    for m in msgs if isinstance(msgs, list) else []:
        c = m.get("content", "") if isinstance(m, dict) else str(m)
        i = c.find(MARK)
        if i >= 0:
            return c[i:]
    return ""


def load_records():
    rows = []
    for f in glob.glob(NODE_GLOB):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        evs = d["events"]
        he = [e for e in evs if e.get("hidden", {}).get("input_disregard")]
        if not he:
            continue
        h0 = he[0]
        im = h0["hidden"]["input_disregard"]
        instr = im.get("instruction", "")
        tgt = im.get("target_agent")
        hoff = h0["event_index"]
        later = [e for e in evs if e["event_index"] > hoff]
        exposing = [e for e in later if instr[:60] in (e.get("input_text") or "")]
        recv = [e for e in later if e.get("agent_role") == tgt]
        tools = collections.Counter(
            e.get("tool_name") for e in recv if e["event_type"] == "tool_call")
        r0 = next((e for e in recv if e["event_type"] == "reasoning"), None)
        block = block_of(r0.get("input_text")) if r0 else ""
        fin = [e for e in evs if e["event_type"] == "final_response"]
        rows.append(dict(
            file=f, trace_id=d["trace_id"],
            family=d["metadata"].get("task_family"),
            topology=d["metadata"].get("topology"),
            mode=d["metadata"].get("propagation_mode"),
            fixture=d["metadata"].get("fixture_id"),
            variant=d["metadata"].get("variant"),
            disregard_type=im.get("disregard_type"),
            instruction=instr, target_agent=tgt,
            n_injection_events=len(he), hoff_index=hoff,
            exposed=bool(exposing), n_exposing_events=len(exposing),
            exposing_roles=sorted({e.get("agent_role") for e in exposing}),
            n_exposing_agents=len({e.get("agent_id") for e in exposing}),
            target_mismatch=bool(exposing and tgt not in {e.get("agent_role") for e in exposing}),
            block_len=len(block),
            block_has_warning=bool(WARN_RE.search(block)),
            block_warn_hits=sorted({m.group(0).lower() for m in WARN_RE.finditer(block)}),
            block_has_order=bool(ORDER_RE.search(block)),
            block_order_hits=sorted({m.group(0).lower() for m in ORDER_RE.finditer(block)}),
            block_has_year=bool(YEAR_RE.search(block)),
            block_has_finding=bool(FIND_RE.search(block)),
            block_find_hits=sorted({m.group(0).lower() for m in FIND_RE.finditer(block)}),
            recv_tools=dict(tools),
            recv_reads=tools.get("read_text_file", 0),
            recv_searches=tools.get("search_files", 0),
            recv_listdir=tools.get("list_directory", 0),
            final_len=len(fin[-1].get("output_text") or "") if fin else 0,
            task_success=d["labels"].get("task_success"),
            downstream_failure=d["labels"].get("downstream_failure"),
            factual=d["labels"].get("factual_score"),
            complete=d["labels"].get("completeness_score"),
            policy=d["labels"].get("policy_score"),
            conf=d["labels"].get("evaluator_confidence"),
            notes=d["labels"].get("evaluator_notes"),
            lep_exposed=d["labels"].get("lep_exposed"),
            lep_consumed=d["labels"].get("lep_consumed"),
            lep_propagated=d["labels"].get("lep_propagated"),
            n_events=len(evs),
        ))
    return rows


def main():
    rows = load_records()
    with open(os.path.join(OUTDIR, "records.jsonl"), "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    print("records:", len(rows))


if __name__ == "__main__":
    main()
