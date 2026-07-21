# Tool-calling LoRA trainer — fully self-contained, offline-ready GPU image.
# Build (needs internet ONCE, at build time):  docker build -t toolcall-lora .
# The image bakes in torch+CUDA, all deps, llama.cpp, AND the ungated base
# model — so the resulting container/.sif runs on an air-gapped compute node
# with no token, no downloads, and no editing.
FROM pytorch/pytorch:2.3.1-cuda12.1-cudnn8-runtime

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY train_lora.py download_assets.py make_data.py system_prompt.txt ./
COPY data/ ./data/

# Generate the data inside the image if it wasn't shipped in (self-contained),
# then bake model + llama.cpp in (ungated mirror, no HF token needed).
RUN [ -s data/toolcall.jsonl ] || python make_data.py --out data/toolcall.jsonl
RUN python download_assets.py
ENV LLAMACPP_DIR=/workspace/assets/llama.cpp
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1

# Defaults produce out/toolcall-lora (adapter + GGUF). Pass flags after the
# image name to override; mount a volume on /workspace/out to keep artifacts.
ENTRYPOINT ["python", "train_lora.py"]
CMD ["--to-gguf"]
