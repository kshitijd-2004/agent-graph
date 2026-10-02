"""Read-only historical audit; writes only the explicitly requested report.

Example: python -m scripts.audit_delivery_tracking RECORDS.jsonl --output REPORT.json
No models, fixture changes, trace rewriting, or dataset re-admission.
"""
import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path

from memory.memory_store import MemoryRecord, MemoryStore


class HistoricalMemoryStore(MemoryStore):
    """Freeze the historical IDF for reproducible execution replay."""
    def _compute_idf(self):
        n = len(self._documents)
        counts = Counter(t for doc in self._documents for t in set(self._tokenize(doc)))
        self._idf = {t: math.log(n / (1 + count)) for t, count in counts.items()}


def audit(records_path):
    rows = [json.loads(line) for line in Path(records_path).read_text().splitlines() if line.strip()]
    financial = defaultdict(Counter)
    financial_topology = defaultdict(Counter)
    backend_failures = Counter()
    memory = defaultdict(Counter)
    replay = Counter()
    label_counts = defaultdict(Counter)
    examples = {}
    missing = []
    for row in rows:
        code = row.get("lep_code", "")
        if not code:
            continue
        family, topology = row["task_family"], row["topology"]
        if family == "financial_analysis":
            financial[code]["attempted"] += 1
            financial[code]["fired"] += bool(row.get("injection_fired"))
            financial[code][row.get("termination_reason", "unknown")] += 1
            if code == "LEP_INDIRECT_PROMPT_INJECTION":
                financial_topology[topology][row.get("termination_reason", "unknown")] += 1
        path = Path(row["trace_path"])
        if not path.exists():
            missing.append(str(path))
            continue
        trace = json.loads(path.read_text())
        events = trace["events"]
        origins = {e["event_id"] for e in events if e.get("event_labels", {}).get("is_injection_origin")}
        if code == "LEP_INDIRECT_PROMPT_INJECTION" and family == "financial_analysis":
            if row.get("termination_reason") == "protocol_violation":
                raw = events[-1].get("observable", {}).get("raw_model_text", "")
                backend_failures[raw] += 1
                examples.setdefault("financial_pi_protocol", {"path": str(path), "event_id": events[-1]["event_id"]})
        if origins:
            counts = label_counts[code]
            counts["fired_traces"] += 1
            for label in ("consumes_perturbed_info", "forwards_perturbed_info", "stores_perturbed_info", "inferred_propagation"):
                counts[label] += any(e.get("event_labels", {}).get(label) for e in events)
                counts["final_" + label] += any(e.get("event_labels", {}).get(label) for e in events if e["event_type"] == "final_response")
        if code != "LEP_MEMORY_POISONING" or not origins:
            continue
        store = HistoricalMemoryStore()
        corrected = MemoryStore()
        reads = []
        poisoned_reads = []
        first_origin = min(e["event_index"] for e in events if e["event_id"] in origins)
        for event in events:
            args = event.get("tool_arguments") or {}
            if event["event_type"] == "memory_write" and args.get("key") and "value" in args:
                record = MemoryRecord(id=f"{event['agent_id']}_{args['key']}", key=args["key"],
                                      value=args["value"], metadata={"write_event_id": event["event_id"]})
                store.add(record)
                corrected.add(record)
            elif event["event_type"] == "memory_retrieval" and event["event_index"] > first_origin:
                reads.append(event)
                query, top_k = args.get("query", ""), args.get("top_k", 3)
                results = store.retrieve(query, top_k)
                deps = {record.metadata["write_event_id"] for record, _ in results}
                replay["reads"] += 1
                if deps != set(event.get("depends_on", [])):
                    replay["replay_mismatch"] += 1
                    continue
                actual = bool(deps & origins)
                if actual:
                    poisoned_reads.append(event)
                if event.get("event_labels", {}).get("consumes_perturbed_info") and not actual:
                    replay["stale_key_false_positive"] += 1
                    examples.setdefault("stale_key", {"path": str(path), "event_id": event["event_id"], "write_dependencies": sorted(deps)})
                if not results:
                    replay["empty"] += 1
                    overlap = any(set(store._tokenize(query)) & set(store._tokenize(rec.key + " " + rec.value)) for rec in store.list_all())
                    replay["empty_with_lexical_overlap" if overlap else "empty_no_overlap"] += 1
                    if overlap:
                        replay["empty_recovered_by_smoothing"] += bool(corrected.retrieve(query, top_k))
                        examples.setdefault("zero_idf", {"path": str(path), "event_id": event["event_id"], "query": query})
        key = "|".join((family, topology, row["propagation_mode"]))
        memory[key]["written_runs"] += 1
        memory[key]["read_attempt_runs"] += bool(reads)
        memory[key]["version_verified_delivery_runs"] += bool(poisoned_reads)
        memory[key]["historically_labeled_delivery_runs"] += any(e.get("event_labels", {}).get("consumes_perturbed_info") for e in reads)
        memory[key]["empty_only_runs"] += bool(reads) and all("No memory records" in (e.get("output_text") or "") for e in reads)
        kind = "memory_delivery" if poisoned_reads else "memory_no_read" if not reads else "memory_no_poison_returned"
        examples.setdefault(kind, {"path": str(path)})
    return {"records_source": str(Path(records_path).resolve()), "record_count": len(rows),
            "missing_traces": missing, "financial": dict(financial),
            "financial_pi_by_topology": dict(financial_topology),
            "financial_pi_protocol_raw_text": dict(backend_failures),
            "historical_labels": dict(label_counts), "memory_by_cell": dict(memory),
            "memory_replay": dict(replay), "representative_traces": examples}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = audit(args.records)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"report": str(output), "records": result["record_count"],
                      "missing": len(result["missing_traces"]), "memory_replay": result["memory_replay"]}))


if __name__ == "__main__":
    main()
