"""Offline comparison against a byte-frozen production snapshot; no inference."""
import argparse
import importlib.util
import json
import logging
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['new', 'old', 'compare'], required=True)
    parser.add_argument('--report', type=Path, default=ROOT / 'analysis/admission_hardening')
    parser.add_argument('--baseline', type=Path, default=ROOT / '.cache/admission_hardening/old_propagation_analysis.py')
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    snapshot = json.loads((args.report / 'snapshot.json').read_text())
    source = Path(snapshot['snapshot'])
    output = args.report / args.mode
    output.mkdir(parents=True, exist_ok=True)
    if args.mode == 'new':
        from benchmark.propagation_analysis import PropagationAnalyzer
        analyzer = PropagationAnalyzer(output, source_dir=source)
        result = analyzer.analyze()
    elif args.mode == 'old':
        spec = importlib.util.spec_from_file_location('old_propagation', args.baseline)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        # The baseline resolves fixture manifests relative to its module file.
        # Keep that location identical to production despite loading saved code.
        module.__file__ = str(ROOT / 'benchmark/propagation_analysis.py')
        inputs = source.parent / 'old_accepted_inputs'
        inputs.mkdir(exist_ok=True)
        for trace in source.glob('node_*/traces/*_trace.json'):
            link = inputs / trace.name
            if not link.exists():
                link.symlink_to(trace.resolve())
        analyzer = module.PropagationAnalyzer(output)
        analyzer.traces_dir = inputs
        result = analyzer.analyze()
    else:
        old = json.loads((args.report / 'old/propagation_summary.json').read_text())
        new = json.loads((args.report / 'new/propagation_summary.json').read_text())
        changed = {}
        for key in sorted(old['by_cell'].keys() | new['by_cell'].keys()):
            before, after = old['by_cell'].get(key, {}), new['by_cell'].get(key, {})
            if not before or not after or before.get('propagation_probability') != after.get('propagation_probability') or before.get('total_lep_runs',0) != after.get('measurable_propagation_outcomes',0):
                changed[key] = {'old_probability': before.get('propagation_probability'),
                    'new_probability': after.get('propagation_probability'),
                    'old_measured': before.get('total_lep_runs',0),
                    'new_measured': after.get('measurable_propagation_outcomes',0),
                    'new_admitted': after.get('valid_admitted_perturbed_traces',0),
                    'reasons': after.get('rejected_by_reason',{})}
        result = {'old': old['overall'], 'new': new['overall'], 'changed_cells': changed,
                  'frozen_historical_plan_available': False}
        (args.report / 'comparison.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result.get('overall', {k:v for k,v in result.items() if k != 'changed_cells'}), indent=2))


if __name__ == '__main__':
    main()
