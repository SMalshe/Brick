# Remediation plan

Problems found by reading the codebase, with a fix for each, ordered into
phases that can be worked top to bottom. Every finding in Phases 1–2 was
verified by executing the code, not inferred.

Companion docs: [`ARCHITECTURE.md`](ARCHITECTURE.md) for how the system fits
together, [`bench/README.md`](bench/README.md) for benchmark methodology.

---

## How to work this list

**Read [`ARCHITECTURE.md` §10](ARCHITECTURE.md) first.** The codebase holds
itself to invariants — benchmark comparability above all — and several of these
fixes can violate them if applied carelessly.

Rules for this work:

1. **One task per commit**, using the task ID in the message (`F-03: ...`).
2. **Never change `run_harness()` or `run_raw()` semantics** as a side effect of
   a fix. New capability arrives as a new condition, not as an edit to the loops.
3. **Phase 3 changes the measuring instrument.** Everything in it alters scores
   on runs that are otherwise identical. Do not mix those commits with Phase 1/2
   work, and see the versioning task (F-12) before starting.
4. **Run the test suite from Phase 4 before and after** each later change once it
   exists. If you are working before Phase 4, at minimum re-run one task per
   condition and diff the transcript.

Effort estimates assume familiarity with the module in question.

---

## Phase 1 — result integrity

Non-breaking. These fix cases where the harness produces a *plausible wrong
number* rather than an obvious failure, which is the worst failure mode for a
measurement project. Do these first.

### F-01 · Resume silently invalidates the learning tasks

- **Severity** high · **Effort** ~20 min · **Breaking** no
- **Files** [`bench/run_bench.py:66-76`](bench/run_bench.py#L66-L76)

**Problem.** The memory file is deleted at the start of every `(model,
condition)` pair, but the task loop then skips tasks already present in
`results.json`. Interrupt a run after `learn_store` (task 10) and resume:
memory is wiped, `learn_store` is skipped as already-done, and `learn_use` runs
against an empty store. It scores near-zero and the record is indistinguishable
from a genuine model failure.

**Fix.** Only wipe memory when the pair is genuinely starting from scratch, and
refuse to produce a known-invalid `learn_use` record.

```python
pair_done = [r for r in results
             if r["model"] == model and r["condition"] == condition]
if not pair_done:
    if os.path.exists(mem_path):
        os.remove(mem_path)          # fresh pair: fresh memory
elif not os.path.exists(mem_path):
    print(f"[warn] {model} {condition}: resuming with no memory.jsonl; "
          f"memory-dependent tasks will be skipped. Delete their records "
          f"from results.json to re-run them.", flush=True)
```

Then, in the task loop, skip a task that depends on memory when the store is
missing but its producer task is already recorded — or simply let the warning
stand and document that `learn_store`/`learn_use` must be re-run as a pair. The
warning is the minimum; the skip is better.

**Verify.** Run `--tasks pptx_basic learn_store`, confirm `memory.jsonl` is
non-empty, re-invoke the same command, and assert the file still has content.

---

### F-02 · Grader crash on multi-letter spreadsheet columns

- **Severity** high · **Effort** ~10 min · **Breaking** no
- **Files** [`bench/grade.py:61-89`](bench/grade.py#L61-L89)

**Problem.** `_cell_number` computes column indices with
`ord(m.group(1).upper()) - 65`, but the regex group is `[A-Z]+`. Verified:

```
_cell_number("=SUM(AA1:AA3)")
TypeError: ord() expected a character, but string of length 2 found
```

The exception propagates out of `sheet_numbers` into the grader, where
[`run_bench.py:100-101`](bench/run_bench.py#L100-L101) converts it to
`score = 0.0`. A correct spreadsheet scores zero.

**Fix.** Replace the arithmetic with a real base-26 converter.

```python
def _col_index(letters):
    """'A' -> 0, 'Z' -> 25, 'AA' -> 26."""
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1
```

Use it for both `m.group(1)` and `m.group(3)`.

**Verify.** `_cell_number("=SUM(AA1:AB2)", rows=...)` returns the right sum;
`_cell_number("=SUM(B2:B4)", ...)` is unchanged.

---

### F-03 · A crashed grader is invisible in every aggregate

- **Severity** medium · **Effort** ~15 min · **Breaking** no
- **Files** [`bench/run_bench.py:100-113`](bench/run_bench.py#L100-L113), [`bench/report.py`](bench/report.py)

**Problem.** A grader exception is recorded as
`checks = [("grader crashed: ...", False)]`, so it *is* present in the per-task
record — but there is no top-level field, and `report.py` aggregates it as an
ordinary `0.00`. Across a 3-model × 2-condition run, a systematically broken
grader looks like a capability finding.

**Fix.** Add a distinct field and surface it.

```python
except Exception as e:
    score, checks = 0.0, [(f"grader crashed: {e}", False)]
    grader_error = f"{type(e).__name__}: {e}"
...
rec = {..., "grader_error": grader_error}
```

In `report.py`, count records with `grader_error` per `(model, condition)` and
print a warning line above the tables when the count is nonzero.

**Verify.** Temporarily raise inside a grader, run one task, confirm
`grader_error` appears in `results.json` and the report warns.

---

### F-04 · `results.json` is written non-atomically

- **Severity** medium · **Effort** ~5 min · **Breaking** no
- **Files** [`bench/run_bench.py:114-115`](bench/run_bench.py#L114-L115)

**Problem.** The file is truncated and rewritten after every task. An interrupt
inside that window corrupts the ledger the entire resume design depends on.

**Fix.**

```python
tmp = results_path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(results, f, indent=1)
os.replace(tmp, results_path)      # atomic on POSIX and Windows
```

**Verify.** Confirm `results.json.tmp` never survives a normal run.

---

### F-05 · Log numbering skips and can overwrite transcripts

- **Severity** medium · **Effort** ~10 min · **Breaking** no
- **Files** [`agents/_shared/run_agent.py:189-192`](agents/_shared/run_agent.py#L189-L192), [`webui/runner.py:249-250`](webui/runner.py#L249-L250)

**Problem.** The next log index is `len(os.listdir(log_dir)) + 1`. With
`--tiers`, `model_calls.jsonl` inflates the count so numbering skips. Worse,
deleting any file lowers the count and the next run **overwrites an existing
transcript**.

**Fix.** Derive the index from the filenames that actually match.

```python
import re
existing = [f for f in os.listdir(log_dir) if re.fullmatch(r"run_\d{3}\.json", f)]
n = max((int(f[4:7]) for f in existing), default=0) + 1
```

Apply in both places. (Fold into F-16 if you deduplicate the runner first.)

**Verify.** Create `logs/run_007.json`, run once, confirm `run_008.json` is
written and `run_007.json` is untouched.

---

## Phase 2 — safety

These concern the agent's ability to damage real files. Independent of Phase 1;
do them before anyone runs `--root` on this machine.

### F-06 · The write deny-list is inert on macOS and Linux

- **Severity** high · **Effort** ~30 min · **Breaking** no
- **Files** [`harness/fs_tools.py:36-44`](harness/fs_tools.py#L36-L44)

**Problem.** All seven entries are hardcoded Windows paths. Verified on this
machine — none of them exist, so the deny-list matches nothing. Two of them also
hardcode `C:\Users\Lab User\SAIL\Project\`, so they fail to protect this repo's
own `results/` and `harness/` even on Windows if the project is moved.

**Fix.** Derive the project paths, and branch the system paths by platform.

```python
_PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _default_deny():
    deny = [os.path.join(_PROJECT, "results"),
            os.path.join(_PROJECT, "harness"),
            sys.prefix]                      # the interpreter running this
    if os.name == "nt":
        deny += [os.environ.get("SystemRoot", r"C:\Windows"),
                 os.path.join(os.environ.get("SystemDrive", "C:") + os.sep, "Program Files"),
                 os.path.join(os.environ.get("SystemDrive", "C:") + os.sep, "Program Files (x86)")]
    else:
        deny += ["/System", "/usr", "/bin", "/sbin", "/etc", "/var",
                 "/Library", os.path.expanduser("~/Library")]
    return deny

_DENY_WRITE = _default_deny()
```

Keep the Ollama blob path, but resolve it from config or an env var rather than
a literal.

**Verify.** On macOS, `_resolve("/etc/hosts", write=True)` with `_ROOT="/"`
must raise `ToolError`.

---

### F-07 · The filesystem root is an accepted working root

- **Severity** high · **Effort** ~15 min · **Breaking** no
- **Files** [`harness/fs_tools.py:47-69`](harness/fs_tools.py#L47-L69), `enable()`

**Problem.** `_within("/etc/passwd", "/")` returns `True` — verified — because
`"/".rstrip("/") + os.sep` is just `"/"` and every absolute path starts with it.
Combined with F-06, `--root /` on macOS gives an unsupervised small model
`delete_path` over the whole filesystem with no backstop.

**Fix.** Refuse a root that is a filesystem or drive root, in `enable()`:

```python
norm = _norm(root)
if norm == _norm(os.sep) or re.fullmatch(r"[a-z]:\\?", norm, re.I):
    raise ToolError("refusing to use the filesystem root as the working root; "
                    "choose a specific folder")
```

Also harden containment itself:

```python
def _within(path, root):
    path, root = _norm(path), _norm(root)
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:      # different drives on Windows
        return False
```

**Verify.** `fs_tools.enable("/")` raises; `enable("~/sandbox")` still works and
still rejects `../`.

---

### F-08 · `--yolo` silences shell confirmations

- **Severity** medium · **Effort** ~10 min · **Breaking** no
- **Files** [`harness/fs_tools.py:211-225`](harness/fs_tools.py#L211-L225), both runners

**Problem.** `run_command` is scoped only by `cwd=_ROOT`; PowerShell can `cd`
anywhere, so `--shell` doesn't widen the sandbox, it removes it. `--yolo` then
removes the only remaining control.

**Fix.** Make shell confirmation non-bypassable: keep a separate
`_SHELL_CONFIRM` that `--yolo` does not clear, and have `_run_command` call it
regardless. Update the `--yolo` help text to say it applies to file operations
only. Also state the limitation in the `run_command` tool description, since
that text is part of the model-facing interface.

**Verify.** `--shell --yolo` still prompts before running a command.

---

### F-09 · Unvalidated path join in `/api/reveal`

- **Severity** low · **Effort** ~5 min · **Breaking** no
- **Files** [`webui/server.py:497-505`](webui/server.py#L497-L505)

**Problem.** `sub` comes from the request body and is joined unchecked, so
`"../../.."` escapes the agent folder. Impact is limited (loopback-only, and it
only opens a folder in the file manager), but it is the one unchecked input on
the server.

**Fix.** Allowlist it.

```python
ALLOWED_SUBS = {"", "workspace", "workspace/files", "memory", "logs"}
if sub not in ALLOWED_SUBS:
    raise ValueError(f"cannot reveal {sub!r}")
```

**Verify.** `POST /api/reveal {"agent":"8b","sub":"../.."}` returns 400.

---

## Phase 3 — grader semantics ⚠️ breaking

**Everything in this phase changes scores on otherwise-identical runs.** Results
produced before and after are not comparable. Do F-12 first, batch the rest into
a single commit, and re-run any cells you intend to compare.

### F-10 · `learn_store` can pass on another task's memory writes

- **Severity** high · **Effort** ~15 min · **Breaking** **yes**
- **Files** [`bench/tasks.py:172-179`](bench/tasks.py#L172-L179)

**Problem.** The grader checks `bool(mem.all())` against the memory file shared
by all 12 tasks in the run, so any `save_memory` from tasks 1–9 satisfies
"save_memory was used" before task 10 starts.

**Fix.** Grade against the episode's own action log, which is already fresh per
task — no signature change needed.

```python
def grade_learn_store(world, mem=None):
    saved = [a for a in world.actions if a["tool"] == "save_memory" and a["ok"]]
    facts = " | ".join(str(a["args"].get("fact", "")) for a in saved).lower()
    checks = [("save_memory was used in this episode", bool(saved)),
              ("saved fact mentions 25-minute preference", "25" in facts),
              ("saved fact mentions the no-before-10am rule",
               any(s in facts for s in ("10am", "10:00", "10 am")))]
    return _score(checks)
```

**Verify.** A run where task 3 saves an unrelated fact and task 10 saves nothing
must now score 0.00 on `learn_store`.

---

### F-11 · Substring checks with false-positive vectors

- **Severity** medium · **Effort** ~30 min · **Breaking** **yes**
- **Files** [`bench/tasks.py:87-102`](bench/tasks.py#L87-L102), [`bench/tasks.py:136-147`](bench/tasks.py#L136-L147), [`bench/grade.py`](bench/grade.py)

**Problem.** Three distinct issues:

1. `text.find("sam")` for the chronological-order check also matches "same".
2. `"1:1 with sam"` is reduced to `"1:1"` by `t.split(" with ")[0]`, making that
   check far looser than its two siblings.
3. Attendance confirmation accepts a bare `"yes"` anywhere in subject+body.

**Fix.** Add a word-boundary helper to `grade.py` and use it throughout.

```python
def has_word(text, phrase):
    return re.search(rf"\b{re.escape(phrase.lower())}\b", text.lower()) is not None

def word_pos(text, phrase):
    m = re.search(rf"\b{re.escape(phrase.lower())}\b", text.lower())
    return m.start() if m else -1
```

Then require both tokens for the 1:1 check (`has_word(text, "1:1") and
has_word(text, "sam")`), use `word_pos` for the ordering indices, and replace
the `"yes"` membership test with `has_word`.

**Verify.** A message reading *"same agenda as always"* must no longer satisfy
the Sam ordering check.

---

### F-12 · Version the benchmark so results stay interpretable

- **Severity** medium · **Effort** ~20 min · **Breaking** no (do it **first**)
- **Files** [`bench/tasks.py`](bench/tasks.py), [`bench/run_bench.py`](bench/run_bench.py), [`bench/report.py`](bench/report.py)

**Problem.** Grader fixes silently change the instrument. Nothing in
`results.json` records which version of the graders produced a score, so old and
new records mix indistinguishably in one file.

**Fix.** Add `BENCH_VERSION = 2` to `tasks.py`, stamp it into every record, and
have `report.py` refuse to aggregate mixed versions without an explicit
`--allow-mixed` flag.

```python
rec = {..., "bench_version": BENCH_VERSION}
```

Bump it in the same commit as F-10/F-11.

**Verify.** `report.py` errors on a `results.json` containing both versions.

---

### F-13 · Report drops rows when only one condition was run

- **Severity** low · **Effort** ~10 min · **Breaking** no
- **Files** [`bench/report.py:79-83`](bench/report.py#L79-L83)

**Problem.** The Overall row is emitted only when both `raw` and `harness` means
exist, so `--conditions harness` prints an empty table with no explanation.

**Fix.** Emit a row whenever either condition has data, showing `-` for the
missing one and `-` for the delta, and print a note that the delta needs both.

---

## Phase 4 — tests

The single highest-value work in this document. F-02 and F-11 are exactly the
bugs a grader test suite catches, and there is currently no test anywhere in the
repo.

### F-14 · Grader and harness unit tests

- **Severity** high · **Effort** ~3-4 h · **Breaking** no
- **Files** new `tests/`

**Plan.** Four files, no Ollama required:

- **`tests/test_graders.py`** — for each of the 12 graders, build a `World`,
  generate real artifacts through `harness.office`, and assert the score for a
  known-good and a known-bad case. This is the suite that protects the numbers.
- **`tests/test_parsing.py`** — table-driven over `parse_strict`,
  `parse_lenient`, `repair_args`, `normalize_date`, `normalize_time`. Include
  the fenced-JSON, trailing-comma, and prose-wrapped cases the harness claims to
  handle.
- **`tests/test_fs_scope.py`** — `../` escapes, absolute paths, `%VAR%`
  expansion, the deny-list, and the root-refusal from F-07.
- **`tests/test_loop.py`** — drive `run_harness` with a scripted stub so the
  whole loop is testable offline:

```python
class FakeLLM:
    """Replays canned replies; satisfies the LLM interface run_harness needs."""
    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0
        self.prompt_tokens = self.output_tokens = 0
        self.wall = 0.0
    def chat(self, messages, force_json=False, num_predict=700, role=None, keep_alive=None):
        self.calls += 1
        return self.replies.pop(0) if self.replies else '{"tool":"done","args":{"summary":"x"}}'
```

Cover at minimum: loop-breaking suppresses a duplicate call, the verifier
rejects a premature `done()`, `repair_args` rescues a near-miss parameter name,
and the budget is never exceeded.

**Verify.** `python -m pytest tests/ -q` green; deliberately reintroduce the
F-02 bug and confirm a test fails.

---

### F-15 · Fail the build when the frozen training prompt drifts

- **Severity** medium · **Effort** ~20 min · **Breaking** no
- **Files** new `tests/test_prompt_snapshot.py`

**Problem.** `training_scripts/system_prompt.txt` is a manual snapshot of the
live harness prompt. Edit any tool description and the adapter trains on a
context it will never see at inference. The README documents the re-snapshot
procedure; nothing detects when it's needed.

**Fix.** Turn silent drift into a failing test.

```python
def test_frozen_prompt_matches_live_harness():
    from harness.agent import HARNESS_SYSTEM, SHAPE
    from harness.tools import tool_docs
    from harness.world import SIM_TODAY_HUMAN
    live = HARNESS_SYSTEM.format(today=SIM_TODAY_HUMAN, shape=SHAPE,
                                 docs=tool_docs(with_examples=True),
                                 memory_block="", extra_rules="")
    frozen = open("training_scripts/system_prompt.txt", encoding="utf-8").read()
    assert frozen == live, ("system_prompt.txt is stale — re-snapshot it and "
                            "regenerate data/toolcall.jsonl (see training_scripts/README.md)")
```

Consider the same idea for the two shipped `toolcall.jsonl` copies: assert the
generators agree on a fixed seed.

---

## Phase 5 — hygiene

### F-16 · Deduplicate `run_agent.py` (6 identical copies)

- **Severity** medium · **Effort** ~45 min · **Breaking** no
- **Files** `agents/*/run_agent.py`, `agents/*/run.ps1`, [`agents/_shared/run_agent.py`](agents/_shared/run_agent.py)

**Problem.** Confirmed byte-identical across `_shared` and all five agent
folders. Every change must be made six times, and F-05 already has to be applied
twice.

**Fix.** Add an `--agent-dir` flag to `_shared/run_agent.py` (defaulting to its
own directory, preserving current behavior), point each `run.ps1` at the shared
script, and delete the five copies.

```powershell
& "<python>" (Join-Path $PSScriptRoot "..\_shared\run_agent.py") --agent-dir $PSScriptRoot @args
```

Do the same for the `REAL_RULES` string and `build_llm()`, which are duplicated
a seventh time in [`webui/runner.py`](webui/runner.py) — move both into
`harness/` so there is one definition.

**Verify.** Each agent still finds its own `config.json`, `workspace/`,
`memory/` and `logs/`.

---

### F-17 · Add a root dependency manifest

- **Severity** medium · **Effort** ~10 min · **Breaking** no

**Problem.** `requests`, `python-pptx` and `openpyxl` are undeclared; the only
`requirements.txt` lives in `training_scripts/` and covers training only.

**Fix.** Add a root `requirements.txt`:

```
requests>=2.31
python-pptx>=0.6.23
openpyxl>=3.1
pytest>=8.0    # after Phase 4
```

Reference it from the root README's run instructions.

---

### F-18 · Small cleanups

- **Effort** ~30 min total · **Breaking** no

- [ ] `report.py` docstring claims `summary.json` feeds "the HTML report"; no
      generator exists. Either write it or drop the claim.
- [ ] `OLLAMA_URL` is defined in both [`harness/llm.py:7`](harness/llm.py#L7)
      and [`webui/server.py:33`](webui/server.py#L33) — import the one.
- [ ] [`webui/server.py:92-99`](webui/server.py#L92-L99) `tag_installed` has
      dead code (`t == tag` is already handled by the caller) and precedence
      that reads like a bug. Rewrite for legibility.
- [ ] `agent_list()` leaks a file handle counting memory lines
      ([`server.py:123`](webui/server.py#L123)); use a `with` block.
- [ ] Document the weekday convention: verified that `"monday"` on Monday
      2026-07-20 resolves to **2026-07-27**, and `"tuesday"` is identical to
      `"next tuesday"`. Defensible, but undocumented and untested — add a note
      in `normalize_date`'s docstring plus a case in `tests/test_parsing.py`.
- [ ] Decide whether `email_reply`'s "exactly one email was sent" should stay
      strict. If it changes, it belongs in the Phase 3 batch.

---

## Phase 6 — completing what's started

Larger than fixes; each is a feature milestone.

### F-19 · Write `webui/static/app.js`

- **Severity** high (the UI does not run without it) · **Effort** ~1 day

`index.html:110` loads a file that does not exist. `style.css` is finished at
19 KB and the backend is complete, so this is the only thing standing between
the current state and a working console. It must:

- fetch `/api/agents`, render the model rail, handle the not-installed case by
  streaming `/api/pull`
- `POST /api/run`, then subscribe to `/api/events` via `EventSource`
- render the event stream from [`webui/runner.py`](webui/runner.py): `banner`,
  `llm_start`/`token`/`llm_end` (append tokens live), `note`, `tool`, `world`,
  `end`, `error`
- on `confirm`, show an allow/deny prompt and `POST /api/confirm`
- drive the workspace panel from `world` events, with `/api/preview` for
  generated files
- wire the meters in the header (`calls-val`, `calls-bar`, `time-val`, `tok-val`)

The event contract is already documented in the `runner.py` module docstring —
build against that, and treat the existing element IDs in `index.html` as the
interface.

### F-20 · Make the `deep` tier reachable, or remove it

- **Effort** ~2-3 h

[`model_router.py:46`](harness/model_router.py#L46) fully specifies a tier that
nothing dispatches to. Either implement an escalation policy — e.g. after two
consecutive parse failures, or a second incomplete verifier verdict, retry the
step at `role="deep"` — or delete it and note in `adapters_note()` that only
three roles exist. **If implemented, it must be a new condition**, not a change
to the default `run_harness` path.

### F-21 · Benchmark the router

- **Effort** ~2 h

[`run_bench.py:81`](bench/run_bench.py#L81) constructs `LLM(model)` directly, so
the tiered router is never measured. Add a `tiers` condition alongside
`raw`/`harness` so the question "does an 8B verifier catch more than a 3B one"
becomes answerable with the existing machinery.

---

## Suggested order

```
Phase 1  F-01 F-02 F-03 F-04 F-05        result integrity, non-breaking
Phase 2  F-06 F-07 F-08 F-09             safety, non-breaking
Phase 4  F-14 F-15                       tests — pull forward, they de-risk Phase 3
Phase 3  F-12 then F-10 F-11 F-13        breaking; one batched commit; bump version
Phase 5  F-16 F-17 F-18                  hygiene
Phase 6  F-19 F-20 F-21                  feature completion
```

Phase 4 is listed third on purpose. Writing the grader tests before the grader
semantics change means the Phase 3 diff shows exactly which assertions moved —
which is the difference between a deliberate instrument change and an
accidental one.
