# Architecture

A complete walkthrough of the system: what it claims, how every module works,
how control flows through one episode, and where the seams and gaps are.

For the benchmark's tasks, metrics and methodology, see
[`bench/README.md`](bench/README.md). This document covers everything else.

---

## 1. The claim

> For small local models doing office-agent work, most of the useful capability
> comes from scaffolding, not from parameters.

Every design decision follows from wanting to *measure* that rather than assert
it. The repo runs the same model, against the same tools, in the same world,
under two conditions — `raw` (naive tool wiring) and `harness` (the scaffolding
under test) — and reports the delta. Model size is the second axis, not the
point.

Everything runs on-device. Inference goes to a local Ollama server at
`127.0.0.1:11434`; the agent runners `assert` the endpoint is loopback and
refuse to start otherwise. No cloud service is involved anywhere in the system.

---

## 2. Repo map

```
harness/            the engine — shared by every entry point
  llm.py              Ollama chat client (temp 0, seed 42, usage counters, optional streaming)
  model_router.py     tiered role→model router with a LoRA seam
  world.py            simulated office: inbox, calendar, messages, reminders + fixed clock
  office.py           REAL .pptx / .xlsx creation (python-pptx, openpyxl)
  memory.py           persistent cross-episode memory (JSONL + keyword retrieval)
  tools.py            the 14-tool registry, validation, execution
  fs_tools.py         REAL filesystem tools — opt-in, never part of the benchmark
  agent.py            the two agent loops: run_raw() and run_harness()

bench/              the experiment
  tasks.py            12 tasks, each with a programmatic grader
  grade.py            graders — reopen real files, inspect world state
  run_bench.py        resumable models × conditions × tasks matrix runner
  report.py           aggregate results.json → markdown + summary.json

agents/             per-model on-device agents (1b, 3b, 8b, 14b, 32b)
  _shared/run_agent.py  the CLI runner — all five folders hold identical copies
  <size>/config.json    the only per-agent difference

webui/              "Agent Lab" — local web console (in progress)
  server.py           stdlib HTTP + SSE, spawns one runner subprocess per run
  runner.py           runs an agent, narrates it as a JSONL event stream
  static/             index.html + style.css  (app.js not yet written)

finetune/           tool-calling LoRA data generation (imports the live harness)
training_scripts/   shippable-to-HPC training package (self-contained)
```

---

## 3. Entry points

Four ways in, all sharing `harness/`:

| entry point | command | uses | model selection |
|---|---|---|---|
| Benchmark | `python -m bench.run_bench --models ... --conditions raw harness` | `run_raw` + `run_harness` | plain `LLM`, one tag |
| Agent CLI | `agents\<size>\run.ps1 "task"` | `run_harness` only | `LLM`, or `ModelRouter` with `--tiers` |
| Agent Lab | `python -m webui.server` | `run_harness` only, hooks installed | same, chosen in the browser |
| Training | `bash training_scripts/run.sh` | none (offline) | trains a LoRA for `llama3.2:1b` |

Only the benchmark ever runs `run_raw`. `raw` exists to be the control, not to
be shipped.

---

## 4. The harness, module by module

### 4.1 `llm.py` — the Ollama client

A thin wrapper over `POST /api/chat`. Deliberately minimal.

```python
LLM(model, num_ctx=8192, temperature=0.0, timeout=900, keep_alive="30m")
.chat(messages, force_json=False, num_predict=700, role=None, keep_alive=None)
```

- **Determinism** — `temperature=0.0` and a hardcoded `seed: 42` on every
  request, so a rerun reproduces a run exactly.
- **`force_json`** sets Ollama's `format: "json"`, which constrains decoding at
  the grammar level. This is the harness's single highest-leverage mechanism and
  is off in the `raw` condition.
- **`keep_alive`** controls model residency. `"30m"` keeps weights warm between
  calls; `"0"` tells Ollama to evict immediately after the response — the basis
  of the router's RAM strategy.
- **`role`** is accepted but only labels stream events here. It exists so a
  plain `LLM` is drop-in interchangeable with `ModelRouter`, which *does* select
  a model from it.
- **Counters** — `calls`, `prompt_tokens`, `output_tokens`, `wall` accumulate
  per episode. `calls` is what the agent loops check against `MAX_CALLS`.
- **`STREAM_HOOK`** (module global, `None` by default) — when installed, the
  request switches to `stream=True` and each chunk is handed to the hook, with
  the final chunk carrying usage. Sampling parameters are unchanged, so a
  streamed run and a non-streamed run produce the same text.

### 4.2 `model_router.py` — tiered model selection

A drop-in replacement for `LLM` exposing the same `.chat()` signature and the
same counters, so `run_harness()` accepts either object unchanged.

Each call carries a `role`; each role maps to a tier — a model tag, sampling
settings, a `keep_alive`, and an optional `adapter`:

| role | dispatched from | purpose |
|---|---|---|
| `driver` | [`agent.py:356`](harness/agent.py#L356) | picks the next tool call |
| `router` | [`agent.py:296`](harness/agent.py#L296) | the planning step |
| `verifier` | [`agent.py:451`](harness/agent.py#L451) | the pre-`done()` check |
| `deep` | *nothing* | declared, never invoked |

The default lineup deliberately **collapses** the interactive tiers onto one
base tag, so exactly one model stays resident. The heavy `deep` tier is marked
`on_demand` with `keep_alive: "0"`, so it can never co-reside for longer than a
single request. `--small TAG` splits routing/verify onto a cheaper model at the
cost of a second resident model.

Bookkeeping: clients are cached by `(model, keep_alive)`; every call appends a
record (role, model, adapter, tokens, latency) to `call_log` and optionally to
`logs/model_calls.jsonl`; `usage_by_role()` aggregates it. Usage sums across
tiers, which is what keeps the `MAX_CALLS` budget honest when tiers are on.

**The LoRA seam is a seam, not a feature.** The per-role `adapter` field is
recorded in the call log and never applied — Ollama's HTTP API cannot hot-swap
a LoRA per request. `adapters_note()` states this plainly. A `llama-server`
backend with `/lora-adapters` is the intended path, and connects directly to the
adapter produced by `training_scripts/`.

### 4.3 `world.py` — the simulated office

State the agent perceives and mutates: 10 seeded emails, 7 seeded calendar
events, plus sent mail, chat messages and reminders that start empty.

- **Fixed clock** — `SIM_TODAY = 2026-07-20` (a Monday). This is what makes
  "book it Tuesday at 2pm" a gradeable instruction rather than a moving target.
- **Two lifetimes** — `World(workdir, persistent=False)` reseeds from fixtures
  every episode (the benchmark). `persistent=True` loads `state.json` and
  evolves across runs (the agent folders).
- **`ToolError`** — the single exception type carrying a message written *for
  the model*. Every validation failure raises one, which is why tool errors read
  like instructions: `"end_time (13:00) must be after start_time (14:00)"`.
- **Validators** — `_check_date` / `_check_time` enforce `YYYY-MM-DD` and 24-hour
  `HH:MM` at the world boundary, in both conditions. The harness's normalizer
  runs *before* this; `raw` gets the error instead.
- **Bookkeeping** — `log()` records every call (tool, args, ok, truncated
  result) into `actions`; `snapshot()` writes `state.json`. `actions` is what
  the verifier reads and what graders inspect.

### 4.4 `office.py` — real documents

Not simulated. `python-pptx` and `openpyxl` write genuine files that graders
reopen and a human can double-click.

- A **first slide with no bullets** becomes a title slide (layout 0); everything
  else is title+content (layout 1) with 20pt bullets.
- Spreadsheet cells whose string starts with `=` become **real formulas** —
  and the grader evaluates simple `=SUM()` ranges, so a formula total earns the
  same credit as a literal.
- `_resolve()` takes `os.path.basename()` of the model-supplied filename and
  auto-appends the extension, so a path-shaped filename can't escape the files
  directory.

### 4.5 `memory.py` — learning across episodes

Append-only JSONL, one `{"fact": ...}` per line, plus keyword-overlap retrieval:
tokenize, drop stopwords, score by set intersection size, return the top *k*.

No embeddings, deliberately — retrieval must behave identically for a 1B and a
32B model, or it becomes a confound in the size comparison. The cost is real:
retrieval is sensitive to *how the model phrased the fact it saved* (see the
`learn_use` caveat in [`bench/README.md`](bench/README.md)).

### 4.6 `tools.py` — the registry

14 tools, each a dict of `desc`, `params` (`{name: (type_description, required)}`),
`example`, and `run`:

| group | tools |
|---|---|
| email | `list_emails`, `read_email`, `send_email` |
| calendar | `list_events`, `add_event` |
| comms | `send_message`, `set_reminder` |
| documents | `create_presentation`, `create_spreadsheet`, `read_spreadsheet` |
| cognition | `think` |
| memory | `save_memory`, `recall_memories` |
| control | `done` (handled by the loop, `run` is `None`) |

Three functions carry the experiment:

- **`tool_docs(with_examples)`** renders the prompt block. The `with_examples`
  flag *is* harness mechanism #1 — `raw` gets `False`.
- **`validate_call(name, args)`** returns a list of human-readable problems
  (missing required params, unknown params). Only the harness calls it, and it
  runs **before** execution.
- **`execute(name, args, world, mem)`** returns `(ok, observation)`. Identical
  in both conditions. Its error taxonomy is the important part: unknown tool →
  the valid list; `ToolError` → the world's message; `KeyError` → "missing
  required parameter"; any other exception → `repr()`. **A tool bug never kills
  the episode** — it comes back as an observation the model can react to.

`TOOL_HOOK` (module global, `None` by default) fires after every executed call
with the arguments *as actually run* — i.e. post-repair, post-normalization.

### 4.7 `fs_tools.py` — real filesystem access

Opt-in, and structurally excluded from the benchmark: nothing in `bench/`
imports this module, and `enable()` mutates the shared `TOOLS` dict **in that
process only**.

Eight tools — `list_dir`, `read_file`, `write_file`, `append_file`,
`delete_path`, `move_path`, `search_files`, and `run_command` (PowerShell, gated
behind `--shell`).

The scoping model mirrors Claude Code / Codex:

| layer | behavior |
|---|---|
| **root** | every path resolves against it; `..`, absolute paths and `%VAR%` expansions are re-checked and rejected if they escape |
| **deny-list** | Windows dirs, Program Files, the Ollama blobs, the Python interpreter, and the project's own `results/` and `harness/` are never writable — even at a drive root |
| **confirm** | overwrite / delete / move / shell route through a callback; a decline raises a `ToolError` telling the model *not to retry* |

Limits: 200 KB read cap, 4 KB output clip, 300 directory entries, 60 s command
timeout, NUL-byte binary detection.

`restrict_to_files()` drops the simulated-office tools entirely, keeping only
the file tools plus `think` / `save_memory` / `recall_memories` / `done` — a
real-folder agent shouldn't be distracted by a fake inbox, which is a known
attractor for small models.

The module docstring states the risk honestly: *"A 1B model that scores 0.3 on
'put the right number in a spreadsheet' will eventually issue a wrong
delete_path. Choose root accordingly."*

### 4.8 `agent.py` — the two loops

The centerpiece. Module constants: `MAX_CALLS = 14`, `OBS_LIMIT = 2000`, and
`SHAPE` — the response format, kept **abstract on purpose** because concrete
example content in an instruction becomes an attractor that 1B models copy
verbatim. Real examples live per-tool in the docs.

#### `run_raw()` — the control

System prompt (tool list, no examples) → user task → loop: `chat` with
`force_json=False`, `parse_strict`, execute, feed back `OBSERVATION:` or a
verbatim error. Stops on `done` or budget exhaustion. That is the whole thing.

#### `run_harness()` — the treatment

The same skeleton plus ten mechanisms:

| # | mechanism | implementation |
|---|---|---|
| 1 | few-shot example per tool | `tool_docs(with_examples=True)` |
| 2 | grammar-constrained decoding | `force_json=True` → Ollama `format: json` |
| 3 | lenient JSON extraction | `parse_lenient` — fence strip, brace matching, trailing-comma repair |
| 4 | deterministic call repair | `repair_args` — `difflib` rename at cutoff 0.5, substring fallback, drop unknowns; plus top-level args lifted into `args` |
| 5 | schema validation w/ corrective feedback | `validate_call` + the tool's own `example` quoted back; unknown tool names get a `get_close_matches` suggestion |
| 6 | date/time normalization | `normalize_date` / `normalize_time` — `"2pm"`→`14:00`, `"tomorrow"`/`"next tuesday"`/`"July 24"`/`"7/24"` → ISO against the simulated clock |
| 7 | tool-grounded planning | `plan_step` — a JSON list of tool names; invalid names dropped, so free prose never enters context |
| 8 | loop-breaking | `seen_calls` signature + `world_version`; duplicate exchanges deleted from `messages` |
| 9 | verifier before `done()` | `_verify` — up to 2 rounds |
| 10 | memory auto-injection | `mem.search(task_text, k=3)` into the system prompt |

Four of these deserve detail, because they encode non-obvious findings about
small models:

**Planning that can't hallucinate (#7).** The plan request is appended, answered,
then `messages.pop()` removes it. Steps whose `tool` isn't in `TOOLS` are
discarded. What survives re-enters as *"Suggested tool sequence (adapt if the
results demand it)"* — guidance, never prose the model then obeys blindly.

**Loop-breaking with context surgery (#8).** Each executed call is fingerprinted
as `{tool, args}` against a `world_version` counter that increments only on
successful **writes**. An identical call while the world is unchanged is not
re-executed; instead the agent is told the result is already above and asked for
the *next* step, with the task restated. Repeated reads become legal again the
moment anything is written. Critically, if the bad reply is a verbatim repeat,
the older assistant/user pair is **deleted from `messages`** — repetition in
context is an attractor, so the fix is removal, not more instruction. The same
surgery happens in `give_feedback()` for unparseable replies.

**A verifier that reads actions, not transcripts (#9).** `_verify()` doesn't
hand the message history to the checker. It rebuilds a clean summary from
`world.actions` — `- add_event({...}) -> ok` — and asks for
`{"complete": bool, "missing": str}`. If the verdict is incomplete, `done()` is
rejected and the loop continues with the missing piece named. It fails **open**:
any exception or unparseable verdict returns `{"complete": True}`, so a flaky
verifier can't trap a finished agent in a loop.

**Anti-stalling.** `think_streak` counts consecutive `think` calls; at 2, the
observation gains `"NOTE: stop thinking and take a concrete action now."`

#### Late-binding globals

`SIM_TODAY`, `SIM_TODAY_HUMAN`, `MAX_CALLS`, `EXTRA_RULES` and
`EXTRA_WRITE_TOOLS` are read at *call* time, not import time, so a runner can
rebind `agent_mod.SIM_TODAY = date.today()` and point the same loop at the real
clock. The benchmark leaves all of them alone.

---

## 5. Anatomy of one harness episode

```
run_harness(llm, world, mem, task_text)
 │
 ├─ mem.search(task_text, k=3)          matches only — never a recency fallback
 ├─ build system prompt                 rules + tool docs w/ examples + memories + EXTRA_RULES
 │
 ├─ CALL 1  role=router                 "which tools, in order?"
 │    └─ parse → drop invalid names → plan text;  the request is popped from context
 │
 ├─ append: TASK + suggested sequence + "make the first tool call now"
 │
 └─ loop while llm.calls < MAX_CALLS:
      ├─ CALL n  role=driver  force_json=True
      ├─ parse_lenient
      │    └─ fail → give_feedback(FORMAT ERROR + SHAPE), maybe delete the repeat, continue
      ├─ name/args extraction (args lifted from top level if absent)
      ├─ if done:
      │    ├─ CALL n+1  role=verifier   (≤2 rounds, budget permitting)
      │    │    └─ incomplete → feedback naming what's missing, continue
      │    └─ complete → finish
      ├─ repair_args   → note "renamed 'when' -> 'date'; dropped unknown 'notes'"
      ├─ normalize_args → "2pm" becomes "14:00"
      ├─ validate_call
      │    └─ problems → feedback quoting the tool's own example, continue
      ├─ duplicate check (signature vs world_version)
      │    └─ duplicate → prune context, restate task, continue  [no execution]
      ├─ execute → world.log + TOOL_HOOK;  world_version++ on a successful write
      └─ append "OBSERVATION: ..."  (truncated at OBS_LIMIT)
```

Every step calls `ep.note(kind, content)`, which appends to the transcript and
fires `EVENT_HOOK` — this is why the web UI can narrate a run without the loop
knowing it's being watched.

---

## 6. The benchmark

12 tasks, 71 boolean checks, graded 100% programmatically — graders reopen the
generated `.pptx`/`.xlsx` and inspect live world state; no LLM grades anything.
Task score is `passed / total`; the headline metric is the mean over 12 tasks
per `(model, condition)` with the **raw → harness delta**.

Alongside the score, four mechanism counters — `parse_failures`,
`invalid_calls`, `tool_errors`, `llm_calls` — show *how* the harness earns its
delta rather than just that it does.

Full detail, ground-truth values, grader tolerances and methodology caveats:
**[`bench/README.md`](bench/README.md)**.

---

## 7. Per-model agents

`agents/1b|3b|8b|14b|32b/` — five self-contained agents. All five
`run_agent.py` files are **byte-identical** to `_shared/run_agent.py`; only
`config.json` differs (`name`, `model`, `num_ctx`).

Per-folder persistent state:

```
workspace/state.json    inbox, calendar, sent mail, messages, reminders
workspace/files/        real .pptx / .xlsx the agent created
memory/memory.jsonl     long-term memory — "remember that ..." persists
logs/run_NNN.json       full transcript per run
logs/model_calls.jsonl  per-tier usage, when --tiers is on
```

Flags: `--root PATH` (real files), `--shell`, `--yolo`, `--with-office`,
`--tiers`, `--small TAG`, `--deep TAG`, `--max-calls N`.

Two behaviors switch on `--root`: the call budget defaults to 40 instead of 14,
and the clock switches from the simulated Monday to the **real** date — a
real-file agent should reason about today.

Sizes run 1B → 32B. On the target machine (Snapdragon X Elite, CPU inference)
1B/3B are interactive, 8B is tens of seconds per step, and 32B is minutes per
step.

---

## 8. Agent Lab (`webui/`)

A local console for watching an agent work. Loopback-only stdlib HTTP server,
no framework, no external JS.

**Process model.** One run at a time, in a subprocess. `server.py` spawns
`python -m webui.runner`, reads its stdout line by line, and fans each JSON line
out to SSE subscribers. This isn't incidental — the harness has process-global
switches (`TOOLS`, `EXTRA_RULES`, `_ROOT`), so a subprocess per run is what
keeps two configurations from colliding, and it makes Stop always work.

**`runner.py`** is `run_agent.py` plus the three observation hooks, emitting a
JSONL event stream:

| event | payload |
|---|---|
| `banner` | agent, model, budget, endpoint, root, toolset, tiers, tool list |
| `llm_start` / `token` / `llm_end` | one model call, streamed as it's written |
| `note` | a transcript entry — plan, model, observation, feedback, repair, verify, done |
| `tool` | an executed call with post-repair arguments and its result |
| `world` | full folder snapshot — inbox, calendar, files, memory, real-file tree |
| `confirm` | a destructive action awaiting y/n; the answer arrives on **stdin** |
| `end` | finished, summary, usage, per-role breakdown, actions taken |
| `error` | the run died, with traceback tail |

Confirmations are the neatest part: `Confirmer.__call__` emits a `confirm` event
and blocks on `sys.stdin.readline()` until the server writes `{"id", "allow"}`
back — the same callback contract `fs_tools` uses for a terminal prompt.

The server also renders generated `.pptx`/`.xlsx` in-page (`/api/preview`),
streams `ollama pull` progress so "run the 14B" never means leaving the page,
and offers scoped resets (`world` / `memory` / `files` / `logs`).

**Status: incomplete.** `static/index.html` loads `/static/app.js`, and that
file does not exist. `style.css` is finished at 19 KB. The backend is complete;
the front-end controller is not.

---

## 9. Fine-tuning track

The next experiment: does a tool-calling LoRA move the 1B model the way the
harness does?

**Data.** `(context → correct tool-call JSON)` pairs in chat format. Because
the generator chooses the slot values, the target call is correct **by
construction** — no verification, no LLM judge. Every row embeds the real
harness system prompt, so the adapter trains on exactly the context it will see
at inference. ~15% of rows are multi-turn *"recover after an ERROR"* traces —
bad call → error observation → fixed call — teaching in-weights what mechanism
#4 currently does deterministically.

The 12 benchmark tasks are **deliberately excluded** to keep them held out.

Two generators, same output shape:

- [`finetune/gen_toolcall_data.py`](finetune/gen_toolcall_data.py) imports the
  live harness, so prompts can never drift from serving.
- [`training_scripts/make_data.py`](training_scripts/make_data.py) is stdlib-only
  and reads a frozen `system_prompt.txt`, so the training package regenerates its
  own data on a machine that has never seen this repo.

Both ship 1,200 rows.

**Training.** `train_lora.py` — transformers + peft, rank 16 / alpha 32 on all
attention and MLP projections, **assistant-only loss** (every non-assistant
token masked to -100), 2560-token sequences. Four delivery paths for whatever
the compute center supports: Apptainer/Slurm, Docker, bare GPU node, or local.
The base model and llama.cpp are baked into the image so the GPU node runs
fully offline with no HF token — the default base is an ungated mirror with the
same weights Ollama's `llama3.2:1b` serves, which is what makes the adapter
valid against the GGUF served at home.

**The experiment.** Serve the adapter with `llama-server --lora`, point the
harness at it, rerun the 12 held-out tasks: `1B` vs `1B+adapter`. That delta is
the result. It also gives `model_router.py`'s `adapter` field something real to
carry.

---

## 10. Design invariants

Rules the codebase holds itself to. Worth preserving when extending it.

**Benchmark comparability is sacred.** Results already on disk must stay
comparable with results produced tomorrow. Hence: `bench/` never imports
`fs_tools`; `EXTRA_RULES` and `EXTRA_WRITE_TOOLS` are empty during grading, so
the graded system prompt is byte-identical to earlier runs; all three
observation hooks default to `None`; and streaming leaves sampling untouched.
**New capability should arrive as a new condition, not as a change to
`run_harness`.**

**Both conditions share everything but scaffolding.** Same tools, same error
strings, same world, same clock, same budget, same seed. If a change touches
one loop, ask what it does to the other.

**The budget covers meta-calls.** Plan and verify come out of the same 14. The
harness cannot buy accuracy with extra inference.

**Tool errors are written for the model.** Every `ToolError` names the field,
the expected format, and the value it got. Error text is part of the interface.

**Nothing leaves the machine.** Loopback assertions in both runners.

**Repetition is treated as a failure mode, not noise.** The loop deletes
repeated context rather than instructing against it — the one small-model
finding most visible in the code.

---

## 11. Process-global state

Extending this system means dealing with module globals that runners mutate at
startup. They're a deliberate trade (they keep the loop signature clean) with a
real cost (two differently-configured agents cannot share a process).

| global | module | mutated by |
|---|---|---|
| `TOOLS` | `tools.py` | `fs_tools.enable()`, `fs_tools.restrict_to_files()` |
| `MAX_CALLS` | `agent.py` | both runners, from flags/config |
| `EXTRA_RULES`, `EXTRA_WRITE_TOOLS` | `agent.py` | both runners, on `--root` |
| `SIM_TODAY`, `SIM_TODAY_HUMAN` | `agent.py` | both runners, on `--root` |
| `_ROOT`, `_ALLOW_SHELL`, `_CONFIRM` | `fs_tools.py` | `enable()` |
| `EVENT_HOOK` / `TOOL_HOOK` / `STREAM_HOOK` | `agent` / `tools` / `llm` | `webui/runner.py` only |

`webui/server.py` already works around this with a subprocess per run. Any
feature needing two agents with **different toolsets in one process** — most
obviously a specialist handoff — requires making the registry a parameter first,
which touches `tool_docs`, `validate_call`, `execute` and `repair_args`.

---

## 12. Current state

**Committed:** one commit (`a84735b Agent`).

**Uncommitted** — the observation-hook seam, four files:

- `llm.py` — `STREAM_HOOK` + `_chat_streamed()`; streams only when a hook is installed
- `agent.py` — `EVENT_HOOK`, fired from `Episode.note`
- `tools.py` — `TOOL_HOOK`, via an `execute` / `_execute` split
- `model_router.py` — a one-line fix: `role` was not being forwarded to
  `llm.chat`, so router-tier calls mislabeled their stream events

All are `None`-default and benchmark-neutral by construction.

**Untracked:** `webui/` and five per-agent `README.md` files.

### Known gaps

| gap | detail |
|---|---|
| `webui/static/app.js` missing | `index.html:110` loads it; the UI won't run until it exists |
| `deep` tier unreachable | fully specified, nothing dispatches `role="deep"`; needs an escalation policy |
| LoRA `adapter` inert | recorded in the call log, never applied; blocked on a `llama-server` backend |
| router unmeasured | `run_bench.py` builds `LLM` directly, so tiering is never benchmarked |
| `summary.json` unconsumed | `report.py` writes it "for the HTML report"; no HTML generator exists |
| no results committed | `results/` is gitignored and absent — the repo defines the benchmark, ships no numbers |

### Portability

The repo is Windows-authored: hardcoded `C:\Users\Lab User\SAIL\` paths in
`run.ps1` and the `fs_tools` deny-list, `powershell.exe` in `_run_command`, and
backslash paths in the file-tool examples. The harness core, the benchmark and
the training package are portable; the agent launchers and real-file mode are
not. On macOS/Linux, `python -m bench.run_bench` and `python -m webui.server`
work, and `--root` mode needs a platform-appropriate deny-list plus a shell
other than PowerShell.
