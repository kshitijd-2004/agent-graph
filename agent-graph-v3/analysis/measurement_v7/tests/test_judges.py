"""
Judge infrastructure: independence, blinding, provenance, API failure
handling, and the two-family config requirement.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, os.path.join(HERE, '..', 'judges'))

from providers import (default_judges, availability_report,  # noqa: E402
                       discover_configured)
from orchestrator import verify_blinding  # noqa: E402
from agreement import (kappa, raw_agreement,  # noqa: E402
                       krippendorff_alpha, numeric_agreement)
from schemas import (validate_extraction, validate_pair,  # noqa: E402
                    parse_json_completion, JudgeOutputError)


def test_two_families_required():
    judges = default_judges()
    fams = {j.family for j in judges.values()}
    assert len(fams) == 2, f'expected 2 distinct families, got {fams}'


def test_judge_b_is_explicit_when_gpt_is_unavailable():
    """GPT-6 is not reachable on this relay (catalog probed: 29 Claude
    models only, no gpt-*/o* identifier accepted).  judge_b therefore runs
    a DISTINCT second model on the same relay, and that substitution is
    EXPLICIT: the config carries a substitution_note and no result may be
    read as two-vendor (Claude vs GPT) agreement.  If a GPT credential is
    ever set, the gpt config takes over and the caveat is lifted — the
    important invariant is that a non-gpt judge_b can NEVER be
    silent/undocumented."""
    import os
    import providers
    judges = providers.default_judges()
    jb = judges['judge_b']
    gpt_key = os.environ.get('OPENAI_API_KEY') or os.environ.get(
        'MP_SEMANTIC_API_KEY')
    if gpt_key:
        assert jb.family == 'gpt'
        assert 'substitution_note' not in jb.extra
    else:
        assert jb.family != 'gpt'
        note = jb.extra.get('substitution_note')
        msg = ('a non-gpt judge_b must carry an explicit substitution '
               'note stating the independence limit')
        assert note and 'NOT' in note.upper(), msg
        # distinct model from judge_a
        assert jb.model != judges['judge_a'].model


def test_blinding_scan_flags_a_leaked_label():
    good = [{'item_token': 'T1', 'task_prompt': 'audit one execution'}]
    assert verify_blinding(good)['blinding_ok'] is True
    bad = [{'item_token': 'T1',
            'note': 'this is a LEP_INDIRECT_PROMPT_INJECTION row'}]
    r = verify_blinding(bad)
    assert r['blinding_ok'] is False
    assert r['forbidden_hits']


def test_schema_rejects_observed_without_evidence():
    labels = {'claim_verdicts': {'state': 'observed',
                                 'value': 'SUPPORTED', 'evidence': ''}}
    r = validate_extraction(labels, ['claim_verdicts'])
    assert r['ok'] is False
    assert any('evidence' in p for p in r['problems'])


def test_schema_accepts_observed_with_evidence():
    labels = {'claim_verdicts': {'state': 'observed',
                                 'value': 'SUPPORTED',
                                 'evidence': 'claim 1 supported by paper B'}}
    assert validate_extraction(labels, ['claim_verdicts'])['ok']


def test_pair_schema_requires_one_difference_index_in_range():
    rec = {'pair_token': 'P1', 'reference_set_usable': 'yes',
           'member_judgments': [
               {'member_index': 0, 'assertions_differs': 'no',
                'decisions_differs': 'no', 'actions_differs': 'no',
                'outcomes_differs': 'no', 'evidence': ''},
               {'member_index': 1, 'assertions_differs': 'yes',
                'decisions_differs': 'no', 'actions_differs': 'no',
                'outcomes_differs': 'no', 'evidence': 'differs'}],
           'any_difference_member_index': 1}
    assert validate_pair(rec, 2)['ok']
    # an out-of-range index is a schema failure
    rec2 = dict(rec, any_difference_member_index=9)
    assert validate_pair(rec2, 2)['ok'] is False


def test_json_parse_strips_a_single_fenced_block():
    d = parse_json_completion('```json\n{"a":1}\n```')
    assert d == {'a': 1}
    import pytest
    with pytest.raises(JudgeOutputError):
        parse_json_completion('not json at all')


def test_agreement_stats_are_labelled_ai_judge_agreement():
    r = raw_agreement(['yes', 'no', 'yes'], ['yes', 'no', 'no'])
    assert r['label'] == 'ai_judge_agreement'
    assert abs(r['agreement'] - 2 / 3) < 1e-9
    k = kappa(['SUPPORTED'] * 10 + ['NOT_ENOUGH'] * 10,
              ['SUPPORTED'] * 8 + ['NOT_ENOUGH'] * 12)
    assert k['label'] == 'ai_judge_agreement'
    assert 'kappa' in k
    a = krippendorff_alpha([['yes', 'yes'], ['no', 'yes'],
                            ['yes', 'no'], ['no', 'no']])
    assert a['label'] == 'ai_judge_agreement'
    assert 'alpha' in a
    n = numeric_agreement([100.0, 200.0, 50.0], [101.0, 190.0, 60.0])
    assert n['n_numeric'] == 3 and n['label'] == 'ai_judge_agreement'


def test_api_failure_is_recorded_not_fabricated(monkeypatch, tmp_path):
    """A judge whose transport fails after retries must yield a failed
    record, never an invented label."""
    from providers import JudgeConfig
    cfg = JudgeConfig(name='judge_a', family='claude_opus',
                      provider='anthropic', model='claude-opus-5-5',
                      base_url='http://localhost:1', credential_var='X',
                      cache_dir=str(tmp_path / 'cache'))
    monkeypatch.setenv('X', 'dummy-token')
    from runner import JudgeClient
    client = JudgeClient(cfg, max_retries=0, retry_wait_s=0.0)
    prompt = {'system': 's', 'user': 'u', 'version': 'v',
              'prompt_sha256': 'h'}
    rec = client.annotate(prompt, 'judge_a', 'T1', 'input-hash')
    assert rec['status'] == 'failed'
    assert 'error' in rec
    # no annotation text is fabricated
    assert 'text' not in rec or not rec.get('text')
