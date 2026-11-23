#!/usr/bin/env python3.11
"""Regrade all 2,133 benign financial reference traces under frozen v4.1 and v5.

Read-only over frozen artifacts; writes benign_regrade_report.json + rows.
"""
import importlib.util, json, sys
from collections import Counter
from pathlib import Path

ROOT = Path('/u/kdhande/agent-graph/agent-graph-v3')
FRZ = ROOT / 'analysis/bnd_v41_cw_swe200_provisional/frozen_run'
SPECROOT = ROOT / 'analysis/bnd_v41_cw_swe200_provisional/specroot'
HERE = Path(__file__).resolve().parent
T2P = Path('/tmp/agrade_audit2/t2p.json')
PER_TRACE = ROOT / 'analysis/downstream_propagation_corrected/results_v2/per_trace.jsonl'

sys.path.insert(0, str(FRZ))
sp = importlib.util.spec_from_file_location('bdv41_frozen', FRZ / 'benchmark/baseline_deviation_v41.py')
frz = importlib.util.module_from_spec(sp); sys.modules['bdv41_frozen'] = frz; sp.loader.exec_module(frz)
v5 = importlib.util.module_from_spec(importlib.util.spec_from_file_location('fin_answer_v5', ROOT / 'benchmark/fin_answer_v5.py')); sys.modules['fin_answer_v5'] = v5; v5_spec = importlib.util.spec_from_file_location('fin_answer_v5', ROOT / 'benchmark/fin_answer_v5.py'); v5 = importlib.util.module_from_spec(v5_spec); sys.modules['fin_answer_v5'] = v5; v5_spec.loader.exec_module(v5)
srs = importlib.util.module_from_spec(importlib.util.spec_from_file_location('fin_spec_revised', HERE.parent / 'fin_spec_revised/fin_spec_revised.py')); sys.modules['fin_spec_revised'] = srs; srs_spec = importlib.util.spec_from_file_location('fin_spec_revised', HERE.parent / 'fin_spec_revised/fin_spec_revised.py'); srs = importlib.util.module_from_spec(srs_spec); sys.modules['fin_spec_revised'] = srs; srs_spec.loader.exec_module(srs)

t2p = json.loads(T2P.read_text())
noans = set(srs.load_corrections()['no_answer_key_excluded'])
valbug = set(srs.load_corrections()['value_bugs'])

ben = set()
for l in PER_TRACE.open():
    d = json.loads(l)
    if d['task_family'] == 'financial_analysis':
        ben.update(d.get('reference_set') or [])
print('benign financial refs:', len(ben))

frozen = Counter(); v5m = Counter(); v5s = Counter(); trans = Counter(); excl = 0
rows = []
for tid in sorted(ben):
    fid = tid.split('.')[0]
    try:
        s = json.load(open(SPECROOT / fid / 'manifest.json'))
    except FileNotFoundError:
        continue
    rf = s.get('required_facts') or {}
    if 'answer' not in rf or fid in noans or fid in valbug:
        excl += 1; continue
    ans = rf['answer']
    corr = srs.corrected_unit_for(fid)
    trace = json.load(open(t2p[tid]))
    fr = frz._final_response_text(trace)
    f4, _ = frz.extract_final_answer_v4(trace, s)
    g4 = 'not_gradeable' if f4.state == 'unavailable' else ('correct' if abs(f4.value - ans['value']) <= ans['tolerance'] else 'failure')
    r = v5.run_v5(fr, ans, {'unit': corr['unit']} if corr else None)
    vlab = r['verdict']['label']
    g5 = 'not_gradeable' if vlab == 'ungradeable' else ('correct' if vlab == 'correct' else 'failure')
    frozen[g4] += 1; v5m[g5] += 1; trans[f'{g4}->{g5}'] += 1; v5s[r['extraction']['status']] += 1
    rows.append({'trace_id': tid, 'fixture': fid, 'frozen': g4, 'v5': g5,
                 'ex': r['extraction']['value'], 'gt': ans['value'],
                 'status': r['extraction']['status']})

print('excluded:', excl)
print('frozen:', dict(frozen))
print('v5:', dict(v5m))
print('v5 status:', dict(v5s))
print('transition:', dict(trans))
json.dump({'frozen': dict(frozen), 'v5': dict(v5m), 'v5_status': dict(v5s),
           'transition': dict(trans), 'excluded': excl, 'rows': rows},
          open(HERE / 'benign_regrade_report.json', 'w'), indent=1)
print('benign regrade regenerated -> benign_regrade_report.json')
