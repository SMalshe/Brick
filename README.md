# SAIL Agent Harness — making small Llama models do agent work

A harness that wraps local Llama models (via Ollama) with the scaffolding they
need to act as an office agent — PowerPoint, Excel, email, calendar
read/write, chat messages, reminders, deliberate thinking, and persistent
learning — plus a benchmark that measures exactly how much the harness
improves each model size over the same model wired to the same tools naively.

## Layout

```
Project/
  harness/
    llm.py       Ollama chat client (temperature 0, fixed seed, usage accounting)
    world.py     Simulated workspace: seeded inbox, calendar, messages, reminders.
                 Fixed simulated clock (Mon 2026-07-20) so date reasoning is gradeable.
    office.py    REAL .pptx / .xlsx creation (python-pptx, openpyxl) — open the outputs
                 in PowerPoint/Excel yourself.
    memory.py    Persistent cross-episode memory store (JSONL + keyword retrieval)
    tools.py     The 14-tool registry shared by BOTH conditions
    agent.py     The two agent loops: run_raw() and run_harness()
  bench/
    tasks.py     12 tasks covering every capability, each with a programmatic grader
    grade.py     Graders reopen the real files / world state; no LLM grades anything
    run_bench.py Matrix runner (models x conditions x tasks), resumable
  results/       Per-task transcripts, generated pptx/xlsx files, results.json
```

Everything (Python, Ollama binary, model weights) lives under `C:\Users\Lab User\SAIL\`.

## The two conditions

Both conditions get the **same 14 tools, same tool error messages, same
simulated world, same LLM-call budget (14 calls), same temperature/seed**.
Only the scaffolding differs.

**raw** — the tool list and JSON instruction in one system prompt, strict
`json.loads` parsing, tool errors fed back verbatim. This is what "just wire
the model to tools" looks like.

**harness** — what this project adds:

1. Few-shot example call attached to every tool doc
2. Grammar-constrained decoding (Ollama `format=json`)
3. Lenient JSON extraction (fence stripping, brace matching, trailing-comma repair)
4. Deterministic call repair: near-miss parameter names renamed, unknown
   parameters dropped, top-level args lifted into `args`
5. Schema validation with corrective feedback quoting the tool's correct shape
6. Date/time normalization ("2pm" → "14:00", "tomorrow"/"next tuesday" →
   resolved against the simulated clock)
7. Tool-grounded planning: the plan is a JSON list of tool names (invalid ones
   dropped) injected as *guidance*, never free prose the model then obeys blindly
8. Loop-breaking: an identical call against an unchanged world is not
   re-executed; the duplicated exchange is deleted from context (repetition is
   an attractor for small models) and the task is restated
9. A verifier pass before `done()` is accepted (model re-checks the task
   against the action log; incomplete → the loop continues)
10. Auto-injection of relevant persistent memories into the system prompt

The harness pays for its plan/verify/repair calls out of the same 14-call
budget, so the comparison is honest.

## The benchmark

12 tasks, run in order (the two learning tasks share one memory file per
model+condition run):

| task | capability |
|---|---|
| pptx_basic | PowerPoint structure from instructions |
| pptx_from_email | email → PowerPoint (real numbers must appear) |
| xlsx_basic | Excel table + total |
| xlsx_from_email | 3 receipt emails → expense sheet + total |
| email_reply | find most recent on-topic email, reply correctly |
| cal_add | natural-language date/time → correct calendar event |
| cal_freeslot | read calendar, find genuinely free hour, book it |
| cal_brief | read a day, message a summary in chronological order |
| remind_msg | reminder at exact datetime + message |
| learn_store | save stated preferences to memory |
| learn_use | separate episode: apply those preferences unprompted |
| multi_offsite | email → calendar + reply + deck, all in one task |

Grading is 100% programmatic: graders reopen the generated .pptx/.xlsx and
inspect the world state. Score per task = fraction of checks passed.

## Run it

```powershell
& "C:\Users\Lab User\SAIL\python\python.exe" -m bench.run_bench `
    --models llama3.2:1b llama3.2:3b llama3.1:8b --conditions raw harness --outdir results
```

Interrupted runs resume (finished tasks are skipped). Any Ollama model tag
works in `--models` — e.g. add a ~30B-class model later. Note: the modern
Llama family has no 30B; sizes go 1B → 3B → 8B → 70B. The nearest options are
older-generation Llama 2 13B or a non-Llama ~30B such as `qwen2.5:32b`.
