"""Bounded MP development pilot. Default is offline preflight; no evaluator changes."""
import argparse
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sys
import urllib.request
from decimal import Decimal

from benchmark.semantic_mp.adapter import OpenAICompatibleTransport, SCHEMA
from benchmark.semantic_mp.extraction import VERSION, PinnedModelExtractor, SemanticExtractor
from benchmark.semantic_mp.evaluator import evaluate_trace
from benchmark.semantic_mp_v2.reporting import semantic_health
from scripts.validate_mp_retrospective import load, APPROVED_SAMPLE_SHA256

ROOT = Path(__file__).resolve().parents[1]
MODEL = 'gpt-4.1-mini-2025-04-14'
TERMS = ['270_aon_2015', '082_spgi_2017', '165_hst_2018', '045_212_488', '061_matplotlib']


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def identity(t, request):
    body = dict(model=t.model, temperature=t.temperature, max_tokens=t.max_tokens,
        messages=[dict(role='system', content=request['system']),
                  dict(role='user', content=json.dumps({'observable_text': request['text']}))],
        response_format={'type':'json_schema','json_schema':{'name':'observable_propositions','strict':True,'schema':SCHEMA}})
    obj = dict(endpoint=t.endpoint, body=body, extraction_version=VERSION)
    return obj, hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def cost(prompt, completion, cached=0):
    return (Decimal(prompt-cached)*Decimal('.40') + Decimal(cached)*Decimal('.10') + Decimal(completion)*Decimal('1.60')) / 1000000


def reservation(body):
    # UTF-8 bytes upper-bound byte-level tokenization; extra framing/schema allowance.
    return cost(len(json.dumps(body).encode()) + 4096, body['max_tokens'])


class StopPilot(Exception):
    pass


class Meter:
    def __init__(self, output, budget, max_calls, opener=None):
        self.output, self.budget, self.max_calls = output, Decimal(budget), max_calls
        self.spent = Decimal(0)
        self.calls = 0
        self.opener = opener or urllib.request.urlopen
        self.rows = []

    def log(self, row):
        self.rows.append(row)
        with (self.output/'requests.jsonl').open('a') as f:
            f.write(json.dumps(row)+'\n'); f.flush(); os.fsync(f.fileno())

    def __call__(self, req, **kwargs):
        body = json.loads(req.data)
        reserve = reservation(body)
        if self.calls >= self.max_calls or self.spent + reserve > self.budget:
            raise StopPilot('call or dollar reservation limit reached')
        self.calls += 1
        self.spent += reserve
        self.log(dict(status='reserved', call=self.calls, reserve_usd=str(reserve), body=body))
        try:
            with self.opener(req, **kwargs) as r:
                raw = r.read()
            response = json.loads(raw)
            usage = response.get('usage', {})
            p, c = usage.get('prompt_tokens'), usage.get('completion_tokens')
            cached = usage.get('prompt_tokens_details', {}).get('cached_tokens', 0)
            if not all(isinstance(x,int) and x>=0 for x in (p,c,cached)) or cached>p:
                raise ValueError('missing or invalid usage; full reservation retained')
            charge = cost(p,c,cached)
            self.spent += charge-reserve
            self.log(dict(status='response', call=self.calls, usage=usage,
                          usage_based_cost_usd=str(charge), response=response))
            if charge>reserve:
                raise ValueError('provider usage exceeded conservative reservation; stop')
            return io.BytesIO(raw)
        except Exception as exc:
            self.log(dict(status='failure', call=self.calls, error_type=type(exc).__name__,
                          budget_accounted_usd=str(self.spent)))
            raise


def select(stage):
    path = ROOT/'analysis/mp_retrospective_v1/sample.json'
    if sha(path)!=APPROVED_SAMPLE_SHA256: raise ValueError('frozen sample hash mismatch')
    m=json.loads(path.read_text())
    entries=[next(e for e in m['entries'] if term in e['path'] and e['legacy']) for term in TERMS[:2 if stage=='smoke' else 5]]
    jobs=[]
    for e in entries:
        paths=[e['path'], *e['clean_paths']]
        for p in paths:
            if sha(ROOT/p)!=m['input_sha256'][p]:raise ValueError('frozen trace changed: '+p)
        trace=load(ROOT/e['path']); cleans=[load(ROOT/p) for p in e['clean_paths']]
        # Independently observed clean source documents only; no attack manifest/answer labels.
        texts=[]
        for c in cleans:
            for ev in c.events:
                if ev.event_type.value=='tool_result' and ev.tool_name=='read_text_file' and ev.output_text and ev.output_text not in texts:
                    texts.append(ev.output_text)
        jobs.append((e,trace,cleans,'\n\n'.join(texts)))
    return jobs


def run_jobs(jobs, extractor):
    for e,t,c,source in jobs:
        yield dict(trace_id=t.trace_id, trace_path=e['path'], clean_paths=e['clean_paths'],
            authoritative_source=source,
            result=evaluate_trace(t,c,extractor=extractor,authoritative_text=source,enabled=True))


def main():
    os.chdir(ROOT)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage', choices=['smoke','pilot'])
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--budget-usd',default='1.00')
    p.add_argument('--max-calls',type=int,default=200)
    p.add_argument('--execute',action='store_true')
    p.add_argument('--smoke-summary',type=Path)
    a=p.parse_args()
    if not Decimal(a.budget_usd).is_finite() or Decimal(a.budget_usd)<=0 or not 1<=a.max_calls<=500:
        p.error('positive finite dollar budget and 1..500 calls required')
    # A dedicated namespace prevents accidental writes into old results/traces.
    base=(ROOT/'analysis/mp_semantic_pilot').resolve()
    output=a.output.resolve()
    if not output.is_relative_to(base) or output==base or output.exists():
        p.error('use a NEW subdirectory of analysis/mp_semantic_pilot')
    t=OpenAICompatibleTransport()
    if t.model!=MODEL or t.endpoint!='https://api.openai.com/v1' or not 1<=t.max_tokens<=16384:
        p.error('pilot requires approved OpenAI endpoint, snapshot and 1..16384 max tokens')
    for protected in ('production_run','preliminary_analysis_oct16','memory_poisoning_delivery_audit_oct16','analysis/mp_retrospective_v1','analysis/semantic_mp_dev1'):
        if t.cache.resolve().is_relative_to((ROOT/protected).resolve()):
            p.error('cache must not write into protected inputs or previous results')
    jobs=select(a.stage)
    requests={}
    def collect(request):
        obj,h=identity(t,request); requests[h]=(request,obj)
        return dict(model=t.model,revision=t.model,content='{"propositions":[],"complete":false}')
    list(run_jobs(jobs,SemanticExtractor(PinnedModelExtractor(t.pin,collect))))
    misses=[]; hits=[]
    for h,(request,obj) in requests.items():
        cache=t.cache/(h+'.json')
        if cache.exists():
            t(request)  # validate cache identity and response offline
            hits.append(h)
        else:misses.append(h)
    tokens=sum(math.ceil(len(json.dumps(requests[h][1]['body']).encode())/4) for h in misses)
    estimate=dict(stage=a.stage,traces=len(jobs),unique_model_requests=len(requests),disk_cache_hits=len(hits),
        paid_model_calls=len(misses),estimated_input_tokens=tokens,
        approximate_cost_with_max_output_usd=str(cost(tokens,len(misses)*t.max_tokens)),
        conservative_reserved_cost_usd=str(sum((reservation(requests[h][1]['body']) for h in misses),Decimal(0))),
        budget_usd=a.budget_usd,hard_max_calls=a.max_calls,model=t.model,max_output_tokens=t.max_tokens,
        token_estimator='UTF-8 bytes/4 estimate; byte count +4096 framing for reservation',
        rates_per_million=dict(input='.40',cached_input='.10',output='1.60'),
        cache=str(t.cache),input_hashes={p:sha(ROOT/p) for e,_,_,_ in jobs for p in [e['path'],*e['clean_paths']]},
        code_hashes={str(f.relative_to(ROOT)):sha(f) for f in [Path(__file__),* (ROOT/'benchmark/semantic_mp').glob('*.py')]})
    print(json.dumps(estimate,indent=2))
    if not a.execute:return
    if a.stage=='pilot':
        if not a.smoke_summary:
            p.error('pilot execution requires --smoke-summary')
        smoke_summary=json.loads(a.smoke_summary.read_text())
        smoke_decisions=a.smoke_summary.parent/'decisions.jsonl'
        if not smoke_decisions.is_file():p.error('missing smoke decisions')
        health=semantic_health([json.loads(line) for line in smoke_decisions.read_text().splitlines()])
        if smoke_summary.get('status')!='completed' or not health['semantic_success']:
            p.error('smoke transport/semantic evaluation incomplete; pilot execution blocked')
    if misses:
        if not t.key:p.error('set OPENAI_API_KEY or MP_SEMANTIC_API_KEY')
        if not sys.stdin.isatty():p.error('paid calls require interactive terminal confirmation')
        if input(f'Type PAY {a.stage} to authorize up to ${a.budget_usd} and {a.max_calls} calls: ')!=f'PAY {a.stage}':
            print('Cancelled; no calls made.');return
    output.mkdir(parents=True)
    (output/'plan.json').write_text(json.dumps(estimate,indent=2))
    (output/'semantic_inputs.json').write_text(json.dumps(requests,indent=2))
    meter=Meter(output,a.budget_usd,a.max_calls)
    t.opener=meter;t.allow_live=True
    failure=None
    for h,(request,obj) in requests.items():
        try:
            was_cached=(t.cache/(h+'.json')).exists()
            response=t(request)
            meter.log(dict(status='cache_hit' if was_cached else 'completed',input_sha256=h,response=response,
                           cached_record=json.loads((t.cache/(h+'.json')).read_text())))
        except Exception as exc:
            failure=type(exc).__name__+': '+str(exc)
            meter.log(dict(status='stopped',input_sha256=h,reason=failure));break
    # Never spend during decision generation. Missing responses stay explicit failures/unknown.
    t.allow_live=False
    decisions=list(run_jobs(jobs,SemanticExtractor(t.extractor())))
    with (output/'decisions.jsonl').open('x') as f:
        for row in decisions:f.write(json.dumps(row)+'\n')
    health=semantic_health(decisions)
    summary=dict(status='stopped' if failure else 'completed' if health['semantic_success'] else 'semantic_incomplete',
                 transport_status='failed' if failure else 'completed',**health,failure=failure,paid_calls=meter.calls,
                 usage_cost_plus_unresolved_reservations_usd=str(meter.spent),
                 cache_hits=sum(r['status']=='cache_hit' for r in meter.rows),traces=len(decisions))
    (output/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))
    if failure:sys.exit(2)
    if not health['semantic_success']:sys.exit(3)


if __name__=='__main__':main()
