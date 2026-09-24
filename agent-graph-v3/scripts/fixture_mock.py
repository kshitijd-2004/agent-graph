"""Deterministic production-runner exercise for fixture acceptance/regression."""
import contextlib
from copy import deepcopy
import hashlib
import io
import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.api_backend import ModelTurn, ToolCall
from generation.runner import ScenarioRunner
from generation.injection_origins import expected_injection_origins
from schemas import ScenarioSpec, WorkflowConfig
from tasks.registry import get_default_leps


class FixtureBackend:
    def __init__(self, manifest):
        self.files = manifest['required_files']
        self.summary = 'Independent review: critical findings require verification. ' + ' '.join(self.files)

    def reset(self, *, agent_name, mcp_tools, **kwargs):
        self.role, self.tools, self.step = agent_name, mcp_tools, 0

    def _append_tool_result(self, *args, **kwargs):
        pass

    def generate(self, prompt='', tool_choice=None):
        self.step += 1
        # Two passes exercise legacy occurrence=2 triggers as well.
        files = self.files * 2
        if self.step <= len(files):
            name, args = 'read_text_file', {'path': files[self.step - 1]}
        elif self.step == len(files) + 1 and 'write_memory' in self.tools:
            name, args = 'write_memory', {'key': 'analysis_notes', 'value': self.summary}
        elif 'submit_final' in self.tools:
            name, args = 'submit_final', {'summary': self.summary}
        else:
            name, args = 'handoff', {'summary': self.summary}
        return ModelTurn(tool_call=ToolCall(id=f'{self.role}-{self.step}', name=name, input=args),
                         text='', stop_reason='tool_use')


def mock_fixture(fixture_dir):
    fixture_dir = Path(fixture_dir).resolve()
    manifest = json.loads((fixture_dir / 'manifest.json').read_text())
    results = {}
    for mode, topology in [('single_origin', 'review_loop'),
                           ('many_to_one', 'branch_and_verify'),
                           ('one_to_many', 'coordinator_workers')]:
        for lep in get_default_leps(manifest['task_family']):
            spec = ScenarioSpec(scenario_id=f"contract-{manifest['fixture_id']}-{mode}-{lep.code}",
                fixture_id=manifest['fixture_id'], task_family=manifest['task_family'],
                task_variant=manifest.get('task_variant', 'default'), condition='single_lep',
                lep_configs=[deepcopy(lep)], workflow_config=WorkflowConfig(
                    topology=topology, propagation_mode=mode, memory_mode='ephemeral_shared',
                    max_agent_turns=2 * len(manifest['required_files']) + 5))
            previous_logging = logging.root.manager.disable
            try:
                logging.disable(logging.CRITICAL)
                with TemporaryDirectory(prefix='fixture-mock-') as output, contextlib.redirect_stdout(io.StringIO()):
                    result = ScenarioRunner(llm_backend=FixtureBackend(manifest), dry_run=False,
                        output_dir=Path(output), max_events=1000).run(spec, fixture_dir.parent)
            finally:
                logging.disable(previous_logging)
            origins = [e for e in result.trace.events if e.event_labels.is_injection_origin] if result.trace else []
            expected = expected_injection_origins(condition='single_lep', lep_codes=[lep.code],
                propagation_mode=mode, topology=topology)
            fingerprints = []
            for event in origins:
                payload = {'index': event.event_index, 'type': str(event.event_type),
                           'role': event.agent_role, 'output': event.output_text,
                           'tool_result': event.tool_result, 'arguments': event.tool_arguments}
                fingerprints.append(hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest())
            results[f'{mode}/{lep.code}'] = {'expected': expected, 'actual': len(origins),
                'passed': result.runner_success and result.dataset_eligible and len(origins) == expected,
                'termination': result.termination_reason, 'hashes': fingerprints, 'error': result.error}
    return results


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('fixtures', nargs='+', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    data = {p.name: mock_fixture(p) for p in args.fixtures}
    args.output.write_text(json.dumps(data, indent=2) + '\n')
    print(json.dumps({name: {'passed': sum(v['passed'] for v in cells.values()),
                             'total': len(cells)} for name, cells in data.items()}, indent=2))
