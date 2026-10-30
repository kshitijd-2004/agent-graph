"""Approved compact-runner limits and implementation-bound cache identity."""
from dataclasses import dataclass,asdict
from decimal import Decimal
import hashlib
from pathlib import Path
from benchmark.mp_adjudicator.smoke_transport import Config as OriginalConfig, Transport as OriginalTransport
from benchmark.mp_adjudicator.packet import hash_json
ROOT=Path(__file__).resolve().parents[2]


def implementation_hashes():
    paths=[ROOT/'scripts/smoke_mp_compact.py',ROOT/'benchmark/memory_delivery_audit.py',
           ROOT/'benchmark/semantic_mp/evaluator.py',ROOT/'benchmark/semantic_mp_v2/extraction.py',
           ROOT/'benchmark/behavioral_anomaly.py',ROOT/'generation/admission.py']
    for directory in ('mp_adjudicator','mp_adjudicator_v2','mp_adjudicator_compact','mp_compact_smoke'):
        paths.extend((ROOT/'benchmark'/directory).glob('*.py'))
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


@dataclass(frozen=True)
class Config(OriginalConfig):
    max_input_tokens: int = 160000
    max_output_tokens: int = 16384
    budget_usd: str = '0.40'

    def __post_init__(self):
        values=asdict(self);values['max_input_tokens']=min(self.max_input_tokens,120000)
        OriginalConfig(**values)  # Retain all existing endpoint/rate/output checks.
        if not 1<=self.max_input_tokens<=160000 or Decimal(self.budget_usd)>Decimal('.40'):
            raise ValueError('approved compact limits: at most 160000 input units and $0.40')

    def identity(self,req):
        identity,_=super().identity(req)
        identity.update(transport_version='compact-mp-smoke-dev1',implementation_sha256=hash_json(implementation_hashes()))
        return identity,hash_json(identity)

    def bounds(self,identity):
        # Measure the exact serialization used by the inherited HTTP transport,
        # including its whitespace. Never estimate Stage B from the Stage A size.
        import json
        size=len(json.dumps(identity['body'],ensure_ascii=False).encode())
        return {'estimated_input_tokens':(size+3)//4,'input_token_bound':size+4096,
                'serialized_request_bytes':size,'max_output_tokens':self.max_output_tokens,
                'reserved_usd':str(self.cost(size+4096,self.max_output_tokens))}


class Transport(OriginalTransport):
    def __call__(self,req):
        try:return super().__call__(req)
        except Exception:
            # A rejected actual request stops any further paid execution in this
            # invocation. Cached evidence remains available for offline review.
            self.halted=True
            raise
