#!/usr/bin/env bash
# Run on any machine with Docker + an NVIDIA GPU (nvidia-container-toolkit).
# Builds a self-contained image (model baked in, no token) and produces
# ./out/toolcall-lora/ with the adapter and a GGUF. No editing.
set -euo pipefail
cd "$(dirname "$0")"

docker build -t toolcall-lora .

mkdir -p "$PWD/out"
docker run --rm --gpus all \
  -v "$PWD/out:/workspace/out" \
  toolcall-lora --to-gguf

echo "done -> $PWD/out/toolcall-lora"
