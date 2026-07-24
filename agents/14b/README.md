# Agent 14B — `qwen2.5:14b`

The first non-Llama agent in the set. Same code, same tools, same harness as the
other four folders; only `config.json` differs.

```json
{ "name": "Agent 14B", "model": "qwen2.5:14b", "num_ctx": 8192 }
```

**Why Qwen.** The modern Llama family has no 14B — sizes go 1B → 3B → 8B → 70B.
Qwen 2.5 is the standard local choice at this size, so the size sweep stays
continuous even though the family changes here. Anything in the results that
looks like a step change between 8B and 14B has two candidate explanations, not
one; keep that in mind when reading the curve.

Everything is on-device. Inference goes to the local Ollama server at
`127.0.0.1:11434` (weights under `C:\Users\Lab User\SAIL\ollama`); the runner
asserts the endpoint is loopback and refuses anything else. All state stays in
this folder. Nothing leaves the machine.

```powershell
cd agents\14b
.\run.ps1 "Find a free hour on Thursday and book it as Deep work"
```

---

## 1. How the system is put together

The model is not the agent. The model is one component inside a loop that
supplies the structure, the checking, and the memory.

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

**Repetition handling.** Models fall into loops, and a repeated exchange sitting
in the context is itself the attractor pulling them back. So the harness does
two things: it refuses to re-execute the duplicate, and it *deletes the older
copy of the exchange from the message list* before restating the task. Two
`think` calls in a row also earn a "stop thinking and act" nudge.

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
distraction — leaving the file tools plus `think`, `save_memory`,
`recall_memories`, `done`.

Guardrails in [`harness/fs_tools.py`](../../harness/fs_tools.py): every path is
resolved against the root and must stay inside it (`..\`, absolute paths and
`%VAR%` expansion are all blocked after resolution); a deny-list keeps
`Windows\`, `Program Files\`, the Python interpreter, the Ollama model blobs and
this project's `results\` and `harness\` unwritable even if the root is a drive
root; overwrite, delete, move and shell each prompt for y/n confirmation, and a
declined action comes back as an error telling the model not to retry it.
`--yolo` turns the prompts off.

The confirmation prompts matter more here than at 8B, not less — a slow model
means a wrong `delete_path` costs you a long run as well as the file.

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

Quirk worth knowing: the router's default `deep` tag is `qwen2.5:14b` — this
folder's own model. Run `--tiers` here with no other flags and all four roles
resolve to the same tag, so the tier machinery is pure accounting. Pass
`--deep qwen2.5:32b` if you want the heavy tier to mean something.

```powershell
.\run.ps1 --tiers --small llama3.2:3b "..."   # 3B plans and verifies; 2 models resident
.\run.ps1 --tiers --deep qwen2.5:32b "..."    # a genuinely heavier on-demand tier
```

`--small` is the flag that actually pays at this size. Planning and verification
are short structured calls; handing them to a 3B while the 14B drives cuts a
noticeable slice off wall-clock, at the cost of a second resident model.

Each role can name a LoRA `adapter`. Ollama's HTTP API cannot hot-swap a LoRA
per request, so today roles specialise by prompt only; the `adapter` field is
the seam where a `llama-server` backend plus trained GGUF adapters plugs in.

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

## 8. What to expect at 14B

Slow on CPU. Not interactive — start a task and go do something else. A full
`--root` run at the 40-call ceiling is a long wait.

Capability-wise this is the first size where the harness is mostly insurance
rather than load-bearing. Format repair and loop-breaking almost never fire;
the plan is usually a clean tool sequence on the first try; the verifier usually
agrees with `done`. The interesting question at this size is no longer "does the
scaffolding rescue it" but "what does the scaffolding still catch" — which is
what the transcripts in `logs/` are for. Grep them for `"kind": "repair"` and
`"kind": "verify"` and see how often each actually changed the outcome.

The counterpoint is cost: 14B here is slower per step than 8B is per *task*.
If you are choosing a size to run unattended rather than to measure, 8B with
`--tiers` is usually the better deal, and this folder is the control that shows
you what you gave up.
