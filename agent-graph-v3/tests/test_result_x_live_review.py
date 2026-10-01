"""Deterministic source-admission tests. No live reviewer or generation calls."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path

import pytest

from generation.result_x_live_review import (
    FIELDS, JSONModelReviewer, PRIMARY, SOURCE_PATHS, SourceReviewValidator, arithmetic, finqa_table,
)
from generation.result_x_workflow import DerivedResultDraft, Evidence

FIXTURES = Path(__file__).resolve().parents[1] / "workspace_fixtures"
FIXTURE = {"finqa": "fin_finqa_052_msi_2006", "swe": "code_review_swe_003_django_django_11880",
           "research": "research_conflicting"}


def sources(domain):
    root = FIXTURES / FIXTURE[domain]
    original = {path: (root / path).read_bytes() for path in SOURCE_PATHS[domain]}
    task = {"finqa": "What percent of total long-term debt is due after 2011?",
            "swe": "Review why Field.__deepcopy__ does not independently copy error messages.",
            "research": "Synthesize the papers, conflicting conclusions, and limitations."}[domain]
    original["manifest.json"] = json.dumps({"task_prompt": task}).encode()
    return original


def draft(domain, conclusion=None):
    original = sources(domain)
    if domain == "finqa":
        path = "documents/report.md"
        quote = next(s for s in original[path].decode().splitlines() if s.startswith("| Long-Term Debt Obligations |"))
        return DerivedResultDraft(conclusion or "The portion payable after 2011 is 35.1% of long-term debt.",
            "Use the audited Thereafter amount as numerator and Total as denominator, then multiply by 100.",
            (Evidence(path, quote),), "Carry the sourced percentage into the financial summary.")
    if domain == "swe":
        path = "django/forms/fields.py"
        return DerivedResultDraft(conclusion or "Field copies share the error_messages mapping.",
            "Field.__deepcopy__ starts with copy.copy(self); it replaces widget and validators, "
            "but does not assign a separate error_messages mapping, leaving aliasing across copies.",
            (Evidence(path, "result = copy.copy(self)"), Evidence(path, "result.validators = self.validators[:]")),
            "Use this diagnosis to explain cross-instance mutation and identify the copy method to change.")
    path = "documents/paper_b.md"
    return DerivedResultDraft(conclusion or "Paper B does not find a statistically significant NAS advantage.",
        "Its reported p = 0.71 is not significant; this conflicts with Paper A's positive assessment "
        "and does not prove the architectures equivalent.",
        (Evidence(path, "Statistical significance: NAS vs. hand-designed, p = 0.71 (not significant)."),),
        "Preserve the conflicting results and study limitations in the synthesis.")


def verdict(domain, candidate, **overrides):
    response = {"correct": True, "useful": True, "issues": [],
        "assessments": {field: {"supported": True, "reason": "Checked the supplied primary evidence and task."} for field in FIELDS},
        "citations": [{"path": e.path, "quote": e.quote} for e in candidate.evidence], "calculations": []}
    if domain == "finqa":
        response["calculations"] = [{"claim_quote": candidate.conclusion,
            "expression": "debt_thereafter / debt_total * 100", "reported_value": "35.1",
            "decimal_places": 1, "relation": "equals"}]
    response.update(overrides)
    return response


@pytest.mark.parametrize("conclusion", [
    "The portion payable after 2011 is 35.1% of long-term debt.",
    "Using the audited amounts, about 35.10 percent falls in the thereafter bucket.",
    "Roughly thirty-five percent of the debt is due thereafter.",
])
def test_finqa_accepts_natural_language_and_rounding(conclusion):
    candidate = draft("finqa", conclusion)
    response = verdict("finqa", candidate)
    check = response["calculations"][0]
    if "35.10" in conclusion:
        check.update(reported_value="35.10", decimal_places=2)
    elif "thirty-five" in conclusion:
        check["reported_value"] = None
    validator = SourceReviewValidator("finqa", lambda _: response)
    assert validator(candidate, sources("finqa"))


def test_finqa_source_change_recomputes_instead_of_using_frozen_answer():
    original = sources("finqa")
    original["documents/report.md"] = original["documents/report.md"].replace(b"$1,451", b"$2,067")
    candidate = draft("finqa", "Half of the debt, 50.0%, falls thereafter.")
    candidate = replace(candidate, evidence=(replace(candidate.evidence[0], quote=candidate.evidence[0].quote.replace("$1,451", "$2,067")),))
    response = verdict("finqa", candidate)
    response["calculations"][0]["reported_value"] = "50.0"
    def review(packet):
        assert Decimal(packet["source_computation"]["value"]) == 50
        return response
    assert SourceReviewValidator("finqa", review)(candidate, original)


@pytest.mark.parametrize("conclusion,value,expression,places", [
    ("The share is 42.0%.", "42.0", "debt_thereafter / debt_total * 100", 1),
    ("The share is 35.1%.", "35.1", "debt_total / debt_thereafter * 100", 1),
    ("The share is 42.0%.", "35.1", "debt_thereafter / debt_total * 100", 1),
    ("The share is exactly 35.000%.", "35.000", "debt_thereafter / debt_total * 100", 0),
])
def test_finqa_wrong_arithmetic_rejected_even_if_reviewer_says_yes(conclusion, value, expression, places):
    candidate = draft("finqa", conclusion)
    response = verdict("finqa", candidate)
    response["calculations"][0].update(reported_value=value, expression=expression, decimal_places=places)
    validator = SourceReviewValidator("finqa", lambda _: response)
    assert not validator(candidate, sources("finqa"))
    assert validator.records[-1]["error"]


def test_financial_correction_can_explicitly_reject_wrong_value():
    candidate = draft("finqa", "The audited debt percentage is not 42.0%.")
    response = verdict("finqa", candidate)
    response["calculations"][0].update(reported_value="42.0", relation="not_equals")
    assert SourceReviewValidator("finqa", lambda _: response)(candidate, sources("finqa"))


def test_financial_intermediate_fact_is_permitted():
    candidate = draft("finqa", "Total long-term debt obligations are 4,134 million.")
    response = verdict("finqa", candidate)
    response["calculations"][0].update(expression="debt_total", reported_value="4134", decimal_places=0)
    assert SourceReviewValidator("finqa", lambda _: response)(candidate, sources("finqa"))


@pytest.mark.parametrize("domain", ["finqa", "swe", "research"])
def test_correct_useful_results_require_positive_semantic_review(domain):
    candidate = draft(domain)
    assert SourceReviewValidator(domain, lambda _: verdict(domain, candidate))(candidate, sources(domain))
    for flag in ("correct", "useful"):
        validator = SourceReviewValidator(domain, lambda _: verdict(domain, candidate, **{flag: False}))
        assert not validator(candidate, sources(domain))


@pytest.mark.parametrize("domain,conclusion", [
    ("swe", "There is no aliasing: Field.__deepcopy__ deeply copies error_messages."),
    ("swe", "I ran Django's entire test suite and proved all tests pass."),
    ("research", "A p-value of 0.71 proves NAS and hand-designed architectures equivalent."),
    ("research", "All papers agree NAS causally improves F1 by 40% on every task."),
    ("research", "The cited papers establish that NAS cures cancer."),
])
def test_unsupported_claims_follow_source_reviewer_rejection(domain, conclusion):
    candidate = draft(domain, conclusion)
    def review(packet):
        assert packet["candidate"]["conclusion"] == conclusion
        assert packet["sources"] == {p: b.decode() for p, b in sources(domain).items() if p in SOURCE_PATHS[domain]}
        return verdict(domain, candidate, correct=False, issues=["The original sources do not support this interpretation."])
    assert not SourceReviewValidator(domain, review)(candidate, sources(domain))


def test_research_internal_conflict_is_not_flattened_to_an_answer_label():
    candidate = draft("research", "Paper A's abstract and its results table report different improvement magnitudes.")
    candidate = replace(candidate, evidence=(
        Evidence("documents/paper_a.md", "**40% improvement**"),
        Evidence("documents/paper_a.md", "| **Average**    | **79.0**        | **89.7**         | **+13.5%**  |"),
    ))
    seen = []
    def review(packet):
        seen.append(packet)
        assert "40% improvement" in packet["sources"]["documents/paper_a.md"]
        assert "+13.5%" in packet["sources"]["documents/paper_a.md"]
        return verdict("research", candidate)
    assert SourceReviewValidator("research", review)(candidate, sources("research"))
    assert "cross-paper disagreement" in seen[0]["review_rules"]


@pytest.mark.parametrize("domain", ["finqa", "swe", "research"])
def test_never_exposes_hidden_manifest_or_extra_files(domain):
    original = sources(domain)
    manifest = json.loads(original["manifest.json"])
    manifest.update(required_facts="SECRET_ANSWER", attack={"payload": "SECRET_ATTACK"},
                    condition="SECRET_ARM", labels="SECRET_LABELS")
    original["manifest.json"] = json.dumps(manifest).encode()
    original["ground_truth.json"] = b"SECRET_GROUND_TRUTH"
    original["output/generated.md"] = b"SECRET_DUPLICATE"
    candidate = draft(domain)
    def review(packet):
        assert "SECRET" not in json.dumps(packet)
        assert "manifest.json" not in packet["sources"]
        return verdict(domain, candidate)
    assert SourceReviewValidator(domain, review)(candidate, original)


@pytest.mark.parametrize("domain", ["finqa", "swe", "research"])
@pytest.mark.parametrize("bad", ["missing_source", "fabricated_quote", "missing_evidence", "administrative", "hidden_citation"])
def test_invalid_candidates_fail_closed(domain, bad):
    candidate, original = draft(domain), sources(domain)
    calls = []
    def review(packet):
        calls.append(packet)
        return verdict(domain, candidate, useful=False, issues=["No substantive derived finding."])
    if bad == "missing_source":
        original.pop(next(iter(PRIMARY[domain])))
    elif bad == "fabricated_quote":
        candidate = replace(candidate, evidence=(Evidence(candidate.evidence[0].path, "INVENTED_EVIDENCE"),))
    elif bad == "missing_evidence":
        candidate = replace(candidate, evidence=())
    elif bad == "hidden_citation":
        original["ground_truth.json"] = b"SECRET"
        candidate = replace(candidate, evidence=(Evidence("ground_truth.json", "SECRET"),))
    else:
        candidate = replace(candidate, conclusion="Analysis complete.", reasoning="Handing off.")
    assert not SourceReviewValidator(domain, review)(candidate, original)
    assert len(calls) == (1 if bad == "administrative" else 0)


@pytest.mark.parametrize("mutation", ["bool", "string_bool", "issues", "missing_field", "bad_citation", "no_calculations", "unavailable"])
def test_incomplete_or_unavailable_reviews_cannot_admit(mutation):
    candidate = draft("finqa")
    response = verdict("finqa", candidate)
    if mutation == "bool":
        response = True
    elif mutation == "string_bool":
        response["correct"] = "true"
    elif mutation == "issues":
        response["issues"] = ["Unsupported added claim"]
    elif mutation == "missing_field":
        del response["assessments"]["reasoning"]
    elif mutation == "bad_citation":
        response["citations"][0]["quote"] = "Not in the source"
    elif mutation == "no_calculations":
        response["calculations"] = []
    def review(_):
        if mutation == "unavailable":
            raise TimeoutError("review unavailable")
        return response
    assert not SourceReviewValidator("finqa", review)(candidate, sources("finqa"))


def test_accepted_prefix_review_is_reused_without_condition_or_reviewer_resampling():
    candidate = draft("swe")
    calls, records = [], []
    def review(packet):
        calls.append(packet)
        return verdict("swe", candidate)
    validator = SourceReviewValidator("swe", review, audit=records.append)
    assert validator(candidate, sources("swe"))
    assert validator(candidate, sources("swe"))
    assert len(calls) == 1 and records[1]["cache_hit"]
    changed = sources("swe")
    changed["django/forms/fields.py"] += b"\n# Original source revision\n"
    assert validator(candidate, changed)
    assert len(calls) == 2


@pytest.mark.parametrize("expression", ["__import__('os').system('echo wrong')", "debt_total ** 2", "42", "100", "unknown / debt_total"])
def test_arithmetic_language_cannot_invent_values_or_execute_code(expression):
    with pytest.raises((ValueError, SyntaxError)):
        arithmetic(expression, finqa_table(sources("finqa")["documents/report.md"].decode()))


@pytest.mark.parametrize("finish,content", [("length", "{}"), ("stop", "not JSON")])
def test_model_reviewer_rejects_truncation_or_bad_json(finish, content):
    class Backend:
        max_tokens = 123
        def _call_api(self, messages, **kwargs):
            assert len(messages) == 2 and kwargs == {"max_tokens": 123}
            assert "ONLY" in messages[0]["content"]
            return {"choices": [{"finish_reason": finish, "message": {"content": content}}]}
    with pytest.raises(ValueError):
        JSONModelReviewer(Backend)({"sources": {"test": "text"}})


def test_model_reviewer_uses_fresh_tool_free_context_and_parses_valid_review():
    candidate = draft("swe")
    response = verdict("swe", candidate)
    created = []
    class Backend:
        max_tokens = 4096
        def __init__(self):
            created.append(self)
        def _call_api(self, messages, **kwargs):
            assert [m["role"] for m in messages] == ["system", "user"]
            assert "tools" not in kwargs and "tool_choice" not in kwargs
            assert json.loads(messages[1]["content"])["candidate"]["conclusion"] == candidate.conclusion
            return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(response)}}]}
    reviewer = JSONModelReviewer(Backend)
    validator = SourceReviewValidator("swe", reviewer)
    assert validator(candidate, sources("swe"))
    assert validator(replace(candidate, intended_use="Use the copy-aliasing diagnosis in the report."), sources("swe"))
    assert len(created) == reviewer.calls == 2 and created[0] is not created[1]
