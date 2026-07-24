# Agent 32B — `qwen2.5:32b`

The largest agent in the set, and the ceiling of what this machine will hold.
Same code, same tools, same harness as the other four folders; only
`config.json` differs.

```json
{ "name": "Agent 32B", "model": "qwen2.5:32b", "num_ctx": 8192 }
```

**Why Qwen.** No Llama exists at ~30B — the modern family is 1B / 3B / 8B / 70B,
and 70B does not fit here. Qwen 2.5 32B is the standard local choice at this
size. As with the 14B folder, the family changes at this end of the sweep, so a
jump in the results between the Llama sizes and the Qwen sizes has two possible
causes and should not be read as scale alone.

Everything is on-device. Inference goes to the local Ollama server at
`127.0.0.1:11434` (weights under `C:\Users\Lab User\SAIL\ollama`); the runner
asserts the endpoint is loopback and refuses anything else. All state stays in
this folder. Nothing leaves the machine.

```powershell
cd agents\32b
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
turns. At this size that trade is expensive in wall-clock: two of your fourteen
calls go to planning and verification before a single tool has run.

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

Do not use `--yolo` here casually. The default `--root` budget is 40 calls at
minutes per call — an unattended run that goes wrong goes wrong for a long time
before you notice.

---

## 6. Model tiers — the flag that matters most at this size

`--tiers` routes calls through
[`harness/model_router.py`](../../harness/model_router.py). Each call carries a
role — `driver` (chooses the next tool), `router` (planning), `verifier` (the
pre-`done` check) — and the default lineup points all three at this folder's
model, so **exactly one model stays resident in RAM**. That single-resident
default exists for exactly this case: 32B in 32 GB leaves no headroom for a
co-resident second model unless you ask for one.

```powershell
.\run.ps1 --tiers --small llama3.2:3b "..."   # 3B plans and verifies; 2 models resident
```

This is the highest-value configuration in the whole project. Planning and
verification are short, highly structured calls — a 3B does them competently —
and each one you move off the 32B saves minutes, not seconds. Watch the
`tier` lines printed at the end of a run and `logs/model_calls.jsonl` to see
where the wall-clock actually went.

Quirk worth knowing: the router's default `deep` tag is `qwen2.5:14b`, which is
*smaller* than this folder's base. The `deep` role is never auto-invoked, so it
costs nothing, but do not read the default lineup as "32B drives, something
bigger thinks" — there is nothing bigger available on this machine.

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

## 8. What to expect at 32B

Minutes per step on CPU. It fits in 32 GB RAM and it is patience-testing. A
single simulated-office task is a coffee break; a `--root` task at the full
budget can run for an hour. Nothing about this size is interactive.

Treat this folder as the **upper bound of the experiment, not a daily driver**.
Its job is to answer "how much of the gap between 1B and competent is scale, and
how much is scaffolding?" — by showing what the same 14 tools and the same loop
produce when the model is no longer the weak link. Almost none of the repair
machinery fires here; the transcripts are short and boring, which is the result.

The practical reading of the size sweep: 3B for iterating, 8B for unattended
work, this for the ceiling measurement. If a task only succeeds at 32B, the
interesting follow-up is usually not "use 32B" but "what would the harness need
to add for 8B to get there" — which is the question
[`finetune/`](../../finetune/) and [`training_scripts/`](../../training_scripts/)
exist to attack from the other direction, by distilling this model's correct
tool calls into a LoRA for the small ones.
