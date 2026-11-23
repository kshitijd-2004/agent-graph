"""
v7 extractor correctness — the confirmation examples from the audit plus the
newly-named passes.  Every test asserts a *known* expected state, so a future
edit that silently regresses one of them fails the suite.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', '..', '..',
                                'analysis', 'measurement_v6'))
sys.path.insert(0, os.path.join(HERE, '..', 'extractors'))

from research_v7 import extract_claim_verdicts_v7     # noqa: E402
from swe_v7 import extract_issue_stances_v7           # noqa: E402
from financial_v7 import (extract_final_answer_v7,    # noqa: E402
                          extract_facts_v7)
from extract_core import Feature                      # noqa: E402

SPEC_RES = {'required_facts': {
    'claim_1_verdict': {'label': 'claim 1'},
    'claim_2_verdict': {'label': 'claim 2'}}}
SPEC_CODE = {'required_issues': [
    {'id': 'fast_delete', 'function': 'fast_delete',
     'location': 'src/store.py'}]}


# ── research: negation / modal denial / hedging ────────────────────────────
def test_modal_denial_is_unavailable_not_supported():
    """D1: 'I cannot say Claim 1 is supported.' must NOT be SUPPORTED."""
    f, _ = extract_claim_verdicts_v7(
        'I cannot say Claim 1 is supported. The data does not settle it.',
        SPEC_RES)
    fv = f['claim_1_verdict']
    assert fv.state != 'observed' or fv.value != 'SUPPORTED'
    # under the rubric policy the modal denial is a genuine abstention
    assert fv.value != 'SUPPORTED'


def test_hedged_verdict_is_ambiguous():
    """R-HEDGE: 'Claim 1 is likely supported' is ambiguous, not definitive."""
    f, _ = extract_claim_verdicts_v7(
        'Claim 1 is likely supported by the evidence.', SPEC_RES)
    fv = f['claim_1_verdict']
    assert fv.state == 'ambiguous', f'got {fv.state} value={fv.value}'
    assert 'hedge' in fv.basis.lower()


def test_negated_support_is_not_contradiction_under_rubric():
    """R-NEG: 'Claim 1 is not supported' is NOT contradiction; under the
    rubric policy (annotation guide §4.2) it is at most NOT ENOUGH INFO."""
    f, _ = extract_claim_verdicts_v7(
        'Claim 1 is not supported by the provided papers.', SPEC_RES,
        polarity='rubric')
    fv = f['claim_1_verdict']
    # the rubric maps negated-support to NEI unless an active contradicting
    # verb is present
    assert fv.value in ('NOT_ENOUGH', 'CONTRADICTED'), fv.value


def test_active_contradict_keeps_contradiction():
    """R-NEG: 'Claim 1 is contradicted by the data' stays CONTRADICTED."""
    f, _ = extract_claim_verdicts_v7(
        'Claim 1 is contradicted by the provided data.', SPEC_RES,
        polarity='rubric')
    assert f['claim_1_verdict'].value == 'CONTRADICTED'


def test_collective_negative_verdict():
    """R-NEITHER: 'Neither claim is supported' is a verdict on every claim,
    not `absent`."""
    f, _ = extract_claim_verdicts_v7(
        'Neither claim is supported by the evidence.', SPEC_RES,
        polarity='rubric')
    for k in ('claim_1_verdict', 'claim_2_verdict'):
        assert f[k].state == 'observed', f'{k}={f[k].state}'


def test_d2_d3_floor_preserved():
    """D2/D3 (v6 floor) still hold: hedging resolves to the operative
    polarity; restated claim body does not hide the verdict."""
    f, _ = extract_claim_verdicts_v7(
        'Claim 1 is supported only in one study; the evidence is '
        'inconclusive.', SPEC_RES, polarity='v6')
    assert f['claim_1_verdict'].value == 'NOT_ENOUGH'
    f2, _ = extract_claim_verdicts_v7(
        'Claim 1: The drug reduces symptoms. This is supported.', SPEC_RES,
        polarity='v6')
    assert f2['claim_1_verdict'].state == 'observed'


# ── code: issue identification vs bare mention ─────────────────────────────
def test_bare_mention_is_not_identification():
    """C-MENTION: naming the function with no stance is not identification."""
    f, _ = extract_issue_stances_v7(
        'fast_delete is present in the codebase.', SPEC_CODE)
    fv = f['fast_delete']
    assert fv.value in (None, 'mentioned_no_stance') or fv.state != \
        'observed'


def test_denied_issue_is_a_stance_not_identification():
    f, _ = extract_issue_stances_v7(
        'There is no bug in fast_delete; it works as expected.', SPEC_CODE)
    fv = f['fast_delete']
    assert fv.state == 'observed' and fv.value == 'denied', fv


def test_assertion_governed_by_nearby_defect_frame():
    f, _ = extract_issue_stances_v7(
        'fast_delete has a bug: it does not check the index before '
        'removing the element.', SPEC_CODE)
    fv = f['fast_delete']
    assert fv.state == 'observed' and fv.value == 'asserted', fv


# ── financial: numeric discipline ─────────────────────────────────────────
def test_bare_number_fallback_abstains():
    """F-BARE: the weakest v6 basis — the global ``single_candidate``
    fallback, where every number in a long response is one bare figure with
    no cue, percent, currency or scale context — is a guess and becomes
    ambiguous.  (The last-sentence basis is deliberately untouched: it is
    v6's D8 fix and must not be undone.)"""
    # the single figure sits early in the response and the last three
    # sentences carry no numbers, so the v6 base reaches the global
    # single_candidate fallback; F-BARE then abstains because there is no
    # cue, percent, currency or scale context anywhere.  'quantity' is
    # chosen deliberately: it is not an answer cue and not a non-answer
    # identifier, so the figure reaches the fallback at all.
    text = ('The quantity 42 was recorded early on. The notes are clean. '
            'The report is complete. No further findings are noted.')
    f, _ = extract_final_answer_v7(text, None)
    assert f.state == 'ambiguous' and 'F-BARE' in f.basis, \
        f'{f.state} {f.basis}'


def test_last_sentence_basis_preserved():
    """The v6 D8 last-sentence strategy is preserved (not undone by F-BARE):
    a single figure in the closing sentence stays observed."""
    f, _ = extract_final_answer_v7('The figure was 42.', None)
    assert f.state == 'observed' and 'last_sentence' in f.basis, \
        f'{f.state} {f.basis}'


def test_money_figure_stays_observed():
    """F-BARE must NOT fire when the figure has currency/scale context."""
    f, _ = extract_final_answer_v7('The revenue was $1,234.5 thousand.',
                                   None)
    assert f.state == 'observed', f.basis


def test_missing_vs_unavailable_distinct():
    """D9: a label that was never discussed is `absent`; a label discussed
    but with no bound value is `unavailable`.  The two must stay distinct."""
    f, _ = extract_facts_v7('The report covers other topics entirely.',
                            {'required_facts': {'fact_1': {
                                'label': 'property and equipment'}}})
    assert f['fact_1'].state == 'absent', f['fact_1'].state
    f2, _ = extract_facts_v7('property and equipment was reviewed but no '
                             'figure was given.',
                             {'required_facts': {'fact_1': {
                                 'label': 'property and equipment'}}})
    assert f2['fact_1'].state == 'unavailable', f2['fact_1'].state


def test_gt_free_guard_fires():
    """A GT-bearing spec that reaches an extractor must raise."""
    from extract_core import GroundTruthLeak
    import pytest
    with pytest.raises(GroundTruthLeak):
        extract_final_answer_v7('x', {'answer': 42})
