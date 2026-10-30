"""AON/SPGI hybrid MP smoke runner. Preview is offline; execution is opt-in."""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sys

from benchmark.mp_adjudicator.contract import MODEL
from benchmark.mp_adjudicator.engine import Session, evaluate
from benchmark.mp_adjudicator.packet import build_packet
from benchmark.mp_adjudicator.smoke_transport import Config, Transport
from scripts.pilot_semantic_mp import select

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT/'analysis/mp_hybrid_smoke'


def dump(path, value):
    with path.open('x') as f: json.dump(value, f, indent=2)


def health(result):
    endpoints = result.get('endpoints', {})
    known = [k for k,v in endpoints.items() if k != 'exposure' and v['state'] in {'positive','negative'} and v['status']=='assessed']
    coverage = result.get('coverage', {})
    defects = bool(result.get('failed_stage') or result.get('rejected_findings') or coverage.get('validation_errors'))
    success = not defects and coverage.get('review_complete', False) and 'adverse_propagation' in known
    return {'semantic_success':success, 'semantic_status':'success' if success else 'failure' if defects or not known else 'inconclusive',
            'assessed_semantic_endpoints':known, 'independently_validated':False}


def jobs():
    result=[]
    for entry,trace,cleans,_ in select('smoke'):
        if len(cleans) != 3: raise ValueError('exactly three matched controls required')
        task=json.loads((ROOT/entry['fixture_path']).read_text())['task_prompt']
        packet=build_packet(trace,cleans,enabled=True,task_text=task)
        if not packet['admitted']: raise ValueError('unchanged admission requirements failed')
        result.append((trace.trace_id,packet))
    if len(result)!=2: raise ValueError('exactly AON and SPGI required')
    return result


def preview(items, config, cache):
    t=Transport(config,cache,progress=False)
    traces=[]
    for trace_id,packet in items:
        t.trace_id=trace_id
        result=evaluate(packet,Session(t),enabled=True)
        traces.append({'trace_id':trace_id,'packet_sha256':packet['packet_sha256'],
                       'cache_only_semantic_health':health(result)})
    # Stage B is conditional on actual Stage A output. Do not fabricate that output
    # to claim an exact plan. Reserve remaining calls at configured input/output caps.
    remaining=4-t.cache_hits
    return {'mode':'offline_preview', 'model':config.model,'configuration':asdict(config),
            'traces':traces,'cache_hits':t.cache_hits,'maximum_remaining_paid_calls':remaining,
            'maximum_paid_calls_this_run':config.max_calls,
            'conservative_cost_ceiling_at_configured_caps_usd':str(config.cost(config.max_input_tokens,config.max_output_tokens)*min(remaining,config.max_calls)),
            'stage_b_inputs':'known only after valid Stage A response; checked before sending',
            'token_accounting':'UTF-8 bytes+4096 conservative input bound; bytes/4 display estimate; no tokenizer API',
            'uncached_requests_known_now':[{k:r[k] for k in ('trace_id','stage','estimated_input_tokens','input_token_bound','max_output_tokens','reserved_usd')} for r in t.rows if r['status']=='planned'],
            'pricing':'configured estimates, no cached-token discount; not an invoice',
            'cache_only_attempts':t.rows}


def execute(items, config, cache, output, *, authorize=None, opener=None, key=None, plan=None):
    output.mkdir(parents=True,exist_ok=False)
    dump(output/'plan.json',plan if plan is not None else {'configuration':asdict(config)})
    paths=[Path(__file__),*(ROOT/'benchmark/mp_adjudicator').glob('*.py')]
    dump(output/'implementation_hashes.json',{str(x.relative_to(ROOT)):hashlib.sha256(x.read_bytes()).hexdigest() for x in paths})
    t=Transport(config,cache,output=output,authorize=authorize,opener=opener,key=key)
    decisions=[]
    for i,(trace_id,packet) in enumerate(items):
        t.trace_id=trace_id
        dump(output/f'packet_{i}.json',packet)
        result=evaluate(packet,Session(t),enabled=True)
        row={'trace_id':trace_id,'packet_file':f'packet_{i}.json','result':result,**health(result)}
        decisions.append(row)
        with (output/'decisions.jsonl').open('a') as f: f.write(json.dumps(row)+'\n')
        with (output/'event_findings.jsonl').open('a') as f:
            for finding in result.get('event_findings',[]):f.write(json.dumps({'trace_id':trace_id,**finding})+'\n')
        dump(output/f'validation_{i}.json',{'failed_stage':result.get('failed_stage'),'failure_reason':result.get('failure_reason'),
             'rejected_findings':result.get('rejected_findings',[]),'coverage':result.get('coverage'),**health(result)})
    request_failures=[r for r in t.rows if r['status']=='failure']
    transport_ok=not request_failures and t.completed+t.cache_hits==4
    semantic_ok=all(x['semantic_success'] for x in decisions)
    summary={'transport_status':'completed' if transport_ok else 'incomplete_or_failed',
             'semantic_success':semantic_ok,'status':'success' if transport_ok and semantic_ok else 'semantic_failure' if transport_ok else 'transport_incomplete',
             'paid_calls':t.calls,'completed_api_responses':t.completed,'cache_hits':t.cache_hits,
             'usage_cost_plus_unresolved_reservations_usd':str(t.spent),'traces':[{k:v for k,v in x.items() if k!='result'} for x in decisions],
             'independently_validated':False,'production_enabled':False}
    dump(output/'summary.json',summary)
    return summary


def main(argv=None):
    os.chdir(ROOT)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute',action='store_true')
    p.add_argument('--cache-only',action='store_true',help='never authorize paid calls, including cache misses')
    p.add_argument('--output',type=Path)
    p.add_argument('--cache',type=Path,default=BASE/'cache')
    p.add_argument('--max-calls',type=int,default=int(os.getenv('MP_HYBRID_MAX_CALLS','4')))
    p.add_argument('--max-input-tokens',type=int,default=int(os.getenv('MP_HYBRID_MAX_INPUT_TOKENS','100000')))
    p.add_argument('--max-output-tokens',type=int,default=int(os.getenv('MP_HYBRID_MAX_OUTPUT_TOKENS','6000')))
    p.add_argument('--budget-usd',default=os.getenv('MP_HYBRID_BUDGET_USD','.25'))
    a=p.parse_args(argv)
    config=Config(endpoint=os.getenv('MP_HYBRID_ENDPOINT','https://api.openai.com/v1'),
                  model=os.getenv('MP_HYBRID_MODEL',MODEL),max_calls=a.max_calls,
                  max_input_tokens=a.max_input_tokens,max_output_tokens=a.max_output_tokens,budget_usd=a.budget_usd,
                  timeout=int(os.getenv('MP_HYBRID_TIMEOUT','120')),
                  input_rate=os.getenv('MP_HYBRID_INPUT_USD_PER_MILLION','.40'),
                  output_rate=os.getenv('MP_HYBRID_OUTPUT_USD_PER_MILLION','1.60'))
    if config.model!=MODEL: p.error('smoke runner requires the approved immutable model snapshot')
    cache=a.cache.resolve();base=BASE.resolve()
    if not cache.is_relative_to(base) or cache==base: p.error('cache must be a dedicated subdirectory of analysis/mp_hybrid_smoke')
    output=a.output.resolve() if a.output else None
    if a.execute and (output is None or output.exists() or not output.is_relative_to(base) or output==base or output.is_relative_to(cache) or cache.is_relative_to(output)):
        p.error('execution requires a NEW output subdirectory, separate from cache, under analysis/mp_hybrid_smoke')
    items=jobs()
    plan=preview(items,config,cache)
    # Human-readable compact preflight, without dumping trace contents.
    print(json.dumps({k:v for k,v in plan.items() if k!='cache_only_attempts'},indent=2),flush=True)
    if not a.execute:return 0
    def confirm():
        if not sys.stdin.isatty(): return False
        text=f'PAY MP HYBRID {config.max_calls} {config.budget_usd}'
        return input(f'Type {text} to authorize at most {config.max_calls} calls within estimated ${config.budget_usd}: ')==text
    # No execution artifacts are overwritten. Stage-specific budgets are rechecked
    # immediately before each request; confirmation occurs at the first cache miss.
    summary=execute(items,config,cache,output,authorize=None if a.cache_only else confirm,plan=plan)
    print(json.dumps(summary,indent=2),flush=True)
    return 0 if summary['status']=='success' else 3 if summary['transport_status']=='completed' else 2


if __name__=='__main__':sys.exit(main())
