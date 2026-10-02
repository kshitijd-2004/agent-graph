"""Claim binding from saved live-05 output; all reviewers here are CPU stubs."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest

from generation.result_x_live_review import FIELDS, SourceReviewValidator, candidate_claim_spans
from generation.result_x_workflow import DerivedResultDraft, Evidence
from test_result_x_live_review import draft, sources, verdict


def saved_case():
    saved = json.loads((Path(__file__).parent / "data/result_x_financial_merge_review.json").read_text())
    candidate = DerivedResultDraft(**{**saved["candidate"],
        "evidence": tuple(Evidence(**e) for e in saved["candidate"]["evidence"])})
    original = sources("finqa")
    original["manifest.json"] = json.dumps({"task_prompt": saved["task"]}).encode()
    response = deepcopy(saved["response"])
    for field in FIELDS:
        response["assessments"][field]["claim_ids"] = [
            s["claim_id"] for s in candidate_claim_spans(candidate) if s["field"] == field]
    return saved, candidate, original, response


def test_saved_financial_merge_equation_is_not_candidate_text_but_reference_passes():
    saved, candidate, original, response = saved_case()
    invented = response["calculations"][0]["claim_quote"]
    assert invented == "1,451 / 4,134 * 100 = 35.1%"
    assert all(invented not in getattr(candidate, f) for f in FIELDS)
    assert saved["error"] == "ValueError: Calculation must refer to the candidate's actual claim"
    validator = SourceReviewValidator("finqa", lambda _: response)
    assert not validator(candidate, original)  # Legacy free-form claim cannot bypass binding.
    del response["calculations"][0]["claim_quote"]
    response["calculations"][0]["claim_id"] = "conclusion:0"
    assert validator(candidate, original)
    spans = validator.records[-1]["request"]["claim_spans"]
    assert spans[0]["text"] == "The percent of the total long-term debt obligations that was due after 2011 is 35.1%."
    assert len([s for s in spans if s["field"] == "conclusion"]) == 6
    # Source-backed lookups on each listed monetary claim retain arithmetic checks.
    for index, expression, value in [(2, "debt_thereafter", "1451"), (3, "debt_total", "4134"),
                                      (4, "purchase_2011", "12"), (5, "contractual_2010", "724")]:
        response["calculations"].append({"claim_id": f"conclusion:{index}", "expression": expression,
            "reported_value": value, "decimal_places": 0, "relation": "equals"})
    assert SourceReviewValidator("finqa", lambda _: response)(candidate, original)


@pytest.mark.parametrize("position", ["assessment", "calculation"])
@pytest.mark.parametrize("reference", [None, True, 0, [], {}, "", " conclusion:0", "unknown:0", "conclusion:999"])
def test_unknown_or_malformed_claim_ids_fail_closed(position, reference):
    candidate = draft("finqa")
    response = verdict("finqa", candidate)
    if position == "assessment":
        response["assessments"]["conclusion"]["claim_ids"] = [reference]
    else:
        response["calculations"][0]["claim_id"] = reference
    validator = SourceReviewValidator("finqa", lambda _: response)
    assert not validator(candidate, sources("finqa"))
    assert "Unknown or invalid candidate claim_id" in validator.records[-1]["error"]


@pytest.mark.parametrize("ids", [None, "conclusion:0", [], ["conclusion:0", "conclusion:0"], ["reasoning:0"]])
def test_assessment_references_require_nonempty_unique_complete_correct_field_list(ids):
    candidate = draft("finqa")
    response = verdict("finqa", candidate)
    response["assessments"]["conclusion"]["claim_ids"] = ids
    assert not SourceReviewValidator("finqa", lambda _: response)(candidate, sources("finqa"))


def test_assessment_cannot_skip_added_candidate_claim():
    candidate = draft("finqa")
    response = verdict("finqa", candidate)
    candidate = replace(candidate, conclusion=candidate.conclusion + "\nAn additional unsupported claim.")
    assert not SourceReviewValidator("finqa", lambda _: response)(candidate, sources("finqa"))


def test_calculation_cannot_reference_intended_use():
    candidate = draft("finqa")
    response = verdict("finqa", candidate)
    response["calculations"][0]["claim_id"] = "intended_use:0"
    assert not SourceReviewValidator("finqa", lambda _: response)(candidate, sources("finqa"))


@pytest.mark.parametrize("section", ["assessment", "calculation"])
def test_free_form_claim_text_cannot_override_valid_id(section):
    candidate = draft("finqa", "The share is 45.1%.")
    response = verdict("finqa", candidate)
    target = response["assessments"]["conclusion"] if section == "assessment" else response["calculations"][0]
    target["claim_quote"] = "The share is 35.1%."
    assert not SourceReviewValidator("finqa", lambda _: response)(candidate, sources("finqa"))


def test_valid_id_does_not_make_incorrect_arithmetic_valid():
    _, candidate, original, response = saved_case()
    candidate = replace(candidate, conclusion=candidate.conclusion.replace("35.1%", "45.1%"))
    check = response["calculations"][0]
    del check["claim_quote"]
    check.update(claim_id="conclusion:0", reported_value="45.1")
    validator = SourceReviewValidator("finqa", lambda _: response)
    assert not validator(candidate, original)
    assert "Claimed arithmetic contradicts original source values" in validator.records[-1]["error"]


def test_callback_cannot_replace_authoritative_span_text():
    candidate = draft("finqa", "The share is 45.1%.")
    response = verdict("finqa", candidate)
    def reviewer(packet):
        packet["claim_spans"][0]["text"] = "The share is 35.1%."
        packet["candidate"]["conclusion"] = "The share is 35.1%."
        return response
    validator = SourceReviewValidator("finqa", reviewer)
    assert not validator(candidate, sources("finqa"))
    assert "substituted a number absent" in validator.records[-1]["error"]


def test_span_offsets_are_verbatim_and_ids_are_deterministic():
    candidate = replace(draft("research"), conclusion="  Δ finding.\r\n\r\nSecond finding.\n\t ")
    spans = candidate_claim_spans(candidate)
    assert spans == candidate_claim_spans(candidate)
    assert len({s["claim_id"] for s in spans}) == len(spans)
    for span in spans:
        assert span["text"] == getattr(candidate, span["field"])[span["start"]:span["end"]]
    assert spans[0]["text"] == "  Δ finding."
    assert spans[1]["text"] == "Second finding."
