#!/bin/bash
# ────────────────────────────────────────────────────────────────────────────
# READY TO USE — DO NOT EXECUTE FROM THIS SESSION (user launches manually).
#
# Launches the 148-fixture / 4,292-execution SWE-bench 101–300 addition as a
# 4-node shard. Each node runs vLLM on its GPU (port 8001) and executes its
# plan-manifest shard; after all nodes finish, run merge_slurm.sh.
#
# Per-node trace outputs:  production_runs/swebench_200_addition/node_<i>/
#
# Submit all four:
#   for i in 0 1 2 3; do sbatch analysis/swebench_200_addition/slurm/swebench200_node$i.slurm; done
#
# Or from this directory:
#   ./launch_slurm.sh            # submits the four node jobs
#
# Notes:
#  - Partition is set to ghx4-interactive in the .slurm headers. Override with
#    `sbatch --partition <name> ...` if your account quota differs.
#  - To run on N≠4 GPUs: edit --num-nodes in each .slurm (and regenerate the
#    per-node files from node_template.slurm). 1 GPU = single node: remove the
#    --run-node/--num-nodes pair from one .slurm.
# ────────────────────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")/../.."   # repo root (script lives in analysis/swebench_200_addition/)

for i in 0 1 2 3; do
    sbatch analysis/swebench_200_addition/slurm/swebench200_node${i}.slurm
done
