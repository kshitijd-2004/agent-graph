#!/bin/bash
# Submit the full MP + memory shown and ID real-findings runs: NJOBS*4 one-GPU tasks per arm
# (fsf, fsr, fsc: 12 each; fif, fir, fic: 4 each; 48 tasks total).
BASE=/work/nvme/biha/$USER
for fam in $(source $BASE/pilot_families.sh ""; echo $FULL); do
  n=$(source $BASE/pilot_families.sh "$fam"; echo $((NJOBS*4 - 1)))
  sbatch --parsable --array=0-$n --job-name=$fam --export=ALL,FAM=$fam "$BASE/pilot.sbatch" \
    | sed "s/^/$fam: job /"
done
