"""Per-model harness profiles — a different harness for each model.

The harness engine in agent.py is one codebase, but its behaviour is governed by
a handful of knobs: whether to plan, how long a plan may be, how many verifier
rounds to spend before accepting done(), whether to suppress repeated calls, how
many think() calls in a row to tolerate, how long a driver reply may run, how
many memories to inject, the context window, and the call budget.

One setting is never right for all five sizes, because the models fail and
succeed differently:

  - A 1B model's mistakes are almost all *mechanical* — broken JSON, wrong
    parameter names — and it falls into repetition loops. It cannot follow a
    plan or judge whether a task is done, so spending calls on planning and
    verification just starves the part of the budget that does real work. Its
    profile pours everything into format repair + loop-breaking, drops planning
    and the verifier, keeps replies short so the JSON survives, and gets a
    bigger budget (each call is cheap and fast).

  - A 3B follows a SHORT plan and survives ONE verify pass; a second verify
    round tends to false-negative and send it back into a loop.

  - An 8B (Llama 3.1) has solid instruction-following and JSON, so the full
    harness — real planning, two verify rounds — pays for itself.

  - Qwen 2.5 14B / 32B are strong at structured output, tool calls and math and
    reason well, so they get richer outputs (longer decks/sheets), longer plans,
    more room to think, and a wider context to read before writing. The 32B is
    also the slowest and the most reliable, so it trades the second verify round
    and a couple of budget slots for fewer, higher-quality steps — when a call
    costs minutes, flailing is the expensive failure, not stopping early.

IMPORTANT: the benchmark does NOT use these. bench/ runs run_harness with the
DEFAULT profile, so the raw-vs-harness comparison stays byte-identical to runs
already on disk. Only the on-device agents (agents/, webui/) select a per-model
profile — the same pattern as EXTRA_RULES / SIM_TODAY in agent.py.
"""
from dataclasses import asdict, dataclass, fields, replace


@dataclass(frozen=True)
class Profile:
    label: str = "default"
    rationale: str = ""

    # planning: ask for a tool-grounded plan up front, cap its length
    plan: bool = True
    plan_max_steps: int = 6

    # verification: how many times a done() may be sent back by the verifier
    verify_rounds: int = 2

    # loop-breaking: suppress an identical call against an unchanged world
    loop_break: bool = True

    # nudge "take a concrete action" after this many think() calls in a row
    think_streak_cap: int = 2

    # driver reply length cap (tokens) — shorter keeps small-model JSON intact,
    # longer lets strong models write full decks / spreadsheets in one call
    num_predict: int = 700

    # long-term memories auto-injected into the system prompt
    memory_k: int = 3

    # model context window
    num_ctx: int = 8192

    # default LLM-call budget for the simulated world (real-file mode still 40)
    max_calls: int = 14

    def to_dict(self):
        return asdict(self)


# Reproduces the benchmark harness exactly. Unknown models fall back to this.
DEFAULT = Profile()


PROFILES = {
    # --- Llama 3.2 1B -------------------------------------------------------
    "llama3.2:1b": Profile(
        label="format-survival",
        rationale="A 1B's errors are mechanical (broken JSON, wrong keys) and it "
                  "loops hard. Everything goes into format repair + loop-breaking; "
                  "planning and the verifier are dropped — it can't follow a plan or "
                  "judge completion, and both only burn its tiny budget. Replies are "
                  "kept short so the JSON stays intact, with extra calls to compensate.",
        plan=False, plan_max_steps=0, verify_rounds=0, loop_break=True,
        think_streak_cap=1, num_predict=350, memory_k=2, num_ctx=8192, max_calls=18),

    # --- Llama 3.2 3B -------------------------------------------------------
    "llama3.2:3b": Profile(
        label="guided-guarded",
        rationale="Small but usable: it follows a SHORT plan and survives one verify "
                  "pass, but a second round tends to false-negative and loop. Keep "
                  "aggressive loop-breaking; moderate output length.",
        plan=True, plan_max_steps=3, verify_rounds=1, loop_break=True,
        think_streak_cap=2, num_predict=500, memory_k=3, num_ctx=8192, max_calls=14),

    # --- Llama 3.1 8B -------------------------------------------------------
    "llama3.1:8b": Profile(
        label="balanced",
        rationale="A strong general 8B with good instruction-following and JSON. The "
                  "full harness pays off: real planning, two verify rounds, standard "
                  "budget and output length.",
        plan=True, plan_max_steps=5, verify_rounds=2, loop_break=True,
        think_streak_cap=2, num_predict=700, memory_k=3, num_ctx=8192, max_calls=14),

    # --- Qwen 2.5 14B -------------------------------------------------------
    "qwen2.5:14b": Profile(
        label="structured-reasoner",
        rationale="Qwen 2.5 14B is excellent at structured output, tool calls and "
                  "math. Let it write richer arguments (longer decks/sheets), follow "
                  "longer plans, reason a little more before acting, and use a wider "
                  "context to read before it writes.",
        plan=True, plan_max_steps=6, verify_rounds=2, loop_break=True,
        think_streak_cap=3, num_predict=900, memory_k=4, num_ctx=12288, max_calls=14),

    # --- Qwen 2.5 32B -------------------------------------------------------
    "qwen2.5:32b": Profile(
        label="few-precise-steps",
        rationale="Qwen 2.5 32B is the most reliable and the slowest. It rarely "
                  "flails, so trust it: one verify pass instead of two, a tight budget "
                  "of high-quality steps, the longest structured outputs and the "
                  "widest context. When each call costs minutes, fewer better calls "
                  "beat more calls.",
        plan=True, plan_max_steps=6, verify_rounds=1, loop_break=True,
        think_streak_cap=3, num_predict=1000, memory_k=4, num_ctx=16384, max_calls=12),
}


def for_model(tag, override=None):
    """Resolve the harness profile for a model tag.

    Exact tag first, then the same base family (`llama3.2:1b-instruct-q4` ->
    `llama3.2:1b`'s family), else DEFAULT so any new model still runs. `override`
    is an optional dict (e.g. a config.json "harness" block) that patches
    individual fields on top of the chosen profile.
    """
    prof = PROFILES.get(tag)
    if prof is None:
        base = str(tag).split(":")[0]
        prof = next((v for k, v in PROFILES.items() if k.split(":")[0] == base), None)
    prof = prof or DEFAULT
    if isinstance(override, dict) and override:
        known = {f.name for f in fields(Profile)}
        prof = replace(prof, **{k: v for k, v in override.items() if k in known})
    return prof
