# Per-model on-device agents

Each folder is a self-contained agent at one model size. Everything runs
locally on this machine: inference goes through the local Ollama server at
`127.0.0.1:11434` (weights stored under `C:\Users\Lab User\SAIL\ollama`), and
all agent state stays inside the folder. No cloud service is involved at any
point; the runner refuses non-local endpoints.

| folder | model | notes |
|---|---|---|
| `1b/`  | llama3.2:1b | |
| `3b/`  | llama3.2:3b | |
| `8b/`  | llama3.1:8b | |
| `14b/` | qwen2.5:14b | no Llama exists at 14B, Qwen 2.5 is the standard local choice |
| `32b/` | qwen2.5:32b | no Llama exists at ~30B (family: 1B/3B/8B/70B) |

## Use

```powershell
cd agents\8b
.\run.ps1 "Find a free hour on Thursday and book it as Deep work"
```

### Real-computer mode (`--root`)

By default the agent only sees the simulated office world. Give it `--root` and
it gets Codex / Claude-Desktop-style access to **real files** under that folder:
`list_dir`, `read_file`, `write_file`, `append_file`, `delete_path`,
`move_path`, `search_files`, and (with `--shell`) `run_command`.

```powershell
.\run.ps1 --root "C:\Users\Lab User\Desktop\sandbox" "Tidy these notes into folders"
.\run.ps1 --root . --shell "What changed in this project today?"
```

Guardrails (see `..\..\harness\fs_tools.py`): every path is resolved against
the root and must stay inside it (`..\`, absolute paths and `%VAR%` escapes are
blocked); a deny-list keeps `Windows\`, `Program Files\`, the Python
interpreter, the Ollama model blobs, and this project's `results\`/`harness\`
unwritable even at a drive root; overwrite/delete/move/shell prompt for
confirmation. `--yolo` skips the prompts. An open-loop small model *will*
eventually issue a wrong write — choose the root accordingly.

### Model tiers (`--tiers`) — one model resident, RAM-optimised

`--tiers` runs the agent's model calls through a router (`..\..\harness\model_router.py`).
Each call carries a role — `driver` (picks the next tool), `router` (planning),
`verifier` (the pre-`done` check) — and the **default lineup points all three at
one base tag, so exactly one model stays in RAM**. A heavier `deep` tier is
declared but load-on-demand (`keep_alive:0`, evicted after use) and is not
auto-invoked. Per-tier token/latency usage prints at the end and is logged to
`logs\model_calls.jsonl`.

```powershell
.\run.ps1 --root . --tiers "Summarise the README and write a one-line TODO file"
.\run.ps1 --tiers --small llama3.2:3b "..."   # cheaper routing/verify (2 models resident)
.\run.ps1 --tiers --deep qwen2.5:32b "..."    # heavier on-demand tier
```

Each role can name a **LoRA `adapter`** (per-role field in the router config).
The default Ollama backend can't hot-swap a LoRA per request, so today roles
specialise by prompt only; the `adapter` field is the seam where a
`llama-server` backend + trained GGUF adapters plugs in later.

### All flags

| flag | effect |
|---|---|
| `--root PATH` | enable real-file tools, scoped to PATH |
| `--shell` | also allow `run_command` (PowerShell), still confirmed |
| `--yolo` | skip confirmation prompts |
| `--tiers` | route model calls through the tiered router |
| `--small TAG` | give routing/verify a cheaper model (implies `--tiers`) |
| `--deep TAG` | set the on-demand heavy tier (implies `--tiers`) |
| `--max-calls N` | LLM call budget (default 14 simulated, 40 with `--root`) |

Each folder keeps its own persistent state across runs:

- `workspace/state.json` — inbox, calendar, sent mail, chat messages, reminders
  (seeded with demo fixtures on first run, then evolves)
- `workspace/files/` — real `.pptx` / `.xlsx` files the agent creates
- `memory/memory.jsonl` — the agent's long-term memory ("learning"); tell it
  "remember that ..." and it applies it in later runs
- `logs/` — full transcript of every run

All five share the harness engine in `..\..\harness\` (few-shot tool docs,
constrained JSON decoding, call repair, planning, loop-breaking, verifier,
memory injection). Delete `workspace/state.json` and `memory/memory.jsonl` to
factory-reset an agent.

Speed expectations on this machine (Snapdragon X Elite, CPU inference):
1B/3B are interactive, 8B takes tens of seconds per step, 14B is slow, and
32B is minutes per step — it fits in 32 GB RAM but is patience-testing.
