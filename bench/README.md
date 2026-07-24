# The benchmark

What it measures, how it scores, and what it produces.

## The claim under test

> For small local models doing office-agent work, most of the useful capability
> comes from scaffolding, not from parameters.

The benchmark tests this by running the **same model** against the **same tools**
in the **same world** under two conditions, and measuring the gap. It is a
controlled A/B on the harness, not a model leaderboard — model size is the
second axis, not the point.

## What is held constant across conditions

Everything except the scaffolding:

| held constant | where |
|---|---|
| 14 tools, identical behavior and error strings | [`harness/tools.py`](../harness/tools.py) |
| seeded world (10 emails, 7 events), fresh per task | [`harness/world.py`](../harness/world.py) |
| simulated clock — Monday 2026-07-20 | `world.SIM_TODAY` |
| LLM call budget — 14 per task | `agent.MAX_CALLS` |
| temperature 0.0, seed 42, `num_ctx` 8192 | [`harness/llm.py`](../harness/llm.py) |
| observation truncation — 2000 chars | `agent.OBS_LIMIT` |

The budget constraint is the one that makes the comparison honest: the harness
spends its plan call (1) and verifier calls (up to 2) **out of the same 14**, so
it cannot buy accuracy with extra inference.

## The two conditions

**`raw`** — `run_raw()`. Tool list in one system prompt, strict `json.loads`,
tool errors fed back verbatim, no other help. This is the control: what "just
wire the model to tools" produces.

**`harness`** — `run_harness()`. The same loop plus ten mechanisms: per-tool
few-shot examples, `format=json` constrained decoding, lenient JSON extraction,
deterministic argument repair, pre-execution schema validation with corrective
feedback, date/time normalization, a tool-grounded plan step, loop-breaking with
context pruning, a verifier pass before `done()` is accepted, and auto-injection
of relevant memories.

Both live in [`harness/agent.py`](../harness/agent.py).

## Scoring

Three levels, all arithmetic — **no LLM grades anything**.

**1. Check** — one boolean assertion about the world or a generated file.
Graders in [`grade.py`](grade.py) reopen the actual `.pptx`/`.xlsx` with
python-pptx/openpyxl and inspect the live `World` object.

**2. Task score** — `passed_checks / total_checks`, a float in `[0, 1]`
(`tasks._score`). Partial credit is the norm.

**3. Aggregates** — [`report.py`](report.py) takes unweighted means over tasks,
per `(model, condition)` and per `(capability, model, condition)`.

Checks are appended **conditionally**: if the artifact doesn't exist, the
dependent checks are never added and the denominator shrinks. A missing file
still scores 0.0 (`0/1`), so this can't inflate a score — but it does mean the
denominator varies by run, and partial credit is only reachable once the
artifact exists.

## The 12 tasks

Run in fixed order. Tasks 10 and 11 are a deliberate pair: they run as separate
episodes sharing one memory file, which is the only real test of cross-episode
learning.

| # | task | capabilities | checks | what it grades |
|---|---|---|---|---|
| 1 | `pptx_basic` | powerpoint | 11 | 5 slides, exact titles in order, ≥3 bullets on slides 2–5 |
| 2 | `pptx_from_email` | powerpoint, email | 6 | reads Dana's email; the three real revenue figures appear |
| 3 | `xlsx_basic` | excel | 7 | 4 item rows with exact costs, a Total row, total = 7550 |
| 4 | `xlsx_from_email` | excel, email | 6 | finds 3 receipts among 10 emails; total = 729.80 |
| 5 | `email_reply` | email | 4 | picks the *most recent* Northwind email, replies to `mia@corp.com` |
| 6 | `cal_add` | calendar_write | 6 | "2pm to 3pm" → `14:00`/`15:00`, both attendees |
| 7 | `cal_freeslot` | calendar_write, thinking | 5 | reads Thursday, books a genuinely non-overlapping hour in 09:00–17:00 |
| 8 | `cal_brief` | calendar_read, messaging, thinking | 5 | messages Jordan all 3 Wednesday meetings, **in chronological order** |
| 9 | `remind_msg` | reminders, messaging | 6 | reminder at exactly 2026-07-24 15:00 + a message to Casey |
| 10 | `learn_store` | learning | 3 | saves the stated preferences to memory |
| 11 | `learn_use` | learning, calendar_write | 4 | **separate episode**: applies them unprompted (25 min, not before 10:00) |
| 12 | `multi_offsite` | email, calendar_write, powerpoint | 8 | one task, three artifacts: calendar + reply + deck |

**71 checks total** across the suite.

### Ground truth

Every expected value is a fact seeded in `world.py`, so correctness is decidable:

- Q3 revenue — West `$1,240,000`, East `$845,000`, Online `$610,000` (email `e2`)
- Receipts — CloudHost `$230.00`, OfficeMax `$87.50`, Delta `$412.30` → total `729.80`
- Offsite — Friday 2026-07-24, 09:00–16:00, Lakeside Pavilion (email `e8`)
- Northwind — two emails; `e7` from Mia (07-17) is newer than `e6` from Jordan (07-15)
- Wednesday 2026-07-22 — Design review 10:00, 1:1 with Sam 14:00, Marketing sync 15:00
- Thursday 2026-07-23 busy — 09:00–11:00, 12:00–13:00, 15:00–16:00 (free: 11–12, 13–15, 16–17)

Emails `e1` (newsletter), `e9` (HR), `e10` (promo) are distractors — retrieval
noise, not content.

### Grader tolerances

Deliberately forgiving on form, strict on fact:

- Filenames match by **case-insensitive stem**, so `Q3_Review.pptx` counts.
- Numbers are normalized before comparison — `$1,240,000` and `1240000` both pass.
- `=SUM(B2:B5)` formulas are **evaluated** by `_cell_number`, so a formula total
  earns the same credit as a literal.
- Calendar checks ignore the 7 seeded events by id, so only events the agent
  actually created are graded.

## Metrics recorded

[`run_bench.py`](run_bench.py) appends one record per `(model, condition, task)`
to `results.json` after **every task**, so an interrupted run is still reportable:

| field | meaning |
|---|---|
| `score` | fraction of checks passed, 0–1 |
| `checks` | every check with its pass/fail — the audit trail |
| `finished` | did the agent call `done()`, or run out of budget |
| `llm_calls` | inference calls used of `max_calls` |
| `parse_failures` | replies that weren't parseable JSON |
| `invalid_calls` | calls rejected by schema validation (harness only — `raw` has no pre-execution validation) |
| `tool_errors` | calls that executed and returned an error |
| `prompt_tokens` / `output_tokens` | token accounting |
| `wall_seconds` | latency |
| `error` | runner exception, if the episode crashed |

The last four failure counters are the *mechanism* metrics — they show **how**
the harness earns its score delta, not just that it does. A drop in
`parse_failures` is constrained decoding working; a nonzero `invalid_calls` with
a flat `tool_errors` is validation catching bad calls before they execute.

## Reports

```powershell
python -m bench.run_bench --models llama3.2:1b llama3.2:3b llama3.1:8b `
    --conditions raw harness --outdir results
python -m bench.report --outdir results
```

`report.py` prints and writes three markdown tables:

1. **Overall** — mean score and tasks-fully-passed per model, `raw` vs `harness`,
   with the **delta**. This is the headline number.
2. **By capability** — the 9 capability labels, mean over tasks carrying that tag.
3. **By task** — the full 12 × (models × conditions) grid.

### Artifacts on disk

```
results/
  results.json                      one record per (model, condition, task)
  summary.json                      aggregates
  SUMMARY.md                        the three tables
  <model>/<condition>/
    memory.jsonl                    shared across the run's 12 tasks
    <task>/
      transcript.md                 full episode: system, plan, every model reply,
                                    repairs, observations, feedback, verify, done
      state.json                    final world state
      files/                        the real .pptx / .xlsx the agent produced
```

The generated files are real Office documents — open them in PowerPoint/Excel.

## Methodology notes

Honest limits, for anyone reading the numbers.

**No results are committed.** `results/` is gitignored. The repo defines the
benchmark; it doesn't ship measurements. Every table above is a schema, not data.

**n = 1 per cell.** Temperature 0 and seed 42 make runs reproducible, but a
single sample per `(model, condition, task)` gives no variance estimate. A small
delta on one task is not evidence; the 12-task mean is the unit to trust.

**`learn_store` reads a shared memory file.** The grader checks
`bool(mem.all())` against the memory file shared by all 12 tasks in a run. Any
`save_memory` call from tasks 1–9 satisfies the "save_memory was used" check
before task 10 begins.

**`learn_use` memory injection is phrasing-dependent.** Auto-injection uses
keyword overlap (`memory.search`) between the task text — *"Book a quick sync
with Priya tomorrow morning"* — and the model's own wording of the saved fact.
If the model wrote *"I like meetings to be 25 minutes long"*, the overlap is
zero and nothing is injected, leaving the harness to rely on the model calling
`recall_memories` itself. The task grades whether the preference was applied, so
this is measured honestly either way, but a low `learning` score may indict
retrieval rather than the model.

**Some string checks are loose.** Attendance confirmation matches any of
`confirm` / `attend` / `yes` / `count me in` in the subject+body, so an
off-target reply containing "yes" can pass. `cal_brief`'s ordering check uses
first-occurrence indices of `"design review"` < `"sam"` < `"marketing"`.

**Resume skips by key.** A task is skipped if a record with the same
`(model, condition, task)` already exists. To re-run one, delete its record from
`results.json` first.

**The router is bypassed.** `run_bench.py` constructs `LLM(model)` directly, so
the tiered [`model_router.py`](../harness/model_router.py) is never exercised.
All published numbers are single-model. Whether tiering helps or hurts is
unmeasured — adding it as a third condition is the natural next experiment.

**`summary.json` has no consumer.** `report.py` documents it as feeding "the
HTML report", but no HTML generator exists in `bench/`.

**Real-file tools are excluded by design.** Nothing in `bench/` imports
[`fs_tools.py`](../harness/fs_tools.py), and `agent.EXTRA_RULES` /
`EXTRA_WRITE_TOOLS` stay empty here, so the graded system prompt is
byte-identical to earlier runs and results stay comparable across time.
