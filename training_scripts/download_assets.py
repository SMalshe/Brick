"""Prefetch everything training needs, so a clean/offline machine trains with
no manual downloads and NO Hugging Face token.

Downloads into ./assets (idempotent — skips whatever is already there):
    assets/base_model/   ungated Llama-3.2-1B-Instruct weights (HF snapshot)
    assets/llama.cpp/    llama.cpp, for GGUF adapter conversion

Run this once where there is internet (a laptop, or an HPC *login* node). After
it finishes, train_lora.py reads these local paths and needs no network at all —
which is exactly what an offline GPU compute node requires.

    python download_assets.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")

# Ungated mirror — same weights as meta-llama/Llama-3.2-1B-Instruct (which is
# what Ollama's llama3.2:1b serves), but downloadable with no license/token.
BASE_ID = os.environ.get("BASE_MODEL_ID", "unsloth/Llama-3.2-1B-Instruct")
LLAMACPP_REPO = os.environ.get("LLAMACPP_REPO", "https://github.com/ggml-org/llama.cpp")


def fetch_model():
    dst = os.path.join(ASSETS, "base_model")
    if os.path.isfile(os.path.join(dst, "config.json")):
        print(f"[model] already present -> {dst}")
        return dst
    from huggingface_hub import snapshot_download
    print(f"[model] downloading {BASE_ID} (no token needed) -> {dst}")
    snapshot_download(
        repo_id=BASE_ID, local_dir=dst,
        # weights + tokenizer + configs only; skip anything huge/irrelevant
        allow_patterns=["*.json", "*.safetensors", "*.model", "tokenizer*", "*.txt"])
    return dst


def fetch_llamacpp():
    dst = os.path.join(ASSETS, "llama.cpp")
    if os.path.isfile(os.path.join(dst, "convert_lora_to_gguf.py")):
        print(f"[llama.cpp] already present -> {dst}")
        return dst
    print(f"[llama.cpp] cloning -> {dst}")
    subprocess.run(["git", "clone", "--depth", "1", LLAMACPP_REPO, dst], check=True)
    return dst


def install_gguf_py(llamacpp_dir):
    """Install the gguf package that matches the cloned llama.cpp, so
    convert_lora_to_gguf.py (--to-gguf) can't hit a gguf-version mismatch.
    Best-effort: the pinned gguf in requirements.txt is the fallback."""
    gguf_py = os.path.join(llamacpp_dir, "gguf-py")
    if not os.path.isdir(gguf_py):
        return
    print("[gguf] installing matching gguf-py from the cloned llama.cpp")
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "-e", gguf_py], check=True)
    except (subprocess.CalledProcessError, OSError) as e:
        print(f"[gguf] gguf-py install skipped ({e}); pinned gguf will be used.")


if __name__ == "__main__":
    os.makedirs(ASSETS, exist_ok=True)
    m = fetch_model()
    lc = fetch_llamacpp()
    install_gguf_py(lc)
    print(f"\nassets ready:\n  base_model: {m}\n  llama.cpp : {lc}\n"
          f"training can now run fully offline.")
