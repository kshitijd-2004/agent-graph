#!/bin/bash -l
#SBATCH --job-name=prophunter
#SBATCH --account=biha-dtai-gh
#SBATCH --partition=ghx4-interactive
#SBATCH --nodes=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=32
#SBATCH --time=01:00:00
#SBATCH --output=/u/kdhande/agent-graph/agent-graph-v3/prophunter-%j.log

cd /u/kdhande/agent-graph/agent-graph-v3

module load python/miniforge3_pytorch/2.10.0

export PYTHONPATH="$HOME/.local/lib/python3.12/site-packages:$PWD:$PYTHONPATH"

echo "Node: $(hostname)"
echo "Job ID: $SLURM_JOB_ID"
nvidia-smi

python -c "import torch, torch_geometric, sklearn; print('Torch:', torch.__version__); print('PyG:', torch_geometric.__version__); print('sklearn:', sklearn.__version__); print('CUDA:', torch.cuda.is_available())"

python scripts/train_detector.py \
  --output-dir benchmark_sbatch/traces \
  --detectors static_gnn tgnn hybrid \
  --cross-validation \
  --outer-folds 5 \
  --inner-folds 4 \
  --epochs 100 \
  --batch-size 8 \
  --patience 10 \
  --device cuda \
  --no-heuristic-baselines \
  -v
