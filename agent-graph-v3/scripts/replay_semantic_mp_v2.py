"""Offline-only v2 replay of the 74 frozen smoke responses. Never imports a network adapter."""
import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from benchmark.semantic_mp.extraction import validate as legacy_validate
from benchmark.semantic_mp_v2.extraction import Extractor, digest, validate, locate
from benchmark.semantic_mp_v2.evaluator import evaluate_trace
from scripts.pilot_semantic_mp import select

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'tests/fixtures/semantic_mp_v2/recorded_smoke.json'


def replay(output):
    if output.exists():raise ValueError('refusing to overwrite results')
    if not output.resolve().is_relative_to((ROOT/'analysis/semantic_mp_v2').resolve()):raise ValueError('dedicated v2 output directory required')
    if hashlib.sha256(FIXTURE.read_bytes()).hexdigest()!='6861078b260b35e685c3f5a1f8a803c43435a6ae71cc41386df62e752dbf8e33':raise ValueError('frozen fixture changed')
    fixture=json.loads(FIXTURE.read_text());recorded={};rows=[]
    for path,expected in fixture['source_hashes'].items():
        if hashlib.sha256((ROOT/path).read_bytes()).hexdigest()!=expected:raise ValueError('original smoke artifact changed: '+path)
    for r in fixture['responses']:
        text=r['text'];payload=r['payload'];recorded[digest(text)]=payload
        try:legacy_validate(text,payload);old='valid'
        except Exception as e:old=str(e)
        repaired=json.loads(json.dumps(payload))
        for p in repaired['propositions']:
            try:span=locate(text,p['quote']);p['start']=span.start;p['end']=span.end
            except ValueError:pass
        try:legacy_validate(text,repaired);syntax='valid'
        except Exception as e:syntax=str(e)
        rows.append({'input_sha256':r['input_sha256'],'text_sha256':digest(text),'legacy_validation':old,
                     'offset_recovery_only':syntax,'grounding':asdict(validate(text,payload,legacy=True))})
    extractor=Extractor(recorded)
    decisions=[]
    for e,t,c,source in select('smoke'):
        decisions.append({'trace_id':t.trace_id,'trace_path':e['path'],
            'result':evaluate_trace(t,c,extractor=extractor,authoritative_text=source,enabled=True)})
    endpoints=Counter(x['state'] for d in decisions for x in d['result']['endpoints'].values())
    unique_results=list(extractor.cache.values())
    summary={'version':'semantic-mp-offline-replay-v2','paid_calls':0,'new_model_calls':extractor.calls,
        'recorded_model_responses':len(rows),'fixture_sha256':hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        'legacy_validation':dict(Counter(r['legacy_validation'] for r in rows)),
        'syntactic_offset_recovery_only':dict(Counter(r['offset_recovery_only'] for r in rows)),
        'migrated_schema_valid_responses':sum(r['grounding']['syntactically_valid'] for r in rows),
        'raw_proposed_items':sum(len(r['payload']['propositions']) for r in fixture['responses']),
        'grounded_cached_items':sum(len(r['grounding']['propositions']) for r in rows),
        'grounded_cached_response_status':dict(Counter(r['grounding']['status'] for r in rows)),
        'grounding_rejection_reasons':dict(Counter(i['code'] for r in rows for i in r['grounding']['issues'])),
        'unique_full_inputs':len(unique_results),'memoized_occurrence_hits':extractor.hits,
        'unique_input_status':dict(Counter(x.status for x in unique_results)),
        'endpoint_states_including_exposure':dict(endpoints),
        'grounding_accuracy':'not independently measured; accepted counts are not accuracy',
        'development_grounding_review':{'cases':10,'independent':False,'annotation_file':'tests/fixtures/semantic_mp_v2/grounding_review.json'},
        'legacy_analysis_ready_population':873,'bounded_legacy_analysis_ready_sample':len(decisions),
        'revised_primary_evaluable_sample':sum(d['result']['endpoints']['adverse_propagation']['state']!='unknown' for d in decisions),
        'alignment_accuracy':'development regressions only; no independent held-out estimate',
        'semantic_success':all(d['result']['endpoints']['adverse_propagation']['state']!='unknown' for d in decisions),
        'transport_status':'historical transport completed; this replay is offline',
        'endpoint_denominators':{ep:{'eligible':len(decisions),'evaluable':sum(d['result']['endpoints'][ep]['state']!='unknown' for d in decisions)} for ep in decisions[0]['result']['endpoints']},
        'code_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [* (ROOT/'benchmark/semantic_mp_v2').glob('*.py'),Path(__file__)]}}
    output.mkdir(parents=True)
    for name,data in [('response_validation.json',rows),('decisions.json',decisions),('summary.json',summary)]:
        (output/name).write_text(json.dumps(data,indent=2))
    print(json.dumps(summary,indent=2))
    return rows,decisions,summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    replay(p.parse_args().output)
