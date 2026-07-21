#!/usr/bin/env bash
# TURNKEY: send this folder to any Linux GPU machine and run `bash run.sh`.
# It bootstraps the env, downloads the (ungated, no-token) base model and
# llama.cpp, trains the LoRA, and writes the adapter + GGUF. No editing, no
# Hugging Face account, no pre-installed models required.
set -euo pipefail
cd "$(dirname "$0")"

# 1. environment (venv + torch + deps), unless already inside a ready container
if ! python3 -c "import torch, transformers, peft" 2>/dev/null; then
  bash setup.sh
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

# 2. generate the training data if it isn't already here (self-contained)
if [ ! -s data/toolcall.jsonl ]; then
  echo "no data/toolcall.jsonl — generating it from make_data.py"
  python make_data.py --out data/toolcall.jsonl
fi

# 3. fetch model + llama.cpp into ./assets (idempotent; needs internet ONCE)
python download_assets.py

# 4. train -> ./out/toolcall-lora/ (adapter + toolcall-lora.gguf)
python train_lora.py --to-gguf

echo
echo "DONE. Adapter and GGUF are in: $(pwd)/out/toolcall-lora"
