"""Thin Ollama chat client with per-episode usage accounting."""
import json
import time

import requests

OLLAMA_URL = "http://127.0.0.1:11434"

# Optional observation hook for live watchers (the web UI sets it):
#   ("start", {"model", "role"})                     before the request goes out
#   ("token", {"text"})                              per streamed chunk
#   ("end",   {"model", "role", "output_tokens", "ms"})
# While a hook is installed the reply is streamed so a watcher can see it being
# written; the payload is otherwise identical, so sampling is unchanged. None by
# default, so the benchmark keeps making one non-streamed request per call.
STREAM_HOOK = None


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
        # tiered ModelRouter (which selects a model from it); here it only
        # labels the stream events.
        hook = STREAM_HOOK
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": bool(hook),
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
        if hook:
            hook("start", {"model": self.model, "role": role})
        t0 = time.time()
        if hook:
            content, data = self._chat_streamed(payload, hook)
        else:
            r = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
            content = data["message"]["content"]
        self.calls += 1
        self.wall += time.time() - t0
        self.prompt_tokens += data.get("prompt_eval_count", 0)
        self.output_tokens += data.get("eval_count", 0)
        if hook:
            hook("end", {"model": self.model, "role": role,
                         "output_tokens": data.get("eval_count", 0),
                         "ms": int((time.time() - t0) * 1000)})
        return content

    def _chat_streamed(self, payload, hook):
        """Same request with stream=True: hand each chunk to the hook and
        return the joined reply plus the final chunk (which carries usage)."""
        parts, final = [], {}
        with requests.post(f"{OLLAMA_URL}/api/chat", json=payload,
                           timeout=self.timeout, stream=True) as r:
            r.raise_for_status()
            for line in r.iter_lines(decode_unicode=True):
                if not line:
                    continue
                chunk = json.loads(line)
                piece = chunk.get("message", {}).get("content", "")
                if piece:
                    parts.append(piece)
                    hook("token", {"text": piece})
                if chunk.get("done"):
                    final = chunk
        return "".join(parts), final
