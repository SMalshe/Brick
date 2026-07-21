"""Train a tool-calling LoRA adapter for Llama 3.2 1B — portable, single-GPU.

Send this whole folder to a computing center. It reads a chat-format JSONL
(each row {"messages":[...]} with the assistant turn = the correct tool call),
fine-tunes a LoRA on the base model with ASSISTANT-ONLY loss, and writes the
adapter (adapter_model.safetensors + adapter_config.json). With --to-gguf it
also emits a GGUF adapter ready for llama-server's /lora-adapters.

Deliberately framework-light: transformers + peft only (no trl), so it survives
version drift on an unknown cluster. Assistant-token masking is done by hand.

Run (inside the container or a GPU env):
    python train_lora.py --data data/toolcall.jsonl --output out/toolcall-lora

Key knobs (all have sane defaults; override via flags or env):
    --base   HF model id or local path      (default: prefetched ./assets/base_model,
             else the ungated unsloth/Llama-3.2-1B-Instruct — no HF token needed)
    --rank --alpha --dropout --lr --epochs --batch --grad-accum --max-len
    --load-4bit   QLoRA (needs bitsandbytes) for small GPUs
    --to-gguf     also convert to GGUF via $LLAMACPP_DIR/convert_lora_to_gguf.py
"""
import argparse
import json
import os
import subprocess
import sys

import torch
from datasets import load_dataset
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from transformers import (AutoModelForCausalLM, AutoTokenizer, Trainer,
                          TrainingArguments)

# Anchor every default path to this script's folder, so the package runs from
# any working directory with no editing.
HERE = os.path.dirname(os.path.abspath(__file__))
LOCAL_BASE = os.path.join(HERE, "assets", "base_model")
LOCAL_LLAMACPP = os.path.join(HERE, "assets", "llama.cpp")


def default_base():
    """Prefer the prefetched local model (offline-ready); else the ungated hub
    mirror — never a gated repo, so no HF token is ever required."""
    if os.path.isfile(os.path.join(LOCAL_BASE, "config.json")):
        return LOCAL_BASE
    return os.environ.get("BASE_MODEL", "unsloth/Llama-3.2-1B-Instruct")


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--data", default=os.environ.get("TRAIN_DATA",
                                                    os.path.join(HERE, "data", "toolcall.jsonl")))
    p.add_argument("--output", default=os.environ.get("OUTPUT_DIR",
                                                      os.path.join(HERE, "out", "toolcall-lora")))
    p.add_argument("--base", default=os.environ.get("BASE_MODEL", default_base()))
    p.add_argument("--rank", type=int, default=16)
    p.add_argument("--alpha", type=int, default=32)
    p.add_argument("--dropout", type=float, default=0.05)
    p.add_argument("--lr", type=float, default=2e-4)
    p.add_argument("--epochs", type=float, default=3.0)
    p.add_argument("--batch", type=int, default=8)
    p.add_argument("--grad-accum", type=int, default=2)
    p.add_argument("--max-len", type=int, default=2560)
    p.add_argument("--warmup-ratio", type=float, default=0.03)
    p.add_argument("--load-4bit", action="store_true")
    p.add_argument("--to-gguf", action="store_true")
    p.add_argument("--gguf-outfile", default=None)
    return p.parse_args()


def build_example(messages, tokenizer, max_len):
    """Tokenize a conversation, masking every non-assistant token to -100 so
    loss is computed on the tool-call outputs only. Relies on the chat template
    being append-only (true for Llama-style templates): the token ids for a
    prefix are a prefix of the token ids for the longer conversation."""
    input_ids, labels, prev_len = [], [], 0
    for i, msg in enumerate(messages):
        ids = tokenizer.apply_chat_template(messages[: i + 1], tokenize=True,
                                            add_generation_prompt=False)
        # Safety net: this masking is only correct if the chat template is
        # append-only (each turn's tokens are a prefix of the next). Fail loudly
        # rather than silently train on mislabeled tokens.
        if ids[:prev_len] != input_ids:
            raise ValueError(
                "chat template is not append-only; assistant-only masking would "
                "be wrong for this tokenizer. Use a Llama-style template.")
        seg = ids[prev_len:]
        input_ids += seg
        labels += seg if msg["role"] == "assistant" else [-100] * len(seg)
        prev_len = len(ids)
    if len(input_ids) > max_len:  # keep the tail (the assistant target) if we must cut
        input_ids, labels = input_ids[-max_len:], labels[-max_len:]
    return {"input_ids": input_ids, "labels": labels,
            "attention_mask": [1] * len(input_ids)}


class Collator:
    def __init__(self, pad_id):
        self.pad_id = pad_id

    def __call__(self, batch):
        m = max(len(x["input_ids"]) for x in batch)
        ids, lab, att = [], [], []
        for x in batch:
            pad = m - len(x["input_ids"])
            ids.append(x["input_ids"] + [self.pad_id] * pad)
            lab.append(x["labels"] + [-100] * pad)
            att.append(x["attention_mask"] + [0] * pad)
        t = lambda a: torch.tensor(a, dtype=torch.long)
        return {"input_ids": t(ids), "labels": t(lab), "attention_mask": t(att)}


def to_gguf(adapter_dir, base, outfile):
    llamacpp = os.environ.get("LLAMACPP_DIR",
                              LOCAL_LLAMACPP if os.path.isdir(LOCAL_LLAMACPP) else "/opt/llama.cpp")
    conv = os.path.join(llamacpp, "convert_lora_to_gguf.py")
    if not os.path.isfile(conv):
        print(f"[gguf] {conv} not found; skipping (set LLAMACPP_DIR). "
              f"Adapter is still saved at {adapter_dir}.")
        return
    outfile = outfile or os.path.join(adapter_dir, "toolcall-lora.gguf")
    cmd = [sys.executable, conv, "--base", base, "--outfile", outfile, adapter_dir]
    print("[gguf]", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True)
        print(f"[gguf] wrote {outfile}")
    except subprocess.CalledProcessError as e:
        print(f"[gguf] conversion failed ({e}); the PEFT adapter is still valid.")


def main():
    a = parse_args()
    # If we're using the prefetched local model, forbid network so an offline
    # compute node never stalls trying to reach Hugging Face.
    if os.path.isdir(a.base) and os.path.isfile(os.path.join(a.base, "config.json")):
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

    cuda = torch.cuda.is_available()
    if not cuda:
        print("WARNING: no CUDA GPU visible — training will be extremely slow.")
    bf16 = cuda and torch.cuda.is_bf16_supported()
    fp16 = cuda and not bf16
    dtype = torch.bfloat16 if bf16 else (torch.float16 if fp16 else torch.float32)

    print(f"base={a.base}  data={a.data}  out={a.output}  4bit={a.load_4bit}  bf16={bf16}")
    tok = AutoTokenizer.from_pretrained(a.base)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    model_kwargs = {"torch_dtype": dtype}
    if a.load_4bit:
        from transformers import BitsAndBytesConfig
        model_kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=dtype, bnb_4bit_use_double_quant=True)
        model_kwargs["device_map"] = {"": 0}
    else:
        model_kwargs["device_map"] = None
    model = AutoModelForCausalLM.from_pretrained(a.base, **model_kwargs)
    model.config.use_cache = False

    # Gradient checkpointing needs inputs to require grad; with frozen base
    # weights that isn't automatic. prepare_model_for_kbit_training handles it
    # for QLoRA, else enable it explicitly.
    if a.load_4bit:
        model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    else:
        model.enable_input_require_grads()

    lora = LoraConfig(
        r=a.rank, lora_alpha=a.alpha, lora_dropout=a.dropout, bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"])
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()

    ds = load_dataset("json", data_files=a.data, split="train")
    ds = ds.map(lambda r: build_example(r["messages"], tok, a.max_len),
                remove_columns=ds.column_names, desc="tokenizing")
    ds = ds.filter(lambda r: any(l != -100 for l in r["labels"]))  # drop rows with no target
    print(f"training rows: {len(ds)}")

    targs = TrainingArguments(
        output_dir=a.output, num_train_epochs=a.epochs,
        per_device_train_batch_size=a.batch, gradient_accumulation_steps=a.grad_accum,
        learning_rate=a.lr, warmup_ratio=a.warmup_ratio, lr_scheduler_type="cosine",
        logging_steps=10, save_strategy="epoch", save_total_limit=1,
        bf16=bf16, fp16=fp16, gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        report_to=[], optim="adamw_torch")

    trainer = Trainer(model=model, args=targs, train_dataset=ds,
                      data_collator=Collator(tok.pad_token_id))
    trainer.train()

    os.makedirs(a.output, exist_ok=True)
    model.save_pretrained(a.output)      # <- adapter_model.safetensors + adapter_config.json
    tok.save_pretrained(a.output)
    with open(os.path.join(a.output, "training_meta.json"), "w") as f:
        json.dump({"base": a.base, "rank": a.rank, "alpha": a.alpha,
                   "epochs": a.epochs, "rows": len(ds)}, f, indent=2)
    print(f"\nADAPTER SAVED -> {a.output}")

    if a.to_gguf:
        to_gguf(a.output, a.base, a.gguf_outfile)


if __name__ == "__main__":
    main()
