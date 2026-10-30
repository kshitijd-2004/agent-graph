"""Exact allowlists without a large combined enum vocabulary."""
from copy import deepcopy
import re
from benchmark.mp_adjudicator_compact.engine import A_SCHEMA
from . import VERSION

INSTRUCTION = (VERSION + ' Event IDs identify packet events, not row numbers. Return at most one row per event. '
    'Evidence must select event-bound occurrence IDs (b...), never shared text IDs (t...). '
    'Every selected occurrence must belong to the row event. Missing reviews remain unresolved. '
    'A fact summary is not evidence of comprehensive behavioral review. ')


def schema(packet, codec):
    result = deepcopy(A_SCHEMA)
    props = result['properties']['events']['items']['properties']
    # Anchored exact alternatives constrain membership without duplicating schemas
    # or combining event and occurrence enums into a provider-wide enum limit.
    for key, identifiers in [('event_id', packet['cards']), ('evidence', codec['lookup'])]:
        constraint = {'type': 'string', 'pattern': '^(?:' + '|'.join(re.escape(x) for x in sorted(identifiers)) + ')$'}
        if key == 'evidence':
            props[key]['items'] = constraint
        else:
            props[key] = constraint
    return result
