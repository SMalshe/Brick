#!/usr/bin/env bash
# One-time environment bootstrap for a bare GPU machine (no Docker needed).
# Creates a local venv and installs torch (CUDA 12.1 wheel) + training deps.
# Safe to re-run. No editing required.
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
if [ ! -d .venv ]; then
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip wheel

# CUDA 12.1 torch wheel — forward-compatible with most modern NVIDIA drivers.
# Override with TORCH_INDEX for a different CUDA (e.g. cu118) if your machine needs it.
TORCH_INDEX=${TORCH_INDEX:-https://download.pytorch.org/whl/cu121}
python -c "import torch" 2>/dev/null || pip install torch --index-url "$TORCH_INDEX"

pip install -r requirements.txt
echo "environment ready in ./.venv"
