"""Thin Ollama chat client with per-episode usage accounting."""
import time

import requests

OLLAMA_URL = "http://127.0.0.1:11434"


class LLM:
    def __init__(self, model, num_ctx=8192, temperature=0.0, timeout=900,
                 keep_alive="30m"):
        self.model = model
        self.num_ctx = num_ctx
        self.temperature = temperature
        self.timeout = timeout
        self.keep_alive = keep_alive  # "0" evicts the model right after the call
        self.reset_usage()

    def reset_usage(self):
        self.calls = 0
        self.prompt_tokens = 0
        self.output_tokens = 0
        self.wall = 0.0

    def chat(self, messages, force_json=False, num_predict=700, role=None,
             keep_alive=None):
        # role is accepted so a plain LLM is drop-in interchangeable with the
        # tiered ModelRouter (which selects a model from it); here it is unused.
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": keep_alive or self.keep_alive,
            "options": {
                "temperature": self.temperature,
                "seed": 42,
                "num_ctx": self.num_ctx,
                "num_predict": num_predict,
            },
        }
        if force_json:
            payload["format"] = "json"
        t0 = time.time()
        r = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        self.calls += 1
        self.wall += time.time() - t0
        self.prompt_tokens += data.get("prompt_eval_count", 0)
        self.output_tokens += data.get("eval_count", 0)
        return data["message"]["content"]
