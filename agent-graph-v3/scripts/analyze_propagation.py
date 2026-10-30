"""Analyze production trace directories with versioned LEP-specific detectors."""
import argparse
import json
import logging
from pathlib import Path

from benchmark.production_propagation import ProductionPropagationAnalyzer, VERSION, CORRECTED_VERSION, TRC_V3_VERSION


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, required=True,
                        help='Consolidated traces/ and rejected_traces/, or a root of node_* shards')
    parser.add_argument('--output-dir', type=Path, required=True, help='New, separate output directory')
    parser.add_argument('--detector-version', choices=[VERSION, CORRECTED_VERSION, TRC_V3_VERSION, 'legacy'], default=VERSION,
                        help='V3 adds bounded TRC source/numeric-header recognizers; V2 remains reproducible. HC, IPI, MP and V2 ID are unchanged.')
    parser.add_argument('--fixture-root', type=Path, help='Defaults to repository workspace_fixtures')
    parser.add_argument('--inventory-only', action='store_true', help='Discover, deduplicate, admit, and group references without detection')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.ERROR, format='%(asctime)s %(levelname)s %(message)s')
    logging.getLogger('benchmark.production_propagation').setLevel(logging.INFO)
    analyzer = ProductionPropagationAnalyzer(args.source_dir, args.output_dir,
        detector_version=args.detector_version, fixture_root=args.fixture_root)
    result = analyzer.analyze(inventory_only=args.inventory_only)
    print(json.dumps(result.get('overall', result), indent=2))
    if not args.inventory_only and result['admission']['overall']['analysis_failures']:
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
