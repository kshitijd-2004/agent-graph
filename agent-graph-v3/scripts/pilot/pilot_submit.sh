#!/bin/bash
# Submit every pilot arm: 4 one-GPU tasks per arm (32 slices), 24 tasks total.
BASE=/work/nvme/biha/$USER
source $BASE/pilot_families.sh ""
for fam in $FAMS; do
  sbatch --parsable --array=0-3 --job-name=p$fam --export=ALL,FAM=$fam "$BASE/pilot.sbatch" \
    | sed "s/^/$fam: job /"
done
