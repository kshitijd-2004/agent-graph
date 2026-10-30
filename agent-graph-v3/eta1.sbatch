#!/bin/bash -l
#SBATCH --job-name=eta1
#SBATCH --account=biha-dtai-gh
#SBATCH --partition=ghx4-interactive
#SBATCH --nodes=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=32
#SBATCH --time=02:00:00

sleep 30
