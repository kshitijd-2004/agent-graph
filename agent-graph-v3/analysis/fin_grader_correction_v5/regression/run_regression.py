#!/usr/bin/env python3.11
"""Run the B5 regression + metamorphic cases against ``fin_answer_v5``.

Read-only over the repository; writes ``regression_report.json`` next to this
file.  All cases are synthetic or development-derived — NO sealed held-out
example appears here (constraint B5).

Each case asserts the *label* (correct/incorrect/ungradeable) and, where
specified, the extracted *value*.  Metamorphic cases assert that reformatting
preserves the value and that a semantic change moves it.
"""
import json
import sys
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path('/u/kdhande/agent-graph/agent-graph-v3')

# load v5 from the repo (independent of the frozen benchmark package)
spec = importlib.util.spec_from_file_location(
    'fin_answer_v5', ROOT / 'benchmark' / 'fin_answer_v5.py')
v5 = importlib.util.module_from_spec(spec)
sys.modules['fin_answer_v5'] = v5   # required for @dataclass
spec.loader.exec_module(v5)


def run_case(c: dict) -> dict:
    out = {'id': c['id']}
    if 'text_a' in c:  # metamorphic
        ra = v5.run_v5(c['text_a'], {'unit': c['unit'], 'value': c['gt'],
                                     'tolerance': c.get('tol', 0.02)})
        rb = v5.run_v5(c['text_b'], {'unit': c['unit'], 'value': c['gt'],
                                     'tolerance': c.get('tol', 0.02)})
        va = ra['extraction']['value']; vb = rb['extraction']['value']
        out['value_a'] = va; out['value_b'] = vb
        out['ok'] = (va == c['expect_value_a'] and vb == c['expect_value_b'])
        out['expect'] = f"value_a={c['expect_value_a']} value_b={c['expect_value_b']}"
    else:
        r = v5.run_v5(c['text'], {'unit': c['unit'], 'value': c['gt'],
                                  'tolerance': c.get('tol', 0.02)})
        lab = r['verdict']['label']
        val = r['extraction']['value']
        ok = lab == c['expect_label']
        if 'expect_value' in c and c['expect_label'] in ('correct', 'incorrect'):
            ok = ok and (val == c['expect_value'])
        if 'expect_status' in c:
            ok = ok and (r['extraction']['status'] == c['expect_status'])
        out.update(label=lab, value=val, status=r['extraction']['status'],
                    basis=r['extraction']['basis'], ok=ok,
                    expect=f"label={c['expect_label']}"
                           + (f" value={c['expect_value']}" if 'expect_value' in c else '')
                           + (f" status={c['expect_status']}" if 'expect_status' in c else ''))
    out['desc'] = c.get('desc', '')
    out['note'] = c.get('note', '')
    return out


def main():
    cases = [json.loads(l) for l in
             open(HERE / 'regression_cases.jsonl')]
    res = [run_case(c) for c in cases]
    passed = sum(1 for r in res if r['ok'])
    for r in res:
        print(f"[{'PASS' if r['ok'] else 'FAIL'}] {r['id']:34s} "
              f"{r.get('label', r.get('value_a'))} "
              f"(expect {r['expect']})")
    report = {'total': len(res), 'passed': passed, 'failed': len(res) - passed,
              'results': res}
    (HERE / 'regression_report.json').write_text(json.dumps(report, indent=1))
    print(f"\n{passed}/{len(res)} regression cases pass -> regression_report.json")
    return 0 if passed == len(res) else 1


if __name__ == '__main__':
    sys.exit(main())
