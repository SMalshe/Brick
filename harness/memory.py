"""Persistent memory ("learning") store.

Facts persist across episodes in a JSONL file. Retrieval is simple keyword
overlap - deliberately cheap so it runs identically for every model size.
The harness condition auto-injects relevant memories into the system prompt;
the raw condition only sees memories if the model calls recall_memories itself.
"""
import json
import os
import re

_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "to", "of", "and", "or", "for", "with", "my", "me",
         "i", "is", "are", "in", "on", "at", "it", "that", "this", "be", "do"}


def _tokens(text):
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP}


class MemoryStore:
    def __init__(self, path):
        self.path = path
        self.facts = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self.facts.append(json.loads(line)["fact"])

    def save(self, fact):
        fact = str(fact).strip()
        if not fact:
            return "nothing to save"
        self.facts.append(fact)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"fact": fact}, ensure_ascii=False) + "\n")
        return f"saved to long-term memory: {fact}"

    def search(self, query, k=3):
        q = _tokens(str(query))
        scored = []
        for fact in self.facts:
            overlap = len(q & _tokens(fact))
            if overlap:
                scored.append((overlap, fact))
        scored.sort(key=lambda t: -t[0])
        return [fact for _, fact in scored[:k]]

    def all(self):
        return list(self.facts)
