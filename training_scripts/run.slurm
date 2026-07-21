#!/bin/bash
# Slurm submission for a GPU computing center (Slurm + Apptainer/Singularity).
# The container is fully self-contained — model, data, and tooling are baked in —
# so the compute node needs NO internet and NO Hugging Face token.
# Submit with:  sbatch run.slurm
#
# One-time, on a login node (which has internet), build the .sif — no Docker
# needed, straight from the definition file:
#     apptainer build toolcall-lora.sif apptainer.def
#   (older Singularity:  singularity build toolcall-lora.sif apptainer.def)
#
#SBATCH --job-name=toolcall-lora
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=02:00:00
#SBATCH --output=lora_%j.log

set -euo pipefail

# Some centers require a module load; uncomment whichever applies:
# module load apptainer      # or:  module load singularity

RUNNER=$(command -v apptainer || command -v singularity)
SIF=${SIF:-toolcall-lora.sif}
OUTDIR=${OUTDIR:-$PWD/out}
mkdir -p "$OUTDIR"

# --nv exposes the GPU; bind the writable output dir so the adapter lands on the
# shared filesystem. Everything else is inside the image — no network used.
"$RUNNER" exec --nv \
  --bind "$OUTDIR:/workspace/out" \
  "$SIF" \
  python /workspace/train_lora.py --output /workspace/out/toolcall-lora --to-gguf

echo "done. adapter + gguf in: $OUTDIR/toolcall-lora"
