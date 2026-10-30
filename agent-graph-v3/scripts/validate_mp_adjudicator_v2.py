"""Offline block integrity and size audit. No semantic transport is instantiated."""
import argparse
import hashlib
import json
from pathlib import Path
from benchmark.mp_adjudicator_v2.packet import bind,resolve,stage_a_view
from benchmark.mp_adjudicator_v2.contract import stage_a_schema,stage_b_schema
ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    out=args.output.resolve();base=ROOT/'analysis/mp_adjudicator_v2_dev1'
    if out.exists() or not out.is_relative_to(base.resolve()) or out==base.resolve():raise ValueError('new dedicated output subdirectory required')
    fixture=json.loads((ROOT/'tests/fixtures/mp_adjudicator_v2/failed_smoke.json').read_text())
    for name,h in fixture['input_hashes'].items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=h:raise ValueError('saved smoke input changed')
    out.mkdir(parents=True);summary=[]
    for i in (0,1):
        p=bind(json.loads((ROOT/f'analysis/mp_hybrid_smoke/smoke_001/packet_{i}.json').read_text()))
        for bid in p['blocks']:resolve(p,bid)
        size=lambda obj:len(json.dumps(obj,ensure_ascii=False,separators=(',',':')).encode())
        row={'case':['AON','SPGI'][i],'events':len(p['cards']),'blocks':len(p['blocks']),
             'packet_bytes':size(p),'stage_a_view_bytes':size(stage_a_view(p)),
             'stage_a_schema_bytes':size(stage_a_schema(p)),
             'maximum_calls':2,'no_verified_target_behavior_calls':1,'paid_calls':0,
             'semantic_accuracy':'not evaluated','stage_b_size':'depends on new Stage A response'}
        summary.append(row);(out/f'packet_{i}.json').write_text(json.dumps(p,indent=2))
    (out/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))

if __name__=='__main__':main()
