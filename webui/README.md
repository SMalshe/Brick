# Agent Lab — run each model from a web page and watch it work

A local web console for the five per-model agents in [`../agents/`](../agents/).
Pick a model, type a task, press **Run**, and watch the loop happen live: the
plan, every model call streaming token by token, each tool call with the exact
arguments the harness sent, the verifier's verdict — and the agent's folder
(inbox, calendar, files, memory) updating on the right as it changes. No
PowerShell, no flags, no `cd` into a folder.

## Start it

**macOS** — double-click **`Agent Lab.command`** in the project root.
**Windows** — double-click **`Agent Lab.bat`**.

Either one installs the three Python packages the first time (`requests`,
`python-pptx`, `openpyxl`), makes sure Ollama is up, starts the server, and
opens the browser. Or start it by hand:

```bash
python -m webui.server        # then open http://127.0.0.1:8765
```

It binds loopback only and talks to the same local Ollama at `127.0.0.1:11434`
the agents already use — nothing leaves the machine.

## What you see

- **Left — Models.** One card per folder (1B → 32B) with a speed hint and its
  run/file/memory counts. A model that isn't downloaded yet has a **Get it**
  button that pulls it (with a progress bar) right there. Below is an
  **Options** drawer for the same switches the command-line runner has: a real
  working folder (`--root`), shell, skip-confirmations, keep-office-tools, the
  tiered router, and a custom call budget.
- **Middle — the run.** A card per step. Model output streams in with a
  blinking cursor; the model's `thought` is pulled out and shown plainly, the
  raw JSON tucked behind a disclosure. Tool calls show their arguments and
  result; harness interventions (a repaired call, corrective feedback, a
  loop-break, the verifier saying "not done yet") are called out in colour so
  you can *see* the scaffolding doing its job. A destructive action in
  real-folder mode pauses the run with **Allow / Deny** buttons.
- **Right — the workspace.** The agent's folder as a live tree: files it has
  created, its inbox and calendar, messages, reminders, sent mail, what it has
  learned, and past run transcripts. Anything that changes mid-run flashes
  **new**. Click a `.pptx` or `.xlsx` to see it rendered in the browser (or
  download it); click an email or a past run to read it. `⤢` opens the real
  folder on disk; `↺` factory-resets that agent (fixtures back to default,
  created files gone, memory wiped — transcripts kept).

One run at a time (one model in RAM at a time), each in its own subprocess, so
**Stop** always works.

## How it hooks in

The harness gained three optional observation hooks, all `None` by default:
`llm.STREAM_HOOK`, `agent.EVENT_HOOK`, `tools.TOOL_HOOK`. The benchmark never
sets them, so raw-vs-harness scoring and the non-streamed LLM path are
byte-for-byte unchanged. Only [`runner.py`](runner.py) installs them, to narrate
one run as a JSONL event stream; [`server.py`](server.py) fans that stream out
to the browser over SSE and serves the agents' state read-only. The run itself
is the very same `run_harness` over the very same `config.json`, workspace, and
memory as `agents/<size>/run_agent.py` — this is a window onto the agents, not a
second implementation of them.
