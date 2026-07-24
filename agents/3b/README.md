# Agent 3B — `llama3.2:3b`

The small-but-usable agent. Same code, same tools, same harness as the other
four folders; only `config.json` differs.

```json
{ "name": "Agent 3B", "model": "llama3.2:3b", "num_ctx": 8192 }
```

Everything is on-device. Inference goes to the local Ollama server at
`127.0.0.1:11434` (weights under `C:\Users\Lab User\SAIL\ollama`); the runner
asserts the endpoint is loopback and refuses anything else. All state stays in
this folder. Nothing leaves the machine.

```powershell
cd agents\3b
.\run.ps1 "Find a free hour on Thursday and book it as Deep work"
```

---

## 1. How the system is put together

The model is not the agent. The model is one component inside a loop that does
the parts a 3B model does not do reliably on its own.

```
run.ps1
  └─ run_agent.py            this folder: config, flags, state paths, banner
       ├─ harness/llm.py         Ollama client (temp 0, seed 42, usage counters)
       │   or model_router.py    tiered variant, one model resident (--tiers)
       ├─ harness/world.py       simulated office: inbox, calendar, messages,
       │                         reminders — persisted to workspace/state.json
       ├─ harness/office.py      REAL .pptx / .xlsx writing (python-pptx, openpyxl)
       ├─ harness/fs_tools.py    REAL file tools, opt-in via --root
       ├─ harness/memory.py      long-term memory (JSONL + keyword retrieval)
       ├─ harness/tools.py       the tool registry + validation
       └─ harness/agent.py       run_harness(): the loop described below
```

`run_agent.py` in this folder is byte-identical to `agents/_shared/run_agent.py`
and to the copy in every other size folder. It:

1. reads `config.json`, asserts the Ollama URL is local,
2. parses flags, decides the LLM call budget (14 simulated, 40 with `--root`),
3. opens `workspace/` as a **persistent** world and `memory/memory.jsonl`,
4. builds either a plain `LLM` or a tiered `ModelRouter`,
5. calls `run_harness(llm, world, mem, task)`,
6. prints what happened and writes `logs/run_NNN.json`.

Determinism: `temperature=0`, `seed=42`, `num_ctx` from config. Two runs of the
same task against the same state produce the same trajectory.

---

## 2. The loop, step by step

`run_harness()` in [`harness/agent.py`](../../harness/agent.py). One tool call
per model reply, JSON only.

**Setup.** Relevant long-term memories are retrieved (`mem.search(task, k=3)`,
keyword overlap, matches only — never a recency fallback) and injected into the
system prompt. The prompt carries the response shape, the rules, and the full
tool docs *with a worked example per tool*.

**Plan.** One call asks for a tool-grounded plan: `{"steps":[{"tool":...,
"what":...}]}`. Every step naming a tool that does not exist is dropped, and
the plan re-enters the context as short numbered guidance. The plan *request*
is popped from the context so the model never sees its own planning prose again.
Free-form prose is never allowed to become an instruction the model then obeys.

**Act.** Then, until `done` is accepted or the budget runs out:

| stage | what happens |
|---|---|
| decode | `format=json` — grammar-constrained, so the reply is JSON or nothing |
| parse | strict `json.loads`; on failure, fence-strip → brace-match → trailing-comma repair |
| repair | near-miss parameter names renamed onto missing required ones (`difflib`, cutoff 0.5); unknown parameters dropped; top-level args lifted into `args` |
| normalize | `date` → `YYYY-MM-DD` ("tomorrow", "next tuesday", "Jul 23", "7/23" all resolve against the clock); `time`/`start_time`/`end_time` → 24h `HH:MM` ("2pm" → `14:00`) |
| validate | missing/unknown parameters caught **before** execution; feedback quotes the tool's correct example |
| dedupe | an identical call against an unchanged world is not re-executed |
| execute | tool runs; result truncated to 2000 chars and fed back as `OBSERVATION:` |

**Finish.** When the model calls `done`, a **verifier** call re-reads the task
against the log of actions actually taken and answers
`{"complete": bool, "missing": str}`. If incomplete, `done` is rejected, the
gap is quoted back, and the loop continues. Up to two verify rounds. On any
verifier error it defaults to `complete: true` rather than trapping the agent.

**Repetition handling.** Small models fall into loops, and a repeated exchange
sitting in the context is itself the attractor pulling them back. So the harness
does two things: it refuses to re-execute the duplicate, and it *deletes the
older copy of the exchange from the message list* before restating the task.
Two `think` calls in a row also earn a "stop thinking and act" nudge.

**Budget honesty.** Plan, verify and every repair round are paid out of the same
`MAX_CALLS` counter as ordinary tool calls. The scaffolding does not get free
turns.

---

## 3. The tools

Simulated-office mode (the default) exposes 14 tools from
[`harness/tools.py`](../../harness/tools.py):

`list_emails` · `read_email` · `send_email` · `list_events` · `add_event` ·
`send_message` · `set_reminder` · `create_presentation` · `create_spreadsheet` ·
`read_spreadsheet` · `think` · `save_memory` · `recall_memories` · `done`

`create_presentation` and `create_spreadsheet` write **real** files — open the
`.pptx` / `.xlsx` in `workspace/files/` in PowerPoint or Excel. A cell string
beginning with `=` becomes a live formula.

The simulated clock is fixed at **Monday, 2026-07-20** so date reasoning is
reproducible. In `--root` mode the harness is switched to the real system date
instead.

---

## 4. State that survives between runs

| path | contents |
|---|---|
| `workspace/state.json` | inbox, calendar, sent mail, chat messages, reminders — seeded with demo fixtures on first run, then evolves |
| `workspace/files/` | the real `.pptx` / `.xlsx` the agent produced |
| `memory/memory.jsonl` | long-term memory; say "remember that ..." and later runs get it injected automatically |
| `logs/run_NNN.json` | full transcript: system prompt, plan, every model reply, repairs, observations, verdicts |
| `logs/model_calls.jsonl` | per-call tier/token/latency records (`--tiers` only) |

Delete `workspace/state.json` and `memory/memory.jsonl` to factory-reset this
agent.

---

## 5. Real-computer mode

`--root` swaps the fake office for real files under one folder, Codex /
Claude-Code style:

```powershell
.\run.ps1 --root "C:\Users\Lab User\Desktop\sandbox" "Tidy these notes into folders"
.\run.ps1 --root . --shell "What changed in this project today?"
```

Tools added: `list_dir`, `read_file`, `write_file`, `append_file`,
`delete_path`, `move_path`, `search_files`, and with `--shell`, `run_command`.
The simulated office tools are dropped in this mode — a fake inbox is a known
distraction for small models — leaving the file tools plus `think`,
`save_memory`, `recall_memories`, `done`.

Guardrails in [`harness/fs_tools.py`](../../harness/fs_tools.py): every path is
resolved against the root and must stay inside it (`..\`, absolute paths and
`%VAR%` expansion are all blocked after resolution); a deny-list keeps
`Windows\`, `Program Files\`, the Python interpreter, the Ollama model blobs and
this project's `results\` and `harness\` unwritable even if the root is a drive
root; overwrite, delete, move and shell each prompt for y/n confirmation, and a
declined action comes back as an error telling the model not to retry it.
`--yolo` turns the prompts off.

3B is the smallest size where real-folder work is worth attempting at all, and
it is still an open-loop model issuing destructive calls. Point `--root` at a
sandbox and leave the confirmations on until you have watched a few runs.

---

## 6. Model tiers

`--tiers` routes calls through
[`harness/model_router.py`](../../harness/model_router.py). Each call carries a
role — `driver` (chooses the next tool), `router` (planning), `verifier` (the
pre-`done` check) — and the default lineup points all three at this folder's
model, so **exactly one model stays resident in RAM**. A `deep` tier is declared
(`keep_alive: "0"`, evicted immediately after use) but nothing invokes it
automatically. Per-tier token and latency totals print at the end and append to
`logs/model_calls.jsonl`.

```powershell
.\run.ps1 --tiers "Summarise the README and write a one-line TODO file"
.\run.ps1 --tiers --small llama3.2:1b "..."   # 1B plans and verifies; 2 models resident
```

3B is itself the model the larger agents reach for with `--small`: cheap enough
that planning and verification cost little, capable enough that the plan is
usually a valid tool sequence. Running `--small llama3.2:1b` from here is the
opposite trade and rarely pays — the plan quality drops faster than the latency.

Each role can name a LoRA `adapter`. Ollama's HTTP API cannot hot-swap a LoRA
per request, so today roles specialise by prompt only; the `adapter` field is
the seam where a `llama-server` backend plus trained GGUF adapters plugs in.
See [`training_scripts/`](../../training_scripts/) — that trainer currently
targets Llama 3.2 1B; pointing `--base` at the 3B is a one-flag change if you
want the adapter for this size.

---

## 7. Flags

| flag | effect |
|---|---|
| `--root PATH` | enable real-file tools, scoped to PATH |
| `--shell` | also allow `run_command` (PowerShell), still confirmed |
| `--yolo` | skip confirmation prompts |
| `--tiers` | route model calls through the tiered router |
| `--small TAG` | cheaper model for routing/verify (implies `--tiers`) |
| `--deep TAG` | on-demand heavy tier (implies `--tiers`) |
| `--with-office` | keep the simulated office tools alongside the file tools |
| `--max-calls N` | LLM call budget (default 14 simulated, 40 with `--root`) |

---

## 8. What to expect at 3B

Still interactive on CPU. This is the best speed-to-competence trade in the set
and the default choice for iterating on a task before handing it to 8B.

Compared to 1B, format failures largely disappear — grammar-constrained decoding
plus this model's own JSON ability means the parser and the near-miss repair
fire much less often. What remains is *task*-level: dropped clauses in multi-part
instructions, a plausible-looking number that came from the wrong email, calling
`done` with one requirement unmet. The verifier and the look-before-you-act rule
are the mechanisms earning their keep at this size, not the JSON repair.

Two or three steps per task is comfortable. Anything with a "then also..." in it
is worth splitting or worth checking the transcript afterwards.
