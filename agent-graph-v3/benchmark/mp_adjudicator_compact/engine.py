"""Codec bridge to the unchanged v2 evaluator; no HTTP or added model calls."""
from copy import deepcopy
import jsonschema
from benchmark.mp_adjudicator.contract import obj,arr,enum,S
from benchmark.mp_adjudicator_v2.engine import evaluate as evaluate_v2
from benchmark.mp_adjudicator_v2.packet import options
from .codec import encode,block,map_evidence
from . import VERSION

A_SCHEMA=obj({'events':arr(obj({'event_id':S,'review':enum('reviewed','unresolved','unsupported'),
    'kind':enum('assertion','rejection','forwarding','calculation','action','correction','other'),
    'summary':S,'evidence':arr(S)}))})


class Bridge:
    def __init__(self,packet,session):
        self.packet=packet;self.session=session;self.codec=encode(packet);self.audit=[];self.a=None

    def ask(self,stage,prompt,schema,payload):
        instruction=VERSION+' Select occurrence IDs, never text IDs. No quote generation or offset calculation. Missing event reviews remain unknown; shared text never establishes shared review. '
        if stage=='A':
            response=self.session.ask(stage,instruction+prompt,A_SCHEMA,self.codec['view'])
            jsonschema.validate(response,A_SCHEMA)
            events={}
            for row in response['events']:
                eid=row['event_id']
                if eid not in self.packet['cards'] or eid in events:raise ValueError('unknown_or_duplicate_event')
                mapped=[block(self.codec,self.packet,x) for x in row['evidence']]
                if any(self.packet['blocks'][bid]['event_id']!=eid for bid in mapped):raise ValueError('cross_event_evidence')
                events[eid]={k:v for k,v in row.items() if k!='event_id'};events[eid]['evidence']=mapped
            for eid in self.packet['cards']:
                events.setdefault(eid,{'review':'unresolved','kind':'other','summary':'No model review supplied.','evidence':[]})
            self.a={'events':events};return self.a
        opts=payload['attribution_options'];option_alias={key:f'p{i:x}' for i,key in enumerate(sorted(opts))}
        inverse={v:k for k,v in option_alias.items()}
        wire_schema=deepcopy(schema)
        def rewrite(x):
            if isinstance(x,dict):
                if x.get('pattern')=='^B[0-9a-f]{20}$':x['pattern']='^b[0-9a-f]+$'
                for v in x.values():rewrite(v)
            elif isinstance(x,list):
                for v in x:rewrite(v)
        rewrite(wire_schema)
        wire_schema['properties']['findings']['items']['properties']['option_id']=enum(*inverse) if inverse else enum('NO_VALID_SELECTION')
        # Stage A quote strings are deterministically re-expressed as the original
        # selected occurrence IDs, not re-extracted or inferred from equal text.
        verified={eid:{'kind':o['kind'],'summary':o['summary'],'evidence':[self.codec['reverse'][b] for b in self.a['events'][eid]['evidence']]}
                  for eid,o in payload['verified_behavior'].items()}
        wire={'version':VERSION,'evidence':self.codec['view'],'run_roles':payload['run_roles'],
              'verified_behavior':verified,'attribution_options':{option_alias[k]:v for k,v in opts.items()},
              'mutations':payload['mutations'],'deliveries':payload['deliveries'],
              'coverage_requirements':payload['coverage_requirements'],
              'review_status':{eid:o['review'] for eid,o in self.a['events'].items()}}
        response=self.session.ask(stage,instruction+prompt,wire_schema,wire)
        jsonschema.validate(response,wire_schema)
        mapped=map_evidence(response,lambda x:block(self.codec,self.packet,x))
        for f in mapped['findings']:f['option_id']=inverse[f['option_id']]
        return mapped


def evaluate(packet,session,*,enabled=False):
    bridge=Bridge(packet,session)
    result=evaluate_v2(packet,bridge,enabled=enabled)
    result.update(version=VERSION,model_audit=session.audit,compact_coverage=bridge.codec['coverage'],
                  occurrence_map=bridge.codec['lookup'],original_packet_sha256=packet['packet_sha256'])
    return result
