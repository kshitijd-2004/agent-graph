#!/usr/bin/env python3.11
"""
Retry pass for adjudication rows that FAILED (adjudicator output not JSON,
or adjudicator transport failure).  Re-issues the call under the CURRENT
prompt (which carries the mandated JSON-only directive), so retries are
separately cached and separately attributed.  Rows that succeed on retry
are merged into the adjudicated file; rows that fail again remain recorded
failures — never coerced.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, os.path.join(REPO, 'analysis', 'measurement_v7'))
sys.path.insert(0, os.path.join(HERE))
from providers import JudgeConfig                      # noqa: E402
from runner import JudgeClient                          # noqa: E402

OUT = os.path.join(REPO, 'analysis', 'measurement_v7', 'out')


def _parse_first_object(text: str) -> Dict[str, Any]:
    """Extract the FIRST complete JSON object from a completion.

    Mirrors the judge parser's contract: a model may emit prose before or
    after the object, or a second object; a leading fence is stripped and
    ``raw_decode`` stops at the end of the first balanced object.  Only a
    completion with NO complete leading object raises."""
    import re
    t = (text or '').strip()
    m = re.match(r'^```(?:json)?\s*(\{.*\})\s*```$', t, re.S)
    if m:
        t = m.group(1)
    i = t.find('{')
    if i < 0:
        raise ValueError('no JSON object')
    try:
        obj, _end = json.JSONDecoder().raw_decode(t[i:])
    except json.JSONDecodeError as e:
        raise ValueError(f'not JSON: {e}')
    if not isinstance(obj, dict):
        raise ValueError('JSON is not an object')
    return obj


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--max-calls', type=int, default=40)
    ap.add_argument('--offset', type=int, default=0)
    a = ap.parse_args()

    import run_adjudication as R
    cfg = R._adjudicator()
    client = JudgeClient(cfg)
    dst = os.path.join(OUT, 'judge_adjudicated_ai.jsonl')
    rows = [json.loads(l) for l in open(dst) if l.strip()]
    failed = [r for r in rows if not r.get('adjudicated')
              and r.get('reason', '').startswith('adjudicator')]
    print(f'failed adjudication rows: {len(failed)}')
    todo = failed[a.offset:a.offset + a.max_calls]
    pk_ext = R._packets('extraction')
    pk_pair = R._packets('pair')
    n_recovered = 0
    new_rows: Dict[str, Dict[str, Any]] = {}
    import json as _json
    # the wrapped disagreement rows (kind/fields/fields_all) that
    # _user_prompt expects — the same shape run_adjudication feeds it
    wrapped = {f"{w['kind']}:{w['item_token']}": w
               for w in R._disagreements()}
    for r in todo:
        key = f"{r['kind']}:{r['item_token']}"
        packet = (pk_ext if r['kind'] == 'extraction'
                  else pk_pair).get(r['item_token'], {})
        row = wrapped.get(key)
        if row is None:
            continue
        user = R._user_prompt(row, packet)
        prompt = R._v2_prompt(user, r['item_token'])
        rec = client.annotate(prompt, 'adjudicator', key,
                              prompt['prompt_sha256'])
        base = dict(r)
        base['retry_attempt'] = True
        if rec['status'] != 'ok':
            base['reason'] = f'adjudicator {rec["status"]} on retry: ' \
                f'{rec.get("error", "")}'
            base['adjudicated'] = False
            new_rows[key] = base
            continue
        try:
            parsed = _parse_first_object(rec.get('text', ''))
        except ValueError:
            base['reason'] = 'adjudicator output not JSON on retry; ' \
                'not coerced'
            base['adjudicated'] = False
            base['raw'] = rec.get('text', '')[:500]
            new_rows[key] = base
            continue
        uf = parsed.get('unresolved_fields') or []
        base.update({
            'adjudicated': not uf,
            'resolution': parsed.get('resolved_labels'),
            'explanations': parsed.get('explanations') or {},
            'unresolved_fields': uf,
            'confidence': parsed.get('confidence')})
        if not uf:
            n_recovered += 1
        new_rows[key] = base
    merged = [new_rows.get(f"{r['kind']}:{r['item_token']}", r)
              for r in rows]
    with open(dst, 'w') as f:
        for r in merged:
            f.write(_json.dumps(r, sort_keys=True) + '\n')
    still = sum(1 for r in merged if not r.get('adjudicated'))
    print(f'retried {len(todo)}; recovered {n_recovered}; '
          f'rows still unadjudicated: {still}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
