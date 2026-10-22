"""OpenAI-compatible structured JSON transport. No live call without opt-in."""
import hashlib
import json
import os
from pathlib import Path
import urllib.request
from .extraction import ModelPin, PROMPT, PinnedModelExtractor, VERSION

FIELDS = ['subject','predicate','value','unit','scope','stance','kind','start','end','quote']
SCHEMA = {'type':'object','additionalProperties':False,'required':['propositions','complete'],
          'properties':{'complete':{'type':'boolean'},'propositions':{'type':'array','items':{
              'type':'object','additionalProperties':False,'required':FIELDS,
              'properties':{k:({'type':'integer'} if k in {'start','end'} else {'type':'string'}) for k in FIELDS}}}}}
SCHEMA['properties']['propositions']['items']['properties']['stance']['enum']=['asserted','rejected','quoted','hypothetical','uncertain']
SCHEMA['properties']['propositions']['items']['properties']['kind']['enum']=['fact','action']


class OpenAICompatibleTransport:
    def __init__(self, *, allow_live=False, env=None, opener=None):
        env=os.environ if env is None else env
        self.endpoint=env.get('MP_SEMANTIC_ENDPOINT','https://api.openai.com/v1').rstrip('/')
        self.model=env.get('MP_SEMANTIC_MODEL','gpt-4.1-mini-2025-04-14')
        self.temperature=float(env.get('MP_SEMANTIC_TEMPERATURE','0'))
        self.max_tokens=int(env.get('MP_SEMANTIC_MAX_TOKENS','4096'))
        self.timeout=float(env.get('MP_SEMANTIC_TIMEOUT','60'))
        self.key=env.get('MP_SEMANTIC_API_KEY',env.get('OPENAI_API_KEY',''))
        self.cache=Path(env.get('MP_SEMANTIC_CACHE','/tmp/agentprop-semantic-cache'))
        self.allow_live=allow_live
        self.opener=opener or urllib.request.urlopen
        self.pin=ModelPin(self.model,self.model,hashlib.sha256(PROMPT.encode()).hexdigest(),self.temperature)
        self.pin.check()
        if not self.endpoint.startswith(('https://','http://localhost','http://127.0.0.1')):
            raise ValueError('HTTPS required for remote endpoints')

    def __call__(self, request):
        if request['pin'] != self.pin.__dict__:
            raise ValueError('transport pin mismatch')
        body={'model':self.model,'temperature':self.temperature,'max_tokens':self.max_tokens,
              'messages':[{'role':'system','content':request['system']},
                          {'role':'user','content':json.dumps({'observable_text':request['text']})}],
              'response_format':{'type':'json_schema','json_schema':{'name':'observable_propositions','strict':True,'schema':SCHEMA}}}
        identity={'endpoint':self.endpoint,'body':body,'extraction_version':VERSION}
        digest=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
        path=self.cache/(digest+'.json')
        if path.exists():
            saved=json.loads(path.read_text())
            if saved['input_sha256']!=digest or saved['request']!=identity:
                raise ValueError('cache request mismatch')
            raw=saved['response']
        else:
            if not self.allow_live:raise RuntimeError('live semantic API calls disabled; cache miss')
            if not self.key:raise RuntimeError('semantic API credentials unavailable')
            req=urllib.request.Request(self.endpoint+'/chat/completions',data=json.dumps(body).encode(),
                   headers={'Content-Type':'application/json','Authorization':'Bearer '+self.key},method='POST')
            # Do not propagate request/header-bearing transport exceptions.
            try:
                with self.opener(req,timeout=self.timeout) as response:raw=json.load(response)
            except Exception as exc:
                raise RuntimeError('semantic transport failed: '+type(exc).__name__) from None
            self._content(raw)
            self.cache.mkdir(parents=True,exist_ok=True)
            with path.open('x') as f:
                json.dump({'input_sha256':digest,'request':identity,'response':raw},f,indent=2)
        content=self._content(raw)
        return {'model':raw['model'],'revision':raw['model'],'content':content,
                'input_sha256':digest,'response_id':raw.get('id'),'system_fingerprint':raw.get('system_fingerprint')}

    def _content(self,raw):
        if raw.get('model')!=self.model:raise ValueError('response model does not match exact configured snapshot')
        choice=raw['choices'][0]
        if choice.get('finish_reason')!='stop' or choice['message'].get('refusal'):
            raise ValueError('refused or incomplete structured response')
        content=choice['message']['content']
        json.loads(content)
        return content

    def extractor(self):
        return PinnedModelExtractor(self.pin,self)
