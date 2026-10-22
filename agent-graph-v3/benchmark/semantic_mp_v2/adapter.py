"""Pinned, quote-schema OpenAI-compatible adapter. Offline by default.

Not wired to the live pilot in this phase. Tests use injected openers only.
Responses are content-addressed by complete request identity; original v1 raw
responses are replayed through an explicit migration, never relabeled as v2.
"""
import hashlib
import json
import os
from pathlib import Path
import urllib.request
from .extraction import VERSION, PROMPT, SCHEMA


class Transport:
    def __init__(self,*,env=None,allow_live=False,opener=None):
        env=os.environ if env is None else env
        self.endpoint=env.get('MP_SEMANTIC_ENDPOINT','https://api.openai.com/v1').rstrip('/')
        self.model=env.get('MP_SEMANTIC_MODEL','gpt-4.1-mini-2025-04-14')
        self.temperature=float(env.get('MP_SEMANTIC_TEMPERATURE','0'))
        self.max_tokens=int(env.get('MP_SEMANTIC_MAX_TOKENS','4096'))
        self.timeout=float(env.get('MP_SEMANTIC_TIMEOUT','60'))
        self.key=env.get('MP_SEMANTIC_API_KEY',env.get('OPENAI_API_KEY',''))
        self.cache=Path(env.get('MP_SEMANTIC_V2_CACHE','/tmp/agentprop-semantic-v2-cache'))
        self.allow_live=allow_live;self.opener=opener or urllib.request.urlopen
        if self.model!='gpt-4.1-mini-2025-04-14' or self.temperature!=0 or self.max_tokens<=0:raise ValueError('invalid approved model configuration')
        if not self.endpoint.startswith('https://'):raise ValueError('HTTPS required')
        self.cache_hits=0;self.network_calls=0

    def __call__(self,request):
        if request!={'system':PROMPT,'schema':SCHEMA,'observable_text':request.get('observable_text'),
                     'model':self.model,'temperature':self.temperature}:raise ValueError('contract/pin mismatch')
        body={'model':self.model,'temperature':0,'max_tokens':self.max_tokens,
              'messages':[{'role':'system','content':PROMPT},{'role':'user','content':json.dumps({'observable_text':request['observable_text']})}],
              'response_format':{'type':'json_schema','json_schema':{'name':'observable_propositions_v2','strict':True,'schema':SCHEMA}}}
        identity={'endpoint':self.endpoint,'body':body,'extraction_version':VERSION}
        h=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest();path=self.cache/(h+'.json')
        if path.exists():
            saved=json.loads(path.read_text())
            if saved['request']!=identity or saved['input_sha256']!=h:raise ValueError('cache identity mismatch')
            raw=saved['response'];self.cache_hits+=1
        else:
            if not self.allow_live:raise RuntimeError('live API disabled; no v2 cache response')
            if not self.key:raise RuntimeError('credentials unavailable')
            req=urllib.request.Request(self.endpoint+'/chat/completions',data=json.dumps(body).encode(),
                headers={'Content-Type':'application/json','Authorization':'Bearer '+self.key},method='POST')
            self.network_calls+=1
            try:
                with self.opener(req,timeout=self.timeout) as response:raw=json.load(response)
            except Exception as exc:raise RuntimeError('semantic transport failure: '+type(exc).__name__) from None
            self.cache.mkdir(parents=True,exist_ok=True)
            # Save even invalid/refused outputs for reproduction, without credentials.
            with path.open('x') as f:json.dump({'input_sha256':h,'request':identity,'response':raw},f,indent=2)
        if raw.get('model')!=self.model:raise ValueError('model pin mismatch')
        choice=raw['choices'][0]
        if choice.get('finish_reason')!='stop' or choice['message'].get('refusal'):raise ValueError('refused or truncated response')
        return {'model':raw['model'],'content':choice['message']['content'],'usage':raw.get('usage'),
                'input_sha256':h,'response_id':raw.get('id'),'system_fingerprint':raw.get('system_fingerprint')}
