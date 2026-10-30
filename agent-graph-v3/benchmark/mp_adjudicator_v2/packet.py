"""Immutable event-bound evidence; original reconstruction remains unchanged."""
from hashlib import sha256
from benchmark.mp_adjudicator.packet import build_packet as original_packet, hash_json, eligible_deliveries
from . import VERSION

BEHAVIOR = {'tool_call','memory_write','agent_handoff','final_response','reasoning','llm_output'}


def bind(base):
    blocks={};cards={}
    for eid,e in base['events'].items():
        if not e['selected']:continue
        text=base['contents'][e['content_id']]
        # This field is the immutable, emitted-content projection in the base
        # packet, not an invented claim about a raw JSON field.
        field='observable_text'
        digest=sha256(text.encode()).hexdigest()
        spans=[(0,len(text))]
        offset=0
        for line in text.splitlines(keepends=True):
            end=offset+len(line)
            if line.strip() and (offset,end) not in spans:spans.append((offset,end))
            offset=end
        ids=[]
        for start,end in spans:
            if start==end:continue
            bid='B'+hash_json([e['run'],eid,field,start,end,digest])[:20]
            if bid in blocks:raise ValueError('block_identity_collision')
            blocks[bid]={'run':e['run'],'event_id':eid,'field':field,'start':start,'end':end,
                         'source_sha256':digest,'text':text[start:end]}
            ids.append(bid)
        cards[eid]={'run':e['run'],'event_id':eid,'event_type':e['type'],'role':e['role'],
                    'tool':e['tool'],'source_path':e['source_path'],'evidence_block_ids':ids,
                    'behavior_candidate':e['type'] in BEHAVIOR}
    p={'version':VERSION,'base':base,'blocks':blocks,'cards':cards}
    p['packet_sha256']=hash_json(p)
    return p


def build_packet(trace,cleans,*,enabled=False,task_text=''):
    return bind(original_packet(trace,cleans,enabled=enabled,task_text=task_text))


def resolve(packet,bid):
    b=packet['blocks'][bid];base=packet['base'];e=base['events'][b['event_id']]
    text=base['contents'][e['content_id']]
    expected='B'+hash_json([e['run'],b['event_id'],'observable_text',b['start'],b['end'],sha256(text.encode()).hexdigest()])[:20]
    if bid!=expected or b['run']!=e['run'] or b['field']!='observable_text' or not 0<=b['start']<b['end']<=len(text) or text[b['start']:b['end']]!=b['text'] or b['source_sha256']!=sha256(text.encode()).hexdigest():
        raise ValueError('invalid_block_binding')
    return {'event_id':b['event_id'],'quote':b['text']}


def stage_a_view(packet):
    # No treatment, origins, clean labels, expected outcomes or trace filenames.
    return {'version':VERSION,'task':packet['base']['task_text'],'events':[
        {**{k:v for k,v in card.items() if k!='evidence_block_ids'},
         'source_text':packet['base']['contents'][packet['base']['events'][card['event_id']]['content_id']],
         'evidence_blocks':{bid:{k:packet['blocks'][bid][k] for k in ('field','start','end')}
                            for bid in card['evidence_block_ids']}}
        for card in packet['cards'].values()]}


def options(packet,observations):
    base=packet['base'];out={}
    for eid,o in observations.items():
        if base['events'][eid]['run']!=base['target_run']:continue
        for d in eligible_deliveries(base,eid):
            value={'event_id':eid,'origin_id':d['origin_id'],'version_id':d['version_id'],
                   'delivery_id':d['delivery_id'],'record_id':d['record_id'],
                   'retrieval_id':d['retrieval_id'],'context_ids':d['context_ids']}
            out['T'+hash_json(value)]=value
    return out


def obligations(packet):
    base=packet['base'];behavior=[k for k,c in packet['cards'].items() if c['behavior_candidate']]
    # No lexical or ancestry-only exclusion: all observable behavior in all runs
    # remains required for trace-wide negative endpoints, including pre-exposure.
    finals=[k for k in behavior if base['events'][k]['type']=='final_response']
    sources=[k for k in packet['cards'] if k in base['source_ids']]
    return {ep:(sorted(set(finals+sources)) if ep=='final_answer_impact' else behavior)
            for ep in ('adoption','forwarding','rejection','adverse_propagation','final_answer_impact')}
