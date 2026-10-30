"""Offline lossless coverage/size audit; no API transport or semantic judgments."""
import argparse,json,hashlib
from pathlib import Path
from benchmark.mp_adjudicator.engine import Session
from benchmark.mp_adjudicator.smoke_transport import Config
from benchmark.mp_adjudicator.contract import MODEL
from benchmark.mp_adjudicator_compact.codec import encode
from benchmark.mp_adjudicator_compact.engine import evaluate
from benchmark.mp_adjudicator_v2.engine import evaluate as original_evaluate
ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    out=args.output.resolve();base=(ROOT/'analysis/mp_compact_dev1').resolve()
    if out.exists() or not out.is_relative_to(base) or out==base:raise ValueError('new dedicated output required')
    out.mkdir(parents=True);rows=[]
    size=lambda x:len(json.dumps(x,ensure_ascii=False,separators=(',',':')).encode())
    for i in (0,1):
        source=ROOT/f'analysis/mp_adjudicator_v2_dev1/final_offline/packet_{i}.json';original=source.read_bytes();p=json.loads(original);c=encode(p)
        captured=[]
        def mock(req):
            captured.append(req)
            # Full-review size scenario only. These are NOT semantic annotations.
            response={'events':[{'event_id':eid,'review':'reviewed','kind':'other','summary':'Size scenario only.',
                        'evidence':[c['reverse'][bid] for bid in card['evidence_block_ids'][:1]]} for eid,card in p['cards'].items()]} if req['stage']=='A' else {}
            return {'model':MODEL,'content':json.dumps(response)}
        evaluate(p,Session(mock),enabled=True)
        original_requests=[]
        def original_mock(req):
            original_requests.append(req)
            response={'events':{eid:{'review':'reviewed','kind':'other','summary':'Size scenario only.',
                        'evidence':card['evidence_block_ids'][:1]} for eid,card in p['cards'].items()}} if req['stage']=='A' else {}
            return {'model':MODEL,'content':json.dumps(response)}
        original_evaluate(p,Session(original_mock),enabled=True)
        bounds=[]
        for req,old in zip(captured,original_requests):
            cfg=Config();identity,_=cfg.identity(req);b=cfg.bounds(identity)
            b.update(stage=req['stage'],wire_body_bytes=size(identity['body']),passes_existing_input_guard=b['input_token_bound']<=cfg.max_input_tokens)
            old_identity,_=cfg.identity(old)
            b['original_v2_wire_bytes']=size(old_identity['body'])
            b['wire_reduction_percent']=round(100*(1-b['wire_body_bytes']/b['original_v2_wire_bytes']),2)
            b['estimated_cost_with_max_output_usd']=str(cfg.cost(b['estimated_input_tokens'],cfg.max_output_tokens))
            bounds.append(b)
        row={'case':['AON','SPGI'][i],'original_sha256':hashlib.sha256(original).hexdigest(),
             'complete_local_packet_bytes':len(original),'compact_view_bytes':size(c['view']),
             'coverage':c['coverage'],'requests':bounds,'stage_b_scenario':'All event reviews, one full block each, short placeholder summaries; size scenario NOT semantic assessment',
             'actual_api_calls':0,'planned_maximum_calls':2}
        (out/f'packet_{i}.json').write_bytes(original)
        (out/f'compact_{i}.json').write_text(json.dumps(c,indent=2))
        (out/f'request_size_scenarios_{i}.json').write_text(json.dumps(captured,indent=2))
        rows.append(row)
    (out/'summary.json').write_text(json.dumps(rows,indent=2));print(json.dumps(rows,indent=2))

if __name__=='__main__':main()
