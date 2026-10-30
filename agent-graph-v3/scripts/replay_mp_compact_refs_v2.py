"""Strictly offline diagnosis of frozen old-contract responses; never model replay."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import jsonschema
from benchmark.mp_adjudicator_compact.codec import encode
from benchmark.mp_adjudicator_v2.engine import observations
from benchmark.mp_adjudicator_v2.contract import A_PROMPT
from benchmark.mp_compact_refs_v2.engine import evaluate
from benchmark.mp_adjudicator.contract import MODEL
from benchmark.mp_compact_refs_v2.decode import decode
from benchmark.mp_compact_refs_v2.contract import schema, INSTRUCTION
from benchmark.mp_compact_smoke.transport import Config

CACHE = ['882611d438e64c43ec9446684da04b7f38574ceba0a06341ae1b04f763d0db1b',
         '0aa4bf2455712bede2c311b4a939622b508d765d20d9ce09a2a2db8bca3574ad']


def run(output):
    output.mkdir(parents=True, exist_ok=False)
    summary = []
    for i, case in enumerate(('AON','SPGI')):
        paths = [ROOT/f'analysis/mp_compact_smoke/cache/{CACHE[i]}.json',
                 ROOT/f'analysis/mp_compact_smoke/smoke_001/packet_{i}.json',
                 ROOT/f'analysis/mp_compact_smoke/smoke_001/compact_{i}.json',
                 ROOT/'analysis/mp_compact_smoke/smoke_001/implementation_hashes.json']
        copies = output/case; copies.mkdir()
        hashes = {}
        for path in paths:
            data=path.read_bytes(); (copies/path.name).write_bytes(data)
            hashes[str(path.relative_to(ROOT))]=hashlib.sha256(data).hexdigest()
        record=json.loads(paths[0].read_text()); packet=json.loads(paths[1].read_text()); codec=encode(packet)
        assert codec == json.loads(paths[2].read_text())
        response=json.loads(record['raw_response']['choices'][0]['message']['content'])
        original=record['identity']['request']
        jsonschema.validate(response, original['schema'])
        report=decode(packet,codec,response)
        report.update(response_contract='original frozen contract; not revised-schema output',
                      original_schema_valid=True, source_sha256=hashes,
                      original_implementation_sha256=record['identity']['implementation_sha256'])
        if report['accepted_event_ids']:
            valid,errors=observations(packet,report['decoded'])
            report.update(observation_verification_status='completed',verified_observations=valid,observation_errors=errors)
        else:
            report.update(observation_verification_status='not_run_no_valid_rows',verified_observations={},observation_errors=None)
        revised=deepcopy(original); revised['schema']=schema(packet,codec); revised['prompt']=INSTRUCTION+A_PROMPT
        identity,_=Config().identity(revised); bounds=Config().bounds(identity)
        report['revised_stage_a_bounds']=bounds
        report['within_input_guard']=bounds['input_token_bound']<=Config().max_input_tokens
        class SizeSession:
            audit=[]
            def ask(self, stage, prompt, schema, payload):
                if stage=='A':
                    return {'events':[dict(event_id=eid,review='reviewed',kind='other',
                        summary='Sizing scenario only; not semantic evidence. '*4,
                        evidence=[codec['reverse'][card['evidence_block_ids'][0]]])
                        for eid,card in packet['cards'].items() if card['evidence_block_ids']]}
                request=dict(version=original['version'],model=MODEL,temperature=0,
                    stage=stage,prompt=prompt,schema=schema,input=payload)
                self.bounds=Config().bounds(Config().identity(request)[0])
                raise RuntimeError('offline_size_capture_only')
        sizing=SizeSession(); evaluate(packet,sizing,enabled=True)
        report['stage_b_synthetic_full_review_bounds']=sizing.bounds
        report['semantic_result']='unknown; no recovered verified target observations'
        (copies/'diagnostics.json').write_text(json.dumps(report,indent=2))
        summary.append(dict(case=case,error_counts=report['error_counts'],rows=len(report['rows']),
            accepted_rows=len(report['accepted_event_ids']),packet_events=len(packet['cards']),
            bounds=bounds,stage_b_scenario_bounds=sizing.bounds,within_input_guard=report['within_input_guard']))
    (output/'summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary,indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args().output)
