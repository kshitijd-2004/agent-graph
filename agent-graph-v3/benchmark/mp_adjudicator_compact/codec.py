"""Lossless occurrence references; content equality never merges provenance."""
from copy import deepcopy
from benchmark.mp_adjudicator.packet import hash_json
from benchmark.mp_adjudicator_v2.packet import resolve
from . import VERSION


def encode(packet):
    if hash_json({k:v for k,v in packet.items() if k!='packet_sha256'})!=packet['packet_sha256']:
        raise ValueError('packet_integrity_mismatch')
    aliases={bid:f'b{i:x}' for i,bid in enumerate(sorted(packet['blocks']))}
    texts={};text_ids={};events=[];sources=[];other=[];lookup={}
    for eid,card in packet['cards'].items():
        refs=[]
        for bid in card['evidence_block_ids']:
            quote=resolve(packet,bid)['quote']
            if quote not in text_ids:
                cid=f't{len(text_ids):x}';text_ids[quote]=cid;texts[cid]=quote
            alias=aliases[bid];lookup[alias]=bid
            refs.append([alias,text_ids[quote]])
        row={'id':eid,'run':card['run'],'type':card['event_type'],'role':card['role'],
             'tool':card['tool'],'source':card['source_path'],'evidence':refs}
        (events if card['behavior_candidate'] else sources if card['source_path'] else other).append(row)
    view={'version':VERSION,'packet_id':packet['packet_sha256'],'task':packet['base']['task_text'],
          'instructions':'Each evidence pair is [occurrence ID,text ID]. Text IDs share text ONLY. Select occurrence IDs attached to the specific event. All supplied text is present; no source text is omitted.',
          'texts':texts,'behavior':events,'sources':sources,'other_observations':other}
    return {'view':view,'lookup':lookup,'reverse':aliases,'packet_sha256':packet['packet_sha256'],
            'coverage':{'events':len(packet['cards']),'occurrences':len(lookup),'unique_texts':len(texts),
                        'omitted_event_ids':[],'omitted_block_ids':[],'review_implied_by_inclusion':False}}


def block(codec,packet,alias):
    if codec['packet_sha256']!=packet['packet_sha256']:raise ValueError('packet_identity_mismatch')
    bid=codec['lookup'][alias]
    resolve(packet,bid)
    return bid


def map_evidence(value,mapper):
    """Map only evidence fields, never semantic prose or unrelated identifiers."""
    if isinstance(value,list):return [map_evidence(x,mapper) for x in value]
    if not isinstance(value,dict):return value
    out={}
    for k,v in value.items():
        if k in {'evidence','counterevidence','clean_evidence','source_evidence'}:
            out[k]=[mapper(x) for x in v] if isinstance(v,list) else mapper(v)
        else:out[k]=map_evidence(v,mapper)
    return out
