"""Transport completion is not semantic pilot success."""
from collections import Counter


def semantic_health(decisions):
    statuses=Counter();endpoints=Counter();total=0;covered=0
    for row in decisions:
        result=row['result']
        if 'extractions' in result:
            xs=[v['result'] for v in result['extractions'].values()]
        else:xs=[a['extraction'] for a in result.get('extraction_audit',[])]
        for x in xs:
            total+=1;covered+=bool(x['complete']);statuses[x['status']]+=1
        for name,ep in result['endpoints'].items():
            if name!='exposure':endpoints[ep['state']]+=1
    # Operational criterion only; not a scientific accuracy/coverage gate.
    # A successful smoke must at least evaluate each primary endpoint and must
    # not consist almost entirely of invalid extraction. Never changes labels.
    primary=bool(decisions) and all(r['result']['endpoints']['adverse_propagation']['state']!='unknown' for r in decisions)
    failed=statuses['evaluation_failed']+statuses['execution_failed']+statuses['schema_invalid']
    success=primary and total>0 and failed==0
    return {'semantic_status':'completed' if success else 'incomplete','semantic_success':success,
            'extraction_units':total,'complete_extraction_units':covered,'extraction_status_counts':dict(statuses),
            'behavioral_endpoint_states':dict(endpoints),'independent_validation':False}
