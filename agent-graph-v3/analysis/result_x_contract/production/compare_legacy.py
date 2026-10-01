"""CPU-only before/after legacy trace comparison using saved pre-session files."""
import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import logging
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
import generation.runner as current_runner
import generation.stage_runner as current_stage
import test_injection_count_regression as production


def load(name, file):
    loader = importlib.machinery.SourceFileLoader(name, str(file))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


HERE = Path(__file__).resolve().parent
old_runner = load('result_x_baseline_runner', HERE / 'runner.py.before')
old_stage = load('result_x_baseline_stage', HERE / 'stage_runner.py.before')
runner_class, stage_class = current_runner.ScenarioRunner, current_stage.StageRunner
logging.disable(logging.CRITICAL)
cases = [('review_loop', 'single_origin'), ('branch_and_verify', 'single_origin'),
         ('branch_and_verify', 'many_to_one'), ('coordinator_workers', 'single_origin'),
         ('coordinator_workers', 'one_to_many')]
codes = [None, 'LEP_TOOL_RESULT_CORRUPTION', 'LEP_INDIRECT_PROMPT_INJECTION',
         'LEP_MEMORY_POISONING', 'LEP_HANDOFF_CORRUPTION', 'LEP_INPUT_DISREGARD']


def normalized(result):
    def visit(value):
        if isinstance(value, dict):
            return {k: visit(v) for k, v in value.items() if k not in {'timestamp', 'runtime_seconds'}}
        if isinstance(value, list):
            return [visit(v) for v in value]
        if isinstance(value, str):
            return value.replace(result.trace.execution_id, '<execution_id>')
        return value
    return visit(result.trace.to_dict())


rows = []
with tempfile.TemporaryDirectory(prefix='result_x_legacy_') as work:
    try:
        for topology, mode in cases:
            for code in codes:
                outputs = []
                for label, rc, sc in [('before', old_runner.ScenarioRunner, old_stage.StageRunner),
                                      ('after', runner_class, stage_class)]:
                    production.ScenarioRunner = rc
                    current_stage.StageRunner = sc
                    with contextlib.redirect_stdout(io.StringIO()):
                        result = production.execute(Path(work) / f'{topology}_{mode}_{code}_{label}',
                                                    mode=mode, topology=topology, code=code)
                    outputs.append(normalized(result))
                identical = outputs[0] == outputs[1]
                row = {'topology': topology, 'mode': mode, 'code': code or 'benign', 'identical': identical,
                       'hashes': [hashlib.sha256(json.dumps(o, sort_keys=True).encode()).hexdigest() for o in outputs]}
                rows.append(row)
                if not identical:
                    (HERE / f'diff_{topology}_{mode}_{code}.json').write_text(json.dumps(outputs, indent=2))
    finally:
        production.ScenarioRunner = runner_class
        current_stage.StageRunner = stage_class
(HERE / 'legacy_comparison.json').write_text(json.dumps(rows, indent=2))
print(f'{sum(r["identical"] for r in rows)}/{len(rows)} legacy traces identical after removing only timestamps and random execution IDs')
assert all(r['identical'] for r in rows)
