#!/usr/bin/env python3.11
"""
Top-level driver for the final analysis directory.

  python3.11 analysis/behavioral_divergence_final/run_all.py --stage <stage>

Stages (in order; each writes only under this directory):
  corpus-inventory   P2/P1/P0/P3 sizes + frozen-ledger fixed references
  candspec-ledger    full-frame candspec ledger (16,773) -> out/
  partitioning       fresh dev/holdout partition -> out/partitions_final.json
  sampling           component A/B + challenge + pair groups (deterministic)
  synthetic          G2/G3 controlled-correctness suite
  pre-seal           DEV-side judge items + live dev chunks + reference
                     (heldout untouched)
  seal               fingerprint the protected artifacts + candidate set
  final-eval         CONSUME the heldout (once); gate verdict
  production         production analysis over the frame (any implementation)
  verify             mechanical integrity gate (11 checks)
  report             assemble results/ publication summaries
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common


def _py(script: str, extra: list) -> None:
    cmd = [sys.executable, os.path.join(HERE, script)] + extra
    print('$', ' '.join(cmd))
    subprocess.run(cmd, check=True)


def corpus_inventory():
    import production
    inv = production.corpus_inventory()
    print(json.dumps(inv, indent=1))


def candspec_ledger():
    _py('extractors/candspec_pipeline.py', ['--prefix', 'candspec'])


def partitioning():
    _py('partitioning.py', [])


def sampling():
    _py('sampling.py', [])


def synthetic():
    _py('synthetic/run_synthetic.py', [])


def pre_seal():
    _py('eval_cli.py', ['--pre-seal'])


def seal():
    _py('eval_cli.py', ['--seal'])


def final_eval():
    _py('eval_cli.py', ['--final-eval'])


def production():
    _py('production.py', ['--impl', 'candspec'])
    _py('audit/build_packets.py', ['--n', '60'])


def verify():
    _py('verify.py', [])


def report():
    _py('report.py', [])


STAGES = {
    'corpus-inventory': corpus_inventory,
    'candspec-ledger': candspec_ledger,
    'partitioning': partitioning,
    'sampling': sampling,
    'synthetic': synthetic,
    'pre-seal': pre_seal,
    'seal': seal,
    'final-eval': final_eval,
    'production': production,
    'verify': verify,
    'report': report,
    'all-dev-safe': lambda: [f() for f in (
        candspec_ledger, partitioning, sampling, synthetic, production,
        verify)],
}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--stage', required=True, choices=sorted(STAGES))
    a = ap.parse_args()
    STAGES[a.stage]()


if __name__ == '__main__':
    main()
