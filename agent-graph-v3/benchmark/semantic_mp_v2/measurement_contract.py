"""Auditable semantic assessment primitives; no model calls or production hooks."""
from collections import Counter
from dataclasses import asdict, dataclass, field
import hashlib
import json

from benchmark.behavioral_anomaly import MIN_CLEAN_RUNS_REQUIRED, STABLE_SUPPORT_THRESHOLD
from benchmark.semantic_mp_v2.extraction import aligned_key, observable_aliases

VERSION = "semantic-measurement-contract-v1"
ALIGNMENT_VERSION = "explicit-identity-or-unresolved-v1"


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True)
class EntityRegistry:
    """Only source-attested aliases; never contains expected claim values/verdicts.

    Freeze this artifact when building the fixture. Runtime callers must supply
    the same task source; an altered source cannot reuse the registry silently.
    """
    source_sha256: str
    aliases: dict
    version: str = ALIGNMENT_VERSION

    @classmethod
    def build(cls, task_source):
        return cls(hashlib.sha256(task_source.encode()).hexdigest(), observable_aliases(task_source))

    def verify(self, task_source):
        if self != self.build(task_source):
            raise ValueError("entity registry does not match the authoritative task source")

    def key(self, fact):
        return aligned_key(fact, self.aliases)


def identity_relation(a, b, registry):
    """Resolve identity, not value/stance; disjoint wording is never nonidentity proof.

    Explicit incompatible units or scopes establish different comparison slots.
    Other unequal keys remain unresolved until an alignment capability is validated.
    No retrieval cutoff can establish absence.
    """
    left, right = registry.key(a), registry.key(b)
    if left == right:
        return "same"
    if a.kind != b.kind:
        return "different"
    if a.unit and b.unit and a.unit != b.unit:
        return "different"
    # Time, level, residual scope, and maturity dates are identity constraints.
    if any(left[i] and right[i] and left[i] != right[i] for i in (4, 5, 6, 7, 8, 9)):
        return "different"
    return "unresolved"


def extraction_status(result):
    # A contextual model's complete=true is not a completeness certificate.
    if result.complete and result.method.startswith("deterministic"):
        return "complete"
    return "partial" if result.propositions else "failed"


@dataclass
class RunAssessment:
    run_id: str
    extraction: str
    alignment: str
    relation: str
    evidence_sources: list[str] = field(default_factory=list)
    depends_on_claims: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def compare_run(run_id, entries, target, registry):
    """Compare a claim to one complete, matched output scope in one clean run.

    entries are (source_id, extraction, claim_ids). Missing scope, failed
    extraction, or unaligned assertions cannot certify assessed absence.
    Rejection is not assertion of the opposite proposition.
    """
    sources = [source for source, _, _ in entries]
    if not entries:
        return RunAssessment(run_id, "failed", "unassessed", "unresolved", reasons=["missing_output_scope"])
    states = [extraction_status(x) for _, x, _ in entries]
    status = "complete" if all(s == "complete" for s in states) else (
        "failed" if all(s == "failed" for s in states) else "partial")
    same, unresolved = [], []
    for source, result, ids in entries:
        for claim, claim_id in zip(result.propositions, ids):
            relation = identity_relation(claim, target, registry)
            if relation == "same":
                same.append((claim, claim_id))
            elif relation == "unresolved":
                unresolved.append(claim_id)
    assertions = {p.value for p, _ in same if p.stance == "asserted"}
    rejected = {p.value for p, _ in same if p.stance == "rejected"}
    reasons = []
    if status != "complete":
        reasons.append("extraction_coverage_unresolved")
    if unresolved:
        reasons.append("claim_identity_unresolved")
    alignment = "ambiguous" if unresolved else "resolved" if status == "complete" else "unassessed"
    if reasons:
        relation = "unresolved"
    elif len(assertions) > 1 or assertions & rejected:
        relation = "mixed"
    elif assertions:
        relation = "supports" if assertions == {target.value} else "contradicts"
    elif same:
        relation = "unresolved"
        reasons.append("reported_or_rejected_without_asserted_verdict")
    else:
        relation = "absent"
    return RunAssessment(run_id, status, alignment, relation, sources,
                         [i for _, i in same] + unresolved, reasons)


def summarize_support(assessments):
    ids = [a.run_id for a in assessments]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate clean run identity")
    counts = Counter(a.relation for a in assessments)
    n = len(assessments)
    supported, unresolved = counts["supports"], counts["unresolved"]
    assessed = n - unresolved
    lower = supported / n if n else None
    upper = (supported + unresolved) / n if n else None
    eligible = n >= MIN_CLEAN_RUNS_REQUIRED and lower >= STABLE_SUPPORT_THRESHOLD
    tier = ("strong_invariant" if n and supported == n else
            "stable_expectation" if n and lower >= STABLE_SUPPORT_THRESHOLD else
            "insufficient_evidence" if not n or unresolved and upper >= STABLE_SUPPORT_THRESHOLD else
            "variable")
    return {
        "total_runs": n, "assessed_runs": assessed,
        "counts": {k: counts[k] for k in ("supports", "contradicts", "absent", "mixed", "unresolved")},
        "observed_support": lower, "assessment_coverage": assessed / n if n else None,
        "conditional_agreement": supported / assessed if assessed else None,
        "support_interval": [lower, upper], "tier": tier,
        "eligible": eligible, "threshold": STABLE_SUPPORT_THRESHOLD,
        "minimum_clean_runs": MIN_CLEAN_RUNS_REQUIRED,
        "runs": [asdict(a) for a in assessments],
    }


class EvidenceGraph:
    """Both supporting and mandatory context roots must resolve successfully."""
    def __init__(self):
        self.nodes = {}

    def add(self, node_id, *, kind, state="supported", depends_on=(), **metadata):
        if state not in {"supported", "unknown", "refuted"}:
            raise ValueError("invalid evidence state")
        node = dict(kind=kind, state=state, depends_on=list(depends_on), **metadata)
        if node_id in self.nodes and self.nodes[node_id] != node:
            raise ValueError("conflicting evidence identity: " + node_id)
        self.nodes[node_id] = node
        return node_id

    def closure(self, roots):
        found, active, blocked = set(), set(), {}

        def visit(node_id):
            if node_id in active:
                blocked[node_id] = "cyclic_evidence"
                return
            if node_id in found:
                return
            found.add(node_id)
            node = self.nodes.get(node_id)
            if node is None:
                blocked[node_id] = "missing_evidence"
                return
            if node["state"] != "supported":
                blocked[node_id] = node["state"]
            active.add(node_id)
            for dep in node["depends_on"]:
                visit(dep)
            active.remove(node_id)

        for root in roots:
            visit(root)
        return {"node_ids": sorted(found), "blockers": blocked, "satisfied": not blocked}

    def assess(self, supporting_dependencies, required_context_checks):
        supporting = self.closure(supporting_dependencies)
        context = self.closure(required_context_checks)
        return {
            "supporting_dependencies": list(supporting_dependencies),
            "required_context_checks": list(required_context_checks),
            "supporting_closure": supporting, "context_closure": context,
            "satisfied": supporting["satisfied"] and context["satisfied"],
        }
