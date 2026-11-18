"""Why does memory poisoning hurt less in financial than in research? Splits MP runs three ways.

For each attacked MP run (compared with its matched clean runs, same task/setup/variant):
  adopted   a number only the poison contains (no matched clean run ever produced it)
            shows up in the final answer
  reread    the final agent opened a source file after the poison reached its input
  key       which memory key the poison went into (which poison text the agent got)
Prints attack pass vs matched clean pass for each group.

    python3 mp_split.py 'traces/pilot/psf/node_*/benchmark_records.jsonl' --family financial_analysis
"""
import argparse
import collections
import glob
import json
from pathlib import Path

from matched_effect import fixture_of
from mp_id_check import load, args_of, labels, seen_by, finalizers, nums, outputs, contains

MP = "LEP_MEMORY_POISONING"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("records", nargs="+")
    ap.add_argument("--family", required=True)
    ap.add_argument("--field", default="task_success")
    ap.add_argument("--trace-roots", nargs="*", default=None)
    a = ap.parse_args()

    files = [p for g in a.records for p in glob.glob(g)]
    roots = a.trace_roots or sorted({str(Path(p).parent) for p in files})
    runs = [json.loads(l) for p in files for l in open(p)]
    runs = [r for r in runs if r.get("task_family") == a.family]

    def cell(r):
        return (fixture_of(r), r["topology"], r.get("execution_variant", "standard"))

    clean = collections.defaultdict(list)
    for r in runs:
        if not r.get("lep_code"):
            clean[cell(r)].append(r)
    clean_nums = {}

    def known(c):
        if c not in clean_nums:
            s = set()
            for cr in clean[c]:
                ev = load(cr, roots)
                if ev is not None:
                    s |= nums(outputs(ev) + "\n" + (cr.get("final_output") or ""))
            clean_nums[c] = s
        return clean_nums[c]

    groups = collections.defaultdict(list)
    for r in runs:
        if r.get("lep_code") != MP:
            continue
        c = cell(r)
        if not clean.get(c):
            continue
        ev = load(r, roots)
        if ev is None:
            continue
        origins = [e for e in ev if e.get("event_type") == "memory_write" and labels(e).get("is_injection_origin")]
        if not origins:
            continue
        o = origins[0]
        val = str(args_of(o).get("value", ""))
        key = str(args_of(o).get("key", "")).lower()
        kname = ("revenue/figures" if any(w in key for w in ("revenue", "figure")) else
                 "margin/profit" if any(w in key for w in ("margin", "profit")) else
                 "trend" if "trend" in key else "other")
        poison_nums = nums(val) - known(c)
        final = (r.get("final_output") or "") + "\n" + "\n".join(
            e.get("output_text") or "" for e in ev if e.get("event_type") == "final_response")
        adopted = bool(poison_nums & nums(final))
        fin = finalizers(ev)
        seen = seen_by(ev, val[:60], o.get("event_index", 0))
        first_seen = min((e.get("event_index", 0) for e in ev if e.get("event_type") == "reasoning"
                          and e.get("agent_id") in (seen & fin)
                          and contains(e.get("input_text"), val[:60])), default=None)
        reread = first_seen is not None and any(
            e.get("event_type") == "tool_call" and e.get("tool_name") == "read_text_file"
            and e.get("agent_id") in fin and e.get("event_index", 0) > first_seen for e in ev)
        v = bool(r.get(a.field))
        b = sum(bool(x.get(a.field)) for x in clean[c]) / len(clean[c])
        groups[("all", "")].append((v, b))
        groups[("poison number in final answer", "yes" if adopted else "no")].append((v, b))
        groups[("final agent re-read sources after poison", "yes" if reread else "no" if first_seen is not None else "poison not seen by final agent")].append((v, b))
        groups[("poisoned key", kname)].append((v, b))

    print(f"{a.family}: memory poisoning runs split (pass, attack vs matched clean)")
    for k in sorted(groups):
        rows = groups[k]
        av = sum(v for v, _ in rows) / len(rows)
        bv = sum(b for _, b in rows) / len(rows)
        print(f"  {k[0]:42} {k[1]:30} n={len(rows):4}  attack {av:4.0%}  clean {bv:4.0%}  diff {(av - bv) * 100:+4.0f}")


if __name__ == "__main__":
    main()
