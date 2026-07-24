"""Tool registry shared by BOTH conditions (raw and harness).

Each tool: name, signature params {name: (type_desc, required)}, description,
an example call (shown only in the harness prompt), and an executor.
Tool behavior and error messages are identical across conditions - the
experiment varies only the scaffolding around the model.
"""
import json

from . import office
from .world import ToolError


def _fmt(result):
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False, default=str)


TOOLS = {
    "list_emails": {
        "desc": "List all emails in the inbox (id, from, date, subject). Newest first.",
        "params": {},
        "example": {"tool": "list_emails", "args": {}},
        "run": lambda w, m, a: w.list_emails(),
    },
    "read_email": {
        "desc": "Read the full body of one email by its id.",
        "params": {"id": ("string, an email id like 'e3'", True)},
        "example": {"tool": "read_email", "args": {"id": "e2"}},
        "run": lambda w, m, a: w.read_email(a["id"]),
    },
    "send_email": {
        "desc": "Send an email.",
        "params": {"to": ("string, recipient address", True),
                   "subject": ("string", True),
                   "body": ("string", True)},
        "example": {"tool": "send_email", "args": {"to": "dana@corp.com", "subject": "Re: numbers",
                                                   "body": "Got it, thanks!"}},
        "run": lambda w, m, a: w.send_email(a["to"], a.get("subject", ""), a.get("body", "")),
    },
    "list_events": {
        "desc": "List calendar events, optionally only for one date.",
        "params": {"date": ("string YYYY-MM-DD, optional - omit for all events", False)},
        "example": {"tool": "list_events", "args": {"date": "2026-07-22"}},
        "run": lambda w, m, a: w.list_events(a.get("date")),
    },
    "add_event": {
        "desc": "Add an event to the calendar.",
        "params": {"title": ("string", True),
                   "date": ("string YYYY-MM-DD", True),
                   "start_time": ("string 24h HH:MM", True),
                   "end_time": ("string 24h HH:MM", True),
                   "attendees": ("list of email strings, optional", False),
                   "location": ("string, optional", False)},
        "example": {"tool": "add_event", "args": {"title": "Budget review", "date": "2026-07-21",
                                                  "start_time": "13:00", "end_time": "14:00",
                                                  "attendees": ["sam@corp.com"]}},
        "run": lambda w, m, a: w.add_event(a["title"], a["date"], a["start_time"], a["end_time"],
                                           a.get("attendees"), a.get("location")),
    },
    "send_message": {
        "desc": "Send a chat/instant message to a person.",
        "params": {"to": ("string, contact name", True),
                   "text": ("string, the message", True)},
        "example": {"tool": "send_message", "args": {"to": "sam", "text": "Running 5 min late."}},
        "run": lambda w, m, a: w.send_message(a["to"], a["text"]),
    },
    "set_reminder": {
        "desc": "Set a reminder for yourself at a specific date and time.",
        "params": {"text": ("string, what to be reminded of", True),
                   "date": ("string YYYY-MM-DD", True),
                   "time": ("string 24h HH:MM", True)},
        "example": {"tool": "set_reminder", "args": {"text": "send invoice", "date": "2026-07-22",
                                                     "time": "09:00"}},
        "run": lambda w, m, a: w.set_reminder(a["text"], a["date"], a["time"]),
    },
    "create_presentation": {
        "desc": "Create a real .pptx PowerPoint file. Each slide is an object with a "
                "'title' and an optional 'bullets' list. A first slide without bullets "
                "becomes a title slide.",
        "params": {"filename": ("string ending in .pptx", True),
                   "slides": ("list of {\"title\": str, \"bullets\": [str, ...]}", True)},
        "example": {"tool": "create_presentation",
                    "args": {"filename": "plan.pptx",
                             "slides": [{"title": "2027 Plan"},
                                        {"title": "Goals", "bullets": ["Grow 20%", "Ship v2", "Hire 3"]}]}},
        "run": lambda w, m, a: office.create_presentation(w.files_dir, a["filename"], a["slides"]),
    },
    "create_spreadsheet": {
        "desc": "Create a real .xlsx Excel file from a list of rows (first row is usually "
                "headers). A cell string starting with '=' becomes a formula.",
        "params": {"filename": ("string ending in .xlsx", True),
                   "rows": ("list of rows, each row a list of cell values", True),
                   "sheet_name": ("string, optional", False)},
        "example": {"tool": "create_spreadsheet",
                    "args": {"filename": "costs.xlsx",
                             "rows": [["Item", "Cost"], ["Chairs", 400], ["Desks", 900],
                                      ["Total", "=SUM(B2:B3)"]]}},
        "run": lambda w, m, a: office.create_spreadsheet(w.files_dir, a["filename"], a["rows"],
                                                         a.get("sheet_name")),
    },
    "read_spreadsheet": {
        "desc": "Read back the cell contents of an existing .xlsx file.",
        "params": {"filename": ("string ending in .xlsx", True)},
        "example": {"tool": "read_spreadsheet", "args": {"filename": "costs.xlsx"}},
        "run": lambda w, m, a: office.read_spreadsheet(w.files_dir, a["filename"]),
    },
    "think": {
        "desc": "Think out loud about the task. Use this to reason before acting. "
                "Has no external effect.",
        "params": {"thought": ("string", True)},
        "example": {"tool": "think", "args": {"thought": "Wednesday has 3 meetings; I should list them in time order."}},
        "run": lambda w, m, a: "Noted. Continue with your next action.",
    },
    "save_memory": {
        "desc": "Save a fact or preference to long-term memory so it persists across future tasks.",
        "params": {"fact": ("string", True)},
        "example": {"tool": "save_memory", "args": {"fact": "User's manager is Sam."}},
        "run": lambda w, m, a: m.save(a["fact"]),
    },
    "recall_memories": {
        "desc": "Search long-term memory for saved facts relevant to a query.",
        "params": {"query": ("string", True)},
        "example": {"tool": "recall_memories", "args": {"query": "meeting preferences"}},
        "run": lambda w, m, a: (m.search(a["query"], k=5) or "no matching memories"),
    },
    "done": {
        "desc": "Call this exactly once, when the entire task is finished, with a short summary.",
        "params": {"summary": ("string", True)},
        "example": {"tool": "done", "args": {"summary": "Booked the meeting and messaged Sam."}},
        "run": None,  # handled by the agent loop
    },
}


def tool_docs(with_examples):
    """Render the tool documentation block for a system prompt."""
    lines = []
    for name, spec in TOOLS.items():
        lines.append(f"- {name}: {spec['desc']}")
        for p, (tdesc, req) in spec["params"].items():
            lines.append(f"    {p} ({'required' if req else 'optional'}): {tdesc}")
        if with_examples:
            lines.append(f"    example: {json.dumps(spec['example'], ensure_ascii=False)}")
    return "\n".join(lines)


def validate_call(name, args):
    """Return a list of problems with a proposed call (harness uses this to give
    corrective feedback BEFORE execution; raw condition executes directly)."""
    problems = []
    if name not in TOOLS:
        return [f"unknown tool {name!r}; valid tools: {', '.join(TOOLS)}"]
    if not isinstance(args, dict):
        return ["'args' must be a JSON object"]
    spec = TOOLS[name]
    for p, (tdesc, req) in spec["params"].items():
        if req and (p not in args or args[p] in (None, "")):
            problems.append(f"missing required parameter '{p}' ({tdesc})")
    for p in args:
        if p not in spec["params"]:
            problems.append(f"unknown parameter '{p}' (valid: {', '.join(spec['params']) or 'none'})")
    return problems


# Optional observation hook (the web UI sets it): hook(name, args, ok, obs)
# after every executed call, with the arguments as actually run - i.e. after the
# harness repaired and normalized them. None for the benchmark.
TOOL_HOOK = None


def execute(name, args, world, mem):
    """Execute a tool call. Returns (ok, observation_string). Identical in both
    conditions - errors come back as readable messages the model can react to."""
    ok, obs = _execute(name, args, world, mem)
    if TOOL_HOOK:
        TOOL_HOOK(name, args, ok, obs)
    return ok, obs


def _execute(name, args, world, mem):
    if name not in TOOLS:
        return False, f"ERROR: unknown tool {name!r}. Valid tools: {', '.join(TOOLS)}"
    spec = TOOLS[name]
    if not isinstance(args, dict):
        args = {}
    try:
        result = spec["run"](world, mem, args)
        obs = _fmt(result)
        world.log(name, args, True, obs)
        return True, obs
    except ToolError as e:
        world.log(name, args, False, str(e))
        return False, f"ERROR: {e}"
    except KeyError as e:
        msg = f"missing required parameter {e.args[0]!r}"
        world.log(name, args, False, msg)
        return False, f"ERROR: {msg}"
    except Exception as e:  # keep the episode alive on any tool bug
        world.log(name, args, False, repr(e))
        return False, f"ERROR: {type(e).__name__}: {e}"
