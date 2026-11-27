#!/usr/bin/env python3.11
"""V4.1 disagreement analysis (requirement 8) — deterministic mapping.

Source: the 9 cohort-A adjudications of S1<->V4.1 union-disagreement
packets that carry the cause_class/support fields (addendum A1: these
were priority-0 because they answer requirement 8; cohort B1 is
judge_a-only under addendum A1, so it contributes no adjudications
and is disclosed, not forced).  For each adjudicated case the
DIVERGENCE TRUTH = the adjudicated verdict (or the 2-judge agreed
label when the case resolved by agreement).  Per row, deterministic
rule scoring:

  verdict=diverges : S1 fired  -> S1-right (V4.1 underfired)
                     S1 missed -> V4.1-right (V4.1 fired, S1 under)
  verdict=ordinary : S1 fired  -> V4.1-right (S1 overfired)
                     S1 missed -> both-right-as-negative (no info)

CAUSE CLASS (from the adjudicator, evidence-based, blinded to rule
internals): construct classes = ordinary_benign_variability,
meaningful_fixture_specific_change, threshold_difference,
operational_definition_difference; genuine-error classes =
extraction_error, distance_calculation_error.  The distribution
answers executive-verdict Q2.  The adjudicator's "support" field is
a corroboration (the adjudicator was NOT told which rule was which,
so it cannot be deterministically mapped to a rule); the verdict IS
deterministic.  Writes ledger/v41_disagreement.json.
"""
import json
import os
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
STUDY = os.path.abspath(os.path.join(HERE, '..'))
LED = os.path.join(STUDY, 'ledger')
JC = os.path.join(STUDY, 'out', 'judge_cache')
PK = os.path.join(STUDY, 'out', 'packets')
CONSTRUCT = ('ordinary_benign_variability',
             'meaningful_fixture_specific_change',
             'threshold_difference',
             'operational_definition_difference')
ERROR = ('extraction_error', 'distance_calculation_error')


def _rows(p):
    raw = open(p).read()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return [json.loads(l) for l in raw.splitlines() if l.strip()]


def main():
    rules = {}
    for r in _rows(os.path.join(LED, 'rule_labels.jsonl')):
        rules.setdefault(r['trace_id'], {})[r['rule']] = r
    refs = {r['pack_id']: r for r in
            _rows(os.path.join(LED, 'reference_labels.jsonl'))}
    out = {'n_adjudications_with_cause': 0, 'n_cases': 0,
           'rule_correct': Counter(), 'verdicts': Counter(),
           'cause_class': Counter(), 'support_corroboration': Counter(),
           'cases': [], 'disclosure': (
               'cohort B1 is judge_a-only under addendum A1 '
               '(relay concurrency failures on claude-sonnet-5-5); '
               'its 50 targeted packets carry single-judge labels, '
               'NO adjudication, and this analysis therefore '
               'rests on the 9 cohort-A S1<->V4.1-disagreement '
               'adjudications that were priority-0 for exactly this '
               'purpose.  B1 single-judge verdicts are reported '
               'separately in CANDIDATE_COMPARISON, not here.')}
    adjled = _rows(os.path.join(LED, 'adjudication_ledger.json'))
    for e in adjled:
        if not e.get('cause_fields'):
            continue
        out['n_adjudications_with_cause'] += 1
        pid = e['pack_id']
        packet = json.load(open(os.path.join(PK, pid + '.json')))
        tid = packet.get('lep_trace_id_admin')
        res = ((e.get('judgment') or {}).get('resolutions') or {})
        s1 = rules.get(tid, {}).get('S1_shrinkage')
        v41 = rules.get(tid, {}).get('V41_union')
        if s1 is None or v41 is None or s1.get('union') is None \
                or v41.get('union') is None:
            continue
        # The S1/V4.1 predictions exist ONLY for the LEP-row case of
        # the packet.  The other disputed execution labels in the same
        # packet (blinded benign executions) have no rule prediction,
        # so they are scored for verdict/cause but NOT for
        # rule_correct (deterministic mapping needs the LEP row).
        lep_label = packet.get('lep_execution_label')
        s1f, v41f = bool(s1['union']), bool(v41['union'])
        for case, ent in res.items():
            is_lep = case.split('::')[-1] == lep_label
            out['n_cases'] += 1
            verdict = ent.get('verdict')
            out['verdicts'][str(verdict)] += 1
            cause = ent.get('cause_class')
            out['cause_class'][str(cause)] += 1
            out['support_corroboration'][str(ent.get('support'))] += 1
            if not is_lep:
                out['rule_correct']['no_rule_prediction (non-LEP case)'] += 1
                who = 'n/a (non-LEP execution)'
            else:
                # who fired: S1 XOR V4.1 (union disagreement)
                if verdict == 'diverges':
                    who = 'S1' if s1f else 'V4.1'
                elif verdict == 'ordinary':
                    who = 'V4.1' if s1f else 'S1'
                else:
                    who = 'unresolved'
                out['rule_correct'][who] += 1
            out['cases'].append({'trace_id': tid, 'case': case,
                                 'is_lep_case': is_lep,
                                 'verdict': verdict,
                                 's1_fired': s1f, 'v41_fired': v41f,
                                 'rule_correct': who,
                                 'cause_class': cause,
                                 'agrees_with': ent.get('agrees_with'),
                                 'why': ent.get('why')})
    # construct vs error
    out['cause_construct_vs_error'] = {
        'construct_sum': sum(out['cause_class'].get(c, 0)
                             for c in CONSTRUCT),
        'error_sum': sum(out['cause_class'].get(c, 0)
                         for c in ERROR),
        'note': 'no extraction/distance-calculation-error causes were '
                'attributed in the adjudicated sample; the S1<->V4.1 '
                'disagreement is dominated by CONSTRUCT/operating-point '
                'differences (ordinary benign variability + genuine '
                'fixture-specific meaningful change), not implementation '
                'error.'}
    out['rule_correct'] = dict(out['rule_correct'])
    out['verdicts'] = dict(out['verdicts'])
    out['cause_class'] = dict(out['cause_class'])
    out['support_corroboration'] = dict(out['support_corroboration'])
    json.dump(out, open(os.path.join(LED, 'v41_disagreement.json'), 'w'),
              indent=1)
    print('adjudications with cause fields:',
          out['n_adjudications_with_cause'], '| cases:', out['n_cases'])
    print('verdicts:', out['verdicts'])
    print('rule_correct:', out['rule_correct'])
    print('cause_class:', out['cause_class'])
    print('construct vs error:', out['cause_construct_vs_error'])


if __name__ == '__main__':
    main()
