"""Five-case offline packet and authored-contract validation. No HTTP or paid calls."""
import argparse
from pathlib import Path
import json
import hashlib
from benchmark.mp_adjudicator.packet import build_packet,observation_view
from benchmark.mp_adjudicator.engine import Session,evaluate
from benchmark.mp_adjudicator.contract import MODEL,STAGE_A_PROMPT,STAGE_A_SCHEMA,STAGE_B_PROMPT,STAGE_B_SCHEMA
from scripts.pilot_semantic_mp import select

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'tests/fixtures/mp_adjudicator/real_case_contracts.json'


def run(output):
    if output.exists() or not output.resolve().is_relative_to((ROOT/'analysis/mp_adjudicator_dev1').resolve()):raise ValueError('new dedicated output directory required')
    fixture=json.loads(FIXTURE.read_text())
    for p,h in fixture['input_hashes'].items():
        if hashlib.sha256((ROOT/p).read_bytes()).hexdigest()!=h:raise ValueError('frozen input changed: '+p)
    output.mkdir(parents=True);summaries=[]
    for entry,t,cs,source in select('pilot'):
        task=json.loads((ROOT/entry['fixture_path']).read_text())['task_prompt']
        packet=build_packet(t,cs,enabled=True,task_text=task)
        case=fixture['cases'][t.metadata['fixture_id']]
        def authored(req):
            return {'model':MODEL,'content':json.dumps(case['stage_a'] if req['stage']=='A' else case['stage_b']),
                    'judgment_source':'authored_contract_fixture_not_live_model_or_independent_ground_truth'}
        session=Session(authored);result=evaluate(packet,session,enabled=True)
        unavailable=evaluate(packet,Session(),enabled=True)
        label=t.metadata['fixture_id'];directory=output/label;directory.mkdir()
        for name,value in [('packet.json',packet),('authored_assessment.json',result),('without_semantic_response.json',unavailable)]:
            (directory/name).write_text(json.dumps(value,indent=2))
        bytes_per_request=[len(json.dumps(a['request'],ensure_ascii=False,separators=(',',':')).encode()) for a in session.audit]
        tokens=sum((n+3)//4 for n in bytes_per_request)
        summary={'fixture_id':label,'packet_sha256':packet['packet_sha256'],'selected_events':packet['coverage']['selected_event_count'],
                 'all_events':packet['coverage']['total_event_count'],'unique_contents':len(packet['contents']),
                 'stage_request_bytes':bytes_per_request,'estimated_total_input_tokens':tokens,'planned_model_calls':2,
                 'cost_scenario_usd':tokens*.4/1000000+2*1500*1.6/1000000,
                 'larger_output_cost_scenario_usd':tokens*.4/1000000+2*6000*1.6/1000000,
                 'cost_assumptions':'historical smoke rates .40 input/1.60 output per million; bytes/4 estimate, scenarios 1500 and 6000 output tokens/stage, no caching discount; not a spend cap or tokenizer count',
                 'authored_fixture_endpoints':{k:v['state'] for k,v in result['endpoints'].items()},
                 'authored_fixture_verified_findings':len(result.get('event_findings',[])),
                 'authored_fixture_rejected_findings':len(result.get('rejected_findings',[])),
                 'without_response_status':unavailable['endpoints']['adverse_propagation']['status'],
                 'paid_calls':0,'independent_semantic_validation':False}
        summaries.append(summary)
    (output/'summary.json').write_text(json.dumps(summaries,indent=2));print(json.dumps(summaries,indent=2))
    return summaries


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    run(p.parse_args().output)
