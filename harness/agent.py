"""Two agent loops over the SAME tools and the SAME LLM-call budget.

raw     - what you get wiring a model to tools naively: tool list in the
          prompt, strict JSON parsing, errors fed back verbatim, no other help.

harness - the scaffolding under test:
          1. few-shot example per tool in the docs
          2. grammar-constrained decoding (Ollama format=json)
          3. lenient JSON extraction + repair feedback
          4. deterministic call repair (rename near-miss params, drop unknowns,
             lift top-level args) before rejecting anything
          5. schema validation with corrective, example-bearing feedback
          6. date/time argument normalization ("2pm" -> "14:00", "tomorrow" ->
             resolved against the simulated clock)
          7. a tool-grounded plan step (JSON list of tool names, not free prose)
          8. loop-breaking: repeated identical calls are not re-executed; the
             duplicated exchanges are removed from context (they act as
             attractors for small models) and the task is restated
          9. a verifier pass before accepting done()
         10. auto-injection of relevant long-term memories

Both loops stop after MAX_CALLS total LLM invocations, so the harness pays
for its plan/verify/repair calls out of the same budget.
"""
import datetime
import difflib
import json
import re

from .tools import TOOLS, execute, tool_docs, validate_call
from .world import SIM_TODAY, SIM_TODAY_HUMAN

MAX_CALLS = 14
OBS_LIMIT = 2000  # observation truncation, same in both conditions

# Abstract on purpose: concrete example content in an instruction becomes an
# attractor that 1B models copy verbatim. Real examples live per-tool in docs.
SHAPE = '{"thought": "<why>", "tool": "<tool_name>", "args": { ... }}'


# ---------------------------------------------------------------- parsing ----

def strip_fences(text):
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    return m.group(1).strip() if m else text.strip()


def parse_strict(text):
    """Raw condition: fence-strip + json.loads, nothing else."""
    try:
        obj = json.loads(strip_fences(text))
        if isinstance(obj, dict):
            return obj, None
        return None, "response was not a JSON object"
    except Exception as e:
        return None, f"response was not valid JSON ({e})"


def parse_lenient(text):
    """Harness condition: also brace-match the first object and repair
    trailing commas."""
    obj, err = parse_strict(text)
    if obj is not None:
        return obj, None
    text = strip_fences(text)
    start = text.find("{")
    if start == -1:
        return None, "no JSON object found in response"
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                cand = text[start:i + 1]
                for fix in (cand, re.sub(r",\s*([}\]])", r"\1", cand)):
                    try:
                        obj = json.loads(fix)
                        if isinstance(obj, dict):
                            return obj, None
                    except Exception:
                        pass
                return None, "found a {...} block but it is not valid JSON"
    return None, "unbalanced braces in response"


# ---------------------------------------------------- date/time normalizing ----

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"])}


def normalize_date(value, today=None):
    # bound at call time, not import time, so a runner can point the harness at
    # the real clock by setting agent.SIM_TODAY (the benchmark leaves it alone)
    today = today or SIM_TODAY
    if not isinstance(value, str):
        return value
    s = value.strip().lower()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    if s == "today":
        return today.isoformat()
    if s == "tomorrow":
        return (today + datetime.timedelta(days=1)).isoformat()
    m = re.match(r"^(?:next\s+)?([a-z]+day)$", s)
    if m and m.group(1) in _WEEKDAYS:
        delta = (_WEEKDAYS.index(m.group(1)) - today.weekday()) % 7 or 7
        return (today + datetime.timedelta(days=delta)).isoformat()
    m = re.match(r"^([a-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?$", s)
    if m:
        for name, num in _MONTHS.items():
            if name.startswith(m.group(1)):
                year = int(m.group(3)) if m.group(3) else today.year
                return f"{year:04d}-{num:02d}-{int(m.group(2)):02d}"
    m = re.match(r"^(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?$", s)
    if m:
        year = int(m.group(3)) if m.group(3) else today.year
        if year < 100:
            year += 2000
        return f"{year:04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return value


def normalize_time(value):
    if not isinstance(value, str):
        return value
    s = value.strip().lower().replace(".", "")
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", s)
    if not m:
        return value
    h = int(m.group(1))
    mins = m.group(2) or "00"
    ap = m.group(3)
    if ap == "pm" and h != 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    if h > 23 or int(mins) > 59:
        return value
    return f"{h:02d}:{mins}"


def normalize_args(name, args):
    if not isinstance(args, dict):
        return args
    out = dict(args)
    for key in out:
        if key == "date":
            out[key] = normalize_date(out[key])
        elif key in ("start_time", "end_time", "time"):
            out[key] = normalize_time(out[key])
    return out


def repair_args(name, args):
    """Deterministic near-miss repair: rename close-match parameter names to
    the missing required ones, then drop unknown parameters. Returns
    (fixed_args, [notes])."""
    spec = TOOLS.get(name)
    if not spec or not isinstance(args, dict):
        return args, []
    valid = spec["params"]
    out = dict(args)
    notes = []
    unknown = [k for k in out if k not in valid]
    missing = [p for p, (_, req) in valid.items() if req and out.get(p) in (None, "")]
    for miss in missing:
        cand = difflib.get_close_matches(miss, unknown, n=1, cutoff=0.5)
        if not cand:
            cand = [u for u in unknown if u in miss or miss in u]
        if cand:
            out[miss] = out.pop(cand[0])
            unknown.remove(cand[0])
            notes.append(f"renamed '{cand[0]}' -> '{miss}'")
    for u in unknown:
        out.pop(u)
        notes.append(f"dropped unknown parameter '{u}'")
    return out, notes


# ------------------------------------------------------------- transcripts ----

# Optional observation hook (the web UI sets it): called as hook(kind, content)
# for every transcript note, so a watcher sees each step as it happens. None
# everywhere else, including the benchmark, so the loops are unaffected.
EVENT_HOOK = None


class Episode:
    def __init__(self):
        self.transcript = []   # readable log of everything
        self.parse_failures = 0
        self.invalid_calls = 0
        self.tool_errors = 0
        self.done_summary = None
        self.finished = False

    def note(self, kind, content):
        self.transcript.append({"kind": kind, "content": content})
        if EVENT_HOOK:
            EVENT_HOOK(kind, content)


def _obs(text):
    text = str(text)
    return text if len(text) <= OBS_LIMIT else text[:OBS_LIMIT] + " ...[truncated]"


# ------------------------------------------------------------------- RAW ----

RAW_SYSTEM = """You are an assistant that completes office tasks using tools. \
Today is {today}.

Available tools:
{docs}

Respond with a single JSON object of the form {{"tool": "<tool name>", "args": {{...}}}}. \
Call the done tool when the task is finished."""


def run_raw(llm, world, mem, task_text):
    ep = Episode()
    system = RAW_SYSTEM.format(today=SIM_TODAY_HUMAN, docs=tool_docs(with_examples=False))
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": task_text}]
    ep.note("system", system)
    ep.note("task", task_text)

    while llm.calls < MAX_CALLS:
        reply = llm.chat(messages, force_json=False)
        messages.append({"role": "assistant", "content": reply})
        ep.note("model", reply)
        obj, err = parse_strict(reply)
        if obj is None:
            ep.parse_failures += 1
            fb = f"ERROR: {err}. Respond with a single JSON object: {{\"tool\": ..., \"args\": {{...}}}}"
            messages.append({"role": "user", "content": fb})
            ep.note("feedback", fb)
            continue
        name = obj.get("tool") or obj.get("name") or ""
        args = obj.get("args") if isinstance(obj.get("args"), dict) else {}
        if name == "done":
            ep.done_summary = str(args.get("summary", ""))
            ep.finished = True
            ep.note("done", ep.done_summary)
            break
        ok, obs = execute(name, args, world, mem)
        if not ok:
            ep.tool_errors += 1
        obs = _obs(obs)
        messages.append({"role": "user", "content": f"OBSERVATION: {obs}"})
        ep.note("observation", obs)
    world.snapshot()
    return ep


# --------------------------------------------------------------- HARNESS ----

HARNESS_SYSTEM = """You are a careful office assistant agent. Today is {today}.
You interact with the world ONLY by calling tools, one call per reply.

RESPONSE FORMAT - every reply must be exactly one JSON object:
{shape}

Rules:
- ONE tool call per reply. No text outside the JSON object.
- Only do what the task requires - nothing extra.
- Look before you act: read the relevant emails or calendar before writing anything that depends on them.
- Dates must be YYYY-MM-DD. Times must be 24-hour HH:MM.
- If a tool returns an ERROR, fix the arguments and try again.
- When every part of the task is complete, call done with a short summary.

TOOLS:
{docs}{memory_block}{extra_rules}"""

# Appended to the harness system prompt. Empty for the benchmark, so the graded
# prompt stays byte-identical to earlier runs; the on-device agents set it.
EXTRA_RULES = ""

# Extra world-changing tool names, for the loop-breaking check. Empty for the
# benchmark; the on-device agents add the real-filesystem writers.
EXTRA_WRITE_TOOLS = set()

PLAN_PROMPT = ('Which tools will you need to call to complete this task, in order? '
               'Reply with one JSON object: {"steps": [{"tool": "<tool_name>", "what": "<5 words>"}, ...]}. '
               'Most tasks need only 1-4 calls. Do not include tools the task does not need.')


def plan_step(llm, messages, ep):
    """Ask for a tool-grounded plan; return it as short text (or ''). Invalid
    tool names are dropped - free prose never enters the context."""
    reply = llm.chat(messages, force_json=True, num_predict=250, role="router")
    obj, _ = parse_lenient(reply)
    steps = []
    if isinstance(obj, dict) and isinstance(obj.get("steps"), list):
        for s in obj["steps"][:6]:
            if isinstance(s, dict) and s.get("tool") in TOOLS:
                what = str(s.get("what", ""))[:60]
                steps.append(f"{len(steps) + 1}. {s['tool']} - {what}")
    plan = "\n".join(steps)
    ep.note("plan", plan or f"(unusable plan reply: {reply[:200]})")
    return plan


def run_harness(llm, world, mem, task_text):
    ep = Episode()
    memories = mem.search(task_text, k=3)  # inject only matches, never a recency fallback
    memory_block = ""
    if memories:
        memory_block = ("\n\nTHINGS YOU HAVE LEARNED PREVIOUSLY (apply them when relevant):\n"
                        + "\n".join(f"- {f}" for f in memories))
    system = HARNESS_SYSTEM.format(today=SIM_TODAY_HUMAN, shape=SHAPE,
                                   docs=tool_docs(with_examples=True),
                                   memory_block=memory_block,
                                   extra_rules=EXTRA_RULES)
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": f"TASK: {task_text}\n\n{PLAN_PROMPT}"}]
    ep.note("system", system)
    ep.note("task", task_text)

    plan = plan_step(llm, messages, ep)
    messages.pop()  # the plan request leaves the context; the plan re-enters as user guidance
    act = f"TASK: {task_text}\n\n"
    if plan:
        act += f"Suggested tool sequence (adapt if the results demand it):\n{plan}\n\n"
    act += f"Make the first tool call now. Reply with exactly one JSON object: {SHAPE}"
    messages.append({"role": "user", "content": act})

    verify_rounds = 0
    seen_calls = {}      # signature -> world_version at last execution
    world_version = 0    # bumped on successful writes; repeated reads are only
                         # suppressed while the world is unchanged
    write_tools = {"send_email", "add_event", "send_message", "set_reminder",
                   "create_presentation", "create_spreadsheet", "save_memory"}
    write_tools |= EXTRA_WRITE_TOOLS  # empty for the benchmark; fs_tools adds its own
    last_reply = None
    think_streak = 0

    def give_feedback(fb, reply):
        """Append corrective feedback; a verbatim-repeated bad reply gets its
        older copy deleted from context (repetition is an attractor)."""
        nonlocal last_reply
        if reply == last_reply and len(messages) >= 3 \
                and messages[-3]["role"] == "assistant" and messages[-3]["content"] == reply:
            del messages[-3:-1]
            fb = "You repeated the same invalid reply. It is still invalid. " + fb
        messages.append({"role": "user", "content": fb})
        ep.note("feedback", fb)
        last_reply = reply

    while llm.calls < MAX_CALLS:
        reply = llm.chat(messages, force_json=True, role="driver")
        messages.append({"role": "assistant", "content": reply})
        ep.note("model", reply)
        obj, err = parse_lenient(reply)
        if obj is None:
            ep.parse_failures += 1
            give_feedback(f"FORMAT ERROR: {err}. Reply with exactly one JSON object: {SHAPE}", reply)
            continue
        name = str(obj.get("tool") or obj.get("name") or "").strip()
        args = obj.get("args") if isinstance(obj.get("args"), dict) else {}
        if not args:
            # repair: models sometimes put args at the top level next to "tool"
            args = {k: v for k, v in obj.items() if k not in ("tool", "name", "thought", "args")}

        if name == "done":
            if verify_rounds < 2 and llm.calls < MAX_CALLS:
                verify_rounds += 1
                verdict = _verify(llm, world, task_text)
                ep.note("verify", json.dumps(verdict, ensure_ascii=False))
                if not verdict.get("complete", True):
                    give_feedback("VERIFIER: the task is NOT finished yet. Missing: "
                                  f"{verdict.get('missing', 'unknown')}. Continue with the next tool call.",
                                  reply)
                    continue
            ep.done_summary = str(args.get("summary", ""))
            ep.finished = True
            ep.note("done", ep.done_summary)
            break

        args, fixes = repair_args(name, args)
        if fixes:
            ep.note("repair", "; ".join(fixes))
        args = normalize_args(name, args)

        problems = validate_call(name, args)
        if problems:
            ep.invalid_calls += 1
            hint = ""
            if name in TOOLS:
                hint = " Correct shape: " + json.dumps(TOOLS[name]["example"], ensure_ascii=False)
            else:
                close = difflib.get_close_matches(name, TOOLS.keys(), n=1)
                if close:
                    hint = (f" Did you mean '{close[0]}'? Correct shape: "
                            + json.dumps(TOOLS[close[0]]["example"], ensure_ascii=False))
            give_feedback("INVALID CALL: " + "; ".join(problems) + "." + hint
                          + " Reply with one corrected JSON object.", reply)
            continue
        last_reply = reply

        sig = json.dumps({"t": name, "a": args}, sort_keys=True, default=str)
        if name != "think" and seen_calls.get(sig) == world_version:
            # Identical call, unchanged world: do not re-execute. If it is a
            # verbatim repeat of the previous exchange, delete the older copy
            # (repetition in context is an attractor for small models).
            if len(messages) >= 3 and messages[-3]["role"] == "assistant" \
                    and messages[-3]["content"] == reply:
                del messages[-3:-1]
            fb = (f"You already called {name} with exactly those arguments; its result is above "
                  f"and has not changed. Do the NEXT step of the task: \"{task_text}\" "
                  f"If everything is complete, call done.")
            messages.append({"role": "user", "content": fb})
            ep.note("feedback", fb)
            continue
        think_streak = think_streak + 1 if name == "think" else 0

        ok, obs = execute(name, args, world, mem)
        if ok and name in write_tools:
            world_version += 1
        seen_calls[sig] = world_version
        if not ok:
            ep.tool_errors += 1
        obs = _obs(obs)
        if think_streak >= 2:
            obs += " NOTE: stop thinking and take a concrete action now."
        messages.append({"role": "user", "content": f"OBSERVATION: {obs}"})
        ep.note("observation", obs)
    world.snapshot()
    return ep


def _verify(llm, world, task_text):
    acts = [a for a in world.actions if a["tool"] != "think"]
    lines = []
    for a in acts:
        status = "ok" if a["ok"] else "FAILED"
        lines.append(f"- {a['tool']}({json.dumps(a['args'], ensure_ascii=False, default=str)[:200]}) -> {status}")
    prompt = (f"TASK GIVEN TO AN ASSISTANT:\n{task_text}\n\n"
              f"ACTIONS THE ASSISTANT TOOK:\n" + "\n".join(lines or ["(none)"])
              + "\n\nCheck the task requirements one by one against the actions. "
                'Respond with one JSON object: {"complete": true or false, "missing": "<what has not been done>"}')
    msgs = [{"role": "system", "content": "You are a strict task-completion verifier. Today is "
             + SIM_TODAY_HUMAN + "."},
            {"role": "user", "content": prompt}]
    try:
        reply = llm.chat(msgs, force_json=True, num_predict=200, role="verifier")
        obj, _ = parse_lenient(reply)
        if isinstance(obj, dict) and isinstance(obj.get("complete"), bool):
            return obj
    except Exception:
        pass
    return {"complete": True, "missing": ""}
