#!/bin/bash
# Merge the four swebench-200 node trace directories into one.
# Run AFTER all swebench200_node*.slurm jobs have completed successfully.
#
#   ./merge_slurm.sh
#
# Output: production_runs/swebench_200_addition/merged/
set -euo pipefail
cd "$(dirname "$0")/../.."   # repo root

BASE=production_runs/swebench_200_addition
for i in 0 1 2 3; do
    [ -d "$BASE/node_$i" ] || { echo "missing $BASE/node_$i — node $i did not finish?"; exit 1; }
done

python -m benchmark \
  --merge "$BASE"/node_0 "$BASE"/node_1 "$BASE"/node_2 "$BASE"/node_3 \
  --output "$BASE/merged"
echo "Merged swebench-200 addition -> $BASE/merged"
