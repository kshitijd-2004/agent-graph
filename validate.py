"""
Data-quality guards for AgentGraphs traces.

    python validate.py --traces ../agent-graph/traces

Label problems are SILENT: a converter will happily build a perfectly-shaped
graph from data whose labels carry no signal.

These exist because label problems are SILENT: a converter will happily build a
perfectly-shaped graph from data whose labels carry no signal, and you only find
out weeks later when every model scores the same. Each check below corresponds
to a failure mode actually observed in the current trace dump.
"""

from collections import Counter, defaultdict

F_TRACE, F_EVENT, F_EXEC = "trace_id", "event_id", "execution_id"


class Issue:
    def __init__(self, level, code, msg):
        self.level, self.code, self.msg = level, code, msg

    def __repr__(self):
        return f"[{self.level}] {self.code}: {self.msg}"


def _last_event_id(events):
    return max(e[F_EVENT] for e in events)


def check_run(trace_id, events):
    """Validate a single run. Returns a list of Issue."""
    issues = []

    # 1) duplicate event_ids within one trace_id -> two files merged into one run
    counts = Counter(e[F_EVENT] for e in events)
    dupes = [eid for eid, c in counts.items() if c > 1]
    if dupes:
        issues.append(Issue(
            "ERROR", "DUPLICATE_EVENT_IDS",
            f"run '{trace_id}': {len(dupes)} event_ids appear more than once "
            f"-- two files likely share this trace_id and were merged."))

    # 2) degenerate runs (aborted generation) -- a 1-2 event 'run' is not a graph
    if len(events) < 5:
        issues.append(Issue(
            "WARN", "DEGENERATE_RUN",
            f"run '{trace_id}': only {len(events)} events -- likely an aborted "
            f"generation, not a usable graph. Recommend filtering."))

    # 3) THE BIG ONE: is downstream_failure just 'ran to the end'?
    #    A failure that always lands on the final event, with failure_type
    #    non_termination, is an artifact of hitting max trace length -- not a
    #    consequence of any injected perturbation.
    fails = [e for e in events if e.get("downstream_failure")]
    if fails:
        last = _last_event_id(events)
        terminal = [e for e in fails if e[F_EVENT] == last]
        nonterm = [e for e in fails if e.get("failure_type") == "non_termination"]
        if terminal and nonterm:
            issues.append(Issue(
                "ERROR", "ARTIFACT_FAILURE_LABEL",
                f"run '{trace_id}': downstream_failure is on the LAST event "
                f"(id={last}) with failure_type='non_termination'. This is a "
                f"trace-length artifact, not a propagated failure."))

    # 4) causal ground truth missing -> propagation cannot be validated
    has_cause = any(e.get("caused_by_event") is not None for e in events)
    has_prop = any(e.get("propagates_to") for e in events)
    injected = [e for e in events if e.get("lep_injected")]
    if injected and not (has_cause or has_prop):
        issues.append(Issue(
            "ERROR", "NO_CAUSAL_LINK",
            f"run '{trace_id}': {len(injected)} injected events but "
            f"caused_by_event/propagates_to are entirely empty. There is no "
            f"ground-truth link from injection to failure, so propagation "
            f"cannot be measured or validated."))

    # 5) injection density -- a 'local' perturbation should be local
    if injected:
        rate = len(injected) / len(events)
        if rate > 0.15:
            issues.append(Issue(
                "WARN", "HIGH_INJECTION_DENSITY",
                f"run '{trace_id}': {len(injected)}/{len(events)} events "
                f"({rate:.0%}) are marked lep_injected. A *Local* Execution "
                f"Perturbation should be sparse; at this density the "
                f"'early signal -> downstream failure' gap barely exists."))

    return issues


def check_dataset(runs):
    """Cross-run checks. `runs` is {trace_id: [events]}."""
    issues = []

    # 6) LEP taxonomy consistency: same code must always mean the same thing
    code_to_names = defaultdict(set)
    code_to_cats = defaultdict(set)
    for events in runs.values():
        for e in events:
            lt = e.get("lep_type")
            if not lt:
                continue
            code = lt.split()[0]
            code_to_names[code].add(lt)
            if e.get("lep_category"):
                code_to_cats[code].add(e["lep_category"])

    for code, names in sorted(code_to_names.items()):
        if len(names) > 1:
            issues.append(Issue(
                "ERROR", "TAXONOMY_CONFLICT",
                f"LEP code '{code}' maps to multiple names across the dataset: "
                f"{sorted(names)}. The same code must mean one thing."))
    for code, cats in sorted(code_to_cats.items()):
        expected = "FC" + code.split(".")[0]
        bad = {c for c in cats if c != expected}
        if bad:
            issues.append(Issue(
                "ERROR", "CATEGORY_MISMATCH",
                f"LEP code '{code}' is tagged category {sorted(bad)} but its "
                f"number implies {expected}."))

    # 7) class balance: are benign and malignant actually distinguishable?
    labelled = {"failed": 0, "clean": 0}
    for events in runs.values():
        if any(e.get("downstream_failure") for e in events):
            labelled["failed"] += 1
        else:
            labelled["clean"] += 1

    # 8) do runs with NO injection still get a failure label?
    contaminated = []
    for tid, events in runs.items():
        inj = any(e.get("lep_injected") for e in events)
        fail = any(e.get("downstream_failure") for e in events)
        if fail and not inj:
            contaminated.append(tid)
    if contaminated:
        issues.append(Issue(
            "ERROR", "FAILURE_WITHOUT_INJECTION",
            f"{len(contaminated)} run(s) have downstream_failure=True but NO "
            f"injected LEP (e.g. {contaminated[:3]}). If clean runs 'fail' too, "
            f"the failure label does not separate the classes and Stage-1 "
            f"detectability is untestable on it."))

    return issues, labelled


def report(runs, verbose=True):
    """Run all checks; print a report; return (issues, ok)."""
    all_issues = []
    for tid, events in runs.items():
        all_issues += check_run(tid, events)
    ds_issues, balance = check_dataset(runs)
    all_issues += ds_issues

    errors = [i for i in all_issues if i.level == "ERROR"]
    warns = [i for i in all_issues if i.level == "WARN"]

    if verbose:
        print("\n" + "=" * 68)
        print("DATA QUALITY REPORT")
        print("=" * 68)
        print(f"runs: {len(runs)}   "
              f"runs w/ failure label: {balance['failed']}   "
              f"without: {balance['clean']}")
        # dedupe repetitive per-run messages by code
        by_code = defaultdict(list)
        for i in all_issues:
            by_code[(i.level, i.code)].append(i)
        print()
        for (level, code), items in sorted(by_code.items()):
            print(f"[{level}] {code}  x{len(items)}")
            print(f"    {items[0].msg}")
            if len(items) > 1:
                print(f"    ... and {len(items)-1} more run(s)")
            print()
        print(f"summary: {len(errors)} error-type, {len(warns)} warning-type")
        print("=" * 68 + "\n")

    return all_issues, len(errors) == 0


# ---------------------------------------------------------------- CLI
if __name__ == "__main__":
    import argparse, glob, json, os
    ap = argparse.ArgumentParser()
    ap.add_argument("--traces", required=True)
    a = ap.parse_args()

    paths = [p for p in sorted(glob.glob(os.path.join(a.traces, "*.jsonl")))
             if "ipynb_checkpoints" not in p]
    shadowed = {p.replace("_labeled.jsonl", ".jsonl")
                for p in paths if p.endswith("_labeled.jsonl")}
    paths = [p for p in paths if p not in shadowed]

    runs = defaultdict(list)
    for p in paths:
        for line in open(p):
            if line.strip():
                ev = json.loads(line)
                runs[ev[F_TRACE]].append(ev)
    for t in runs:
        runs[t].sort(key=lambda e: e[F_EVENT])

    print(f"loaded {len(paths)} file(s) -> {len(runs)} run(s)")
    report(dict(runs))
