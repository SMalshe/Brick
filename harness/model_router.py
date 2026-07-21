"""Tiered, RAM-aware model router with a LoRA-adapter seam.

Two ideas from the architecture doc, adapted to a Codex-style open agent loop:

1. Multi-model routing. The agent's model calls carry a `role` (driver /
   router / verifier / deep). Each role maps to a tier: a model tag, sampling
   settings, an optional keep-alive, and an optional LoRA adapter.

2. RAM optimisation ("one model active at once"). The DEFAULT lineup points
   every interactive role at ONE base tag, so exactly one model is resident.
   The heavy `deep` role is marked on_demand with keep_alive="0": Ollama loads
   it only for that call and evicts it immediately, so it never co-resides for
   longer than a single request. Nothing but the one base stays in RAM between
   calls.

LoRA-adapter seam. Each role may name an `adapter`. The default backend here is
Ollama, whose HTTP API cannot hot-swap a LoRA per request, so it treats the
adapter as documentation and specialises the base by prompt+sampling instead.
A llama-server backend (llama-server.exe ships with Ollama) CAN toggle a LoRA
per call via its /lora-adapters endpoint at ~0 RAM cost — that backend is the
place a real adapter plugs in. See adapters_note() for the honest status.

Drop-in for harness.llm.LLM: exposes .chat(messages, force_json=, num_predict=,
role=), plus .calls / .output_tokens / .prompt_tokens / .wall, so run_harness
accepts either object unchanged.
"""
import json
import os
import time

from .llm import LLM


def default_roles(base="llama3.1:8b", small=None, deep="qwen2.5:14b"):
    """The RAM-optimal default: driver/router/verifier all share one resident
    base; deep is load-on-demand and evicted after use.

    Pass small=<tag> to give routing/verify a cheaper model — faster per call,
    but then TWO models are resident. Leave small=None to stay single-resident.
    """
    light = small or base
    return {
        "driver":   {"model": base,  "temperature": 0.0, "num_predict": 700, "keep_alive": "30m"},
        "router":   {"model": light, "temperature": 0.0, "num_predict": 250, "keep_alive": "30m"},
        "verifier": {"model": light, "temperature": 0.0, "num_predict": 250, "keep_alive": "30m"},
        "deep":     {"model": deep,  "temperature": 0.2, "num_predict": 900, "keep_alive": "0",
                     "on_demand": True},
    }


class ModelRouter:
    def __init__(self, roles=None, num_ctx=8192, log_path=None, default_role="driver"):
        self.roles = roles or default_roles()
        self.num_ctx = num_ctx
        self.default_role = default_role
        self.log_path = log_path
        self.call_log = []          # one record per model call
        self._clients = {}          # (model, keep_alive) -> LLM, reused
        self.reset_usage()

    # --- usage, aggregated across every tier so the budget check still works --
    def reset_usage(self):
        self.calls = 0
        self.prompt_tokens = 0
        self.output_tokens = 0
        self.wall = 0.0

    def _client(self, spec):
        key = (spec["model"], spec.get("keep_alive", "30m"))
        if key not in self._clients:
            self._clients[key] = LLM(spec["model"], num_ctx=self.num_ctx,
                                     temperature=spec.get("temperature", 0.0),
                                     keep_alive=spec.get("keep_alive", "30m"))
        return self._clients[key]

    def resident_models(self):
        """Tags that stay in RAM between calls (everything not on_demand)."""
        return sorted({s["model"] for s in self.roles.values() if not s.get("on_demand")})

    def chat(self, messages, force_json=False, num_predict=None, role=None):
        spec = self.roles.get(role) or self.roles[self.default_role]
        llm = self._client(spec)
        np = num_predict if num_predict is not None else spec.get("num_predict", 700)

        before_out, before_prompt = llm.output_tokens, llm.prompt_tokens
        t0 = time.time()
        content = llm.chat(messages, force_json=force_json, num_predict=np,
                           keep_alive=spec.get("keep_alive"))
        dt = time.time() - t0

        d_out = llm.output_tokens - before_out
        d_prompt = llm.prompt_tokens - before_prompt
        self.calls += 1
        self.wall += dt
        self.output_tokens += d_out
        self.prompt_tokens += d_prompt

        rec = {"ts": round(time.time(), 3), "role": role or self.default_role,
               "model": spec["model"], "adapter": spec.get("adapter"),
               "prompt_tokens": d_prompt, "output_tokens": d_out,
               "latency_ms": int(dt * 1000)}
        self.call_log.append(rec)
        if self.log_path:
            try:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            except OSError:
                pass
        return content

    def usage_by_role(self):
        agg = {}
        for r in self.call_log:
            a = agg.setdefault(r["role"], {"calls": 0, "output_tokens": 0, "ms": 0, "model": r["model"]})
            a["calls"] += 1
            a["output_tokens"] += r["output_tokens"]
            a["ms"] += r["latency_ms"]
        return agg


def adapters_note():
    """Honest one-liner about the LoRA seam, for the runner banner."""
    return ("LoRA adapters: seam present (per-role 'adapter' field). Default Ollama "
            "backend cannot hot-swap them, so roles specialise by prompt for now; a "
            "llama-server backend + trained GGUF adapters is the path to real swapping.")
