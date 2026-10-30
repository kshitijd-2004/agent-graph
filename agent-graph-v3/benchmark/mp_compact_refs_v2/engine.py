"""Opt-in bridge retaining the existing causal and endpoint verification kernel."""
import json
from copy import deepcopy
import jsonschema
from benchmark.mp_adjudicator_compact.engine import Bridge as OriginalBridge
from benchmark.mp_adjudicator_v2.engine import evaluate as evaluate_v2
from .contract import schema, INSTRUCTION
from .decode import decode
from . import VERSION


class Bridge(OriginalBridge):
    diagnostics = None

    def ask(self, stage, prompt, old_schema, payload):
        if stage != 'A':
            original_session = self.session
            occurrence_constraint = schema(self.packet, self.codec)['properties']['events']['items']['properties']['evidence']['items']
            class ConstrainedSession:
                def ask(self, stage, prompt, wire_schema, payload):
                    wire_schema = deepcopy(wire_schema)
                    def constrain(value):
                        if isinstance(value, dict):
                            if value.get('pattern') == '^b[0-9a-f]+$':
                                value.clear()
                                value['$ref'] = '#/$defs/occurrence'
                            for item in value.values():
                                constrain(item)
                        elif isinstance(value, list):
                            for item in value:
                                constrain(item)
                    constrain(wire_schema)
                    wire_schema.setdefault('$defs', {})['occurrence'] = occurrence_constraint
                    return original_session.ask(stage, prompt, wire_schema, payload)
            self.session = ConstrainedSession()
            try:
                return super().ask(stage, INSTRUCTION + prompt, old_schema, payload)
            finally:
                self.session = original_session
        try:
            response = self.session.ask(stage, INSTRUCTION + prompt, schema(self.packet, self.codec), self.codec['view'])
        except jsonschema.ValidationError:
            # Session preserves the raw response before schema validation. Diagnose
            # all rows, but never pass a schema-rejected response to Stage B.
            response = json.loads(self.session.audit[-1]['response']['content'])
            self.diagnostics = decode(self.packet, self.codec, response)
            self.diagnostics['schema_status'] = 'rejected'
            raise
        self.diagnostics = decode(self.packet, self.codec, response)
        self.diagnostics['schema_status'] = 'accepted'
        self.a = self.diagnostics['decoded']
        return self.a


def evaluate(packet, session, *, enabled=False):
    bridge = Bridge(packet, session)
    result = evaluate_v2(packet, bridge, enabled=enabled)
    reached = 'verified_observations' in result
    has_rows = bool(bridge.diagnostics and bridge.diagnostics['accepted_event_ids'])
    result.update(version=VERSION, reference_diagnostics=bridge.diagnostics,
                  observation_verification_status=('completed' if has_rows else 'not_run_no_valid_rows') if reached else 'not_run',
                  model_audit=session.audit)
    if not reached or not has_rows:
        result['observation_errors'] = None
    return result
