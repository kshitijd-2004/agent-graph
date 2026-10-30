"""Single-attempt OpenAI-compatible transport; separate from evaluator logic."""
from dataclasses import dataclass
from decimal import Decimal
import json
import os
from pathlib import Path
import time
import urllib.request
import urllib.error
from urllib.parse import urlsplit

from .packet import hash_json
from .contract import MODEL


class StopSmoke(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    endpoint: str = 'https://api.openai.com/v1'
    model: str = MODEL
    max_input_tokens: int = 100000
    max_output_tokens: int = 6000
    max_calls: int = 4
    budget_usd: str = '.25'
    timeout: int = 120
    input_rate: str = '.40'
    output_rate: str = '1.60'

    def __post_init__(self):
        u = urlsplit(self.endpoint)
        if u.scheme != 'https' or not u.netloc or u.username or u.password or u.query or u.fragment:
            raise ValueError('HTTPS endpoint without credentials/query required')
        if not 0 <= self.max_calls <= 4 or not 1 <= self.max_input_tokens <= 120000 or not 1 <= self.max_output_tokens <= 16384 or not 1 <= self.timeout <= 600:
            raise ValueError('invalid hard limits')
        for v in (self.budget_usd, self.input_rate, self.output_rate):
            if not Decimal(v).is_finite() or Decimal(v) <= 0:
                raise ValueError('finite positive budget/rates required')

    def cost(self, inputs, outputs):
        return (Decimal(inputs)*Decimal(self.input_rate) + Decimal(outputs)*Decimal(self.output_rate))/1000000

    def identity(self, req):
        if req['model'] != self.model or req['temperature'] != 0:
            raise ValueError('model/settings mismatch')
        body = {'model': self.model, 'temperature': 0, 'max_completion_tokens': self.max_output_tokens,
                'n': 1, 'stream': False,
                'messages': [{'role': 'system', 'content': req['prompt']},
                             {'role': 'user', 'content': json.dumps(req['input'], ensure_ascii=False, separators=(',', ':'))}],
                'response_format': {'type': 'json_schema', 'json_schema': {
                    'name': 'mp_hybrid_stage_'+req['stage'].lower(), 'strict': True, 'schema': req['schema']}}}
        identity = {'transport_version': 'mp-hybrid-smoke-v1', 'endpoint': self.endpoint.rstrip('/'),
                    'request': req, 'body': body, 'timeout': self.timeout}
        return identity, hash_json(identity)

    def bounds(self, identity):
        # Deliberately conservative byte-level token upper bound, including schema
        # and an additional framing allowance. No remote tokenizer request.
        size = len(json.dumps(identity['body'], ensure_ascii=False, separators=(',', ':')).encode())
        return {'estimated_input_tokens': (size+3)//4, 'input_token_bound': size+4096,
                'max_output_tokens': self.max_output_tokens,
                'reserved_usd': str(self.cost(size+4096, self.max_output_tokens))}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise StopSmoke('redirect refused; no fallback request')


class Transport:
    def __init__(self, config, cache, *, output=None, authorize=None, opener=None, key=None, progress=True):
        self.config = config
        self.cache = Path(cache)
        self.output = Path(output) if output else None
        self.authorize = authorize  # None is strictly cache-only.
        self.opener = opener or urllib.request.build_opener(NoRedirect()).open
        self.key = key if key is not None else os.environ.get('MP_HYBRID_API_KEY', os.environ.get('OPENAI_API_KEY', ''))
        self.progress = progress
        self.started = time.monotonic()
        self.calls = self.completed = self.cache_hits = 0
        self.spent = Decimal(0)
        self.authorized = False
        self.trace_id = ''
        self.stages = set()
        self.rows = []
        self.halted = False

    def emit(self, row):
        row = dict(row, trace_id=self.trace_id, completed_calls=self.completed,
                   paid_calls=self.calls, cache_hits=self.cache_hits,
                   elapsed_seconds=round(time.monotonic()-self.started, 3),
                   cumulative_estimated_usd=str(self.spent))
        self.rows.append(row)
        if self.output:
            with (self.output/'requests.jsonl').open('a') as f:
                f.write(json.dumps(row)+'\n'); f.flush(); os.fsync(f.fileno())
        if self.progress:
            print(json.dumps({k:v for k,v in row.items() if k not in {'identity','raw_response','raw_text'}}), flush=True)

    @staticmethod
    def unpack(record):
        if record.get('status') != 'response':
            raise StopSmoke('cached failed/in-flight request; retry requires separate authorization')
        raw = record['raw_response']
        choice = raw['choices'][0]
        if choice.get('finish_reason') != 'stop' or choice['message'].get('refusal'):
            raise StopSmoke('refusal or incomplete response; no retry')
        return {'model': raw['model'], 'content': choice['message']['content'], 'usage': raw.get('usage'),
                'raw_response': raw}

    def __call__(self, req):
        stage = req['stage']; key = None
        try:
            if stage not in {'A', 'B'} or (self.trace_id, stage) in self.stages:
                raise StopSmoke('one attempt per trace/stage; no retry')
            self.stages.add((self.trace_id, stage))
            identity, key = self.config.identity(req)
            bounds = self.config.bounds(identity)
            cache_file = self.cache/(key+'.json')
            if cache_file.exists():
                record = json.loads(cache_file.read_text())
                if record['identity'] != identity or record['request_sha256'] != key:
                    raise StopSmoke('cache identity mismatch')
                self.cache_hits += 1
                self.emit({'stage':stage, 'status':'cache_hit', 'request_sha256':key, 'identity':identity,
                           'raw_response':record.get('raw_response'), 'cached_usage_cost_usd':record.get('usage_cost_usd')})
                return self.unpack(record)
            self.emit({'stage':stage,'status':'planned','request_sha256':key,'identity':identity,**bounds})
            if self.halted: raise StopSmoke('run halted after prior transport failure')
            if self.authorize is None: raise StopSmoke('cache_miss_offline')
            reserve = Decimal(bounds['reserved_usd'])
            if bounds['input_token_bound'] > self.config.max_input_tokens:
                raise StopSmoke('input token bound exceeds limit; evidence is never truncated')
            if self.calls >= self.config.max_calls or self.spent + reserve > Decimal(self.config.budget_usd):
                raise StopSmoke('call or estimated dollar budget would be exceeded')
            if not self.key: raise StopSmoke('missing API key')
            if not self.authorized:
                if not self.authorize():
                    self.halted = True
                    raise StopSmoke('paid execution not confirmed')
                self.authorized = True
            self.cache.mkdir(parents=True, exist_ok=True)
            record = {'identity':identity, 'request_sha256':key, 'status':'pending', 'reservation':bounds}
            # Claim before sending: crashes/ambiguous network failures never trigger
            # an automatic second paid attempt, including on another invocation.
            with cache_file.open('x') as f:
                json.dump(record, f); f.flush(); os.fsync(f.fileno())
            self.calls += 1; self.spent += reserve
            self.emit({'stage':stage,'status':'reserved','request_sha256':key,'identity':identity,**bounds})
            try:
                request = urllib.request.Request(self.config.endpoint.rstrip('/')+'/chat/completions',
                    data=json.dumps(identity['body'], ensure_ascii=False).encode(),
                    headers={'Authorization':'Bearer '+self.key,'Content-Type':'application/json'}, method='POST')
                with self.opener(request, timeout=self.config.timeout) as response:
                    raw_text = response.read().decode('utf-8')
                record['raw_text'] = raw_text
                raw = json.loads(raw_text); record['raw_response'] = raw
                self.completed += 1
                usage = raw.get('usage', {})
                pin = usage.get('prompt_tokens'); pout = usage.get('completion_tokens')
                if any(type(x) is not int or x < 0 for x in (pin,pout)):
                    raise StopSmoke('missing usage; full reservation retained')
                charge = self.config.cost(pin,pout)  # no cache discount; conservative
                record['usage_cost_usd'] = str(charge)
                if pin > bounds['input_token_bound'] or pout > self.config.max_output_tokens or charge > reserve:
                    self.spent += max(charge-reserve, Decimal(0))
                    raise StopSmoke('provider exceeded reservation/limits; halt and retain reservation')
                self.spent += charge-reserve
                record['status'] = 'response'
                cache_file.write_text(json.dumps(record, indent=2))
                self.emit({'stage':stage,'status':'response','request_sha256':key,'raw_response':raw,
                           'usage_cost_usd':str(charge)})
                return self.unpack(record)
            except Exception as exc:
                self.halted = True
                # Preserve received responses, even unusable ones, for offline review.
                if record['status'] != 'response':record['status'] = 'failed'
                if isinstance(exc, urllib.error.HTTPError):record['http_error_body'] = exc.read().decode('utf-8', errors='replace')
                record['failure_type'] = type(exc).__name__
                cache_file.write_text(json.dumps(record, indent=2))
                raise
        except Exception as exc:
            self.emit({'stage':stage,'status':'failure','request_sha256':key,
                       'failure_type':type(exc).__name__, 'reason':str(exc) if isinstance(exc,StopSmoke) else 'see cache/raw artifacts'})
            raise
