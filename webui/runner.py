"""Run one agent folder and narrate it as a JSONL event stream on stdout.

Same engine, same config, same state files as `agents/<size>/run_agent.py` — it
just installs the harness's observation hooks so a watcher can see every step
as it happens, and it takes its confirmations over stdin instead of a terminal
prompt. The web server spawns one of these per run; nothing here is imported by
the benchmark.

    python -m webui.runner --agent 8b --task "Book a free hour Thursday"

Events (one JSON object per line):
    banner   agent/model/budget/toolset, once at the start
    llm_start / token / llm_end     a model call, streamed as it is written
    note     a transcript entry: plan, model, observation, feedback, repair,
             verify, done, system
    tool     an executed call, with the arguments as actually run
    world    a snapshot of the agent's folder (inbox, calendar, files, memory)
    confirm  a destructive action awaiting a y/n answer on stdin
    end      finished/summary/usage, once
    error    the run died
"""
import argparse
import datetime
import json
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)

from harness import agent as agent_mod  # noqa: E402
from harness import fs_tools  # noqa: E402
from harness import llm as llm_mod  # noqa: E402
from harness import tools as tools_mod  # noqa: E402
from harness.agent import run_harness  # noqa: E402
from harness.llm import LLM, OLLAMA_URL  # noqa: E402
from harness.memory import MemoryStore  # noqa: E402
from harness.model_router import ModelRouter, adapters_note, default_roles  # noqa: E402
from harness.world import World  # noqa: E402

AGENTS_DIR = os.path.join(PROJECT, "agents")

REAL_RULES = """

You also have tools that act on the REAL computer, inside the working root
{root}. Paths are relative to that root.
- Look before you write: call list_dir or read_file first, so you change the
  file that actually exists instead of one you assumed.
- Never delete or overwrite anything the task did not ask you to change.
- The user is asked to confirm deletes, overwrites and shell commands. If one
  is declined, do not retry it - choose another approach."""

MAX_TREE_ENTRIES = 400


def emit(event, **fields):
    line = json.dumps({"t": event, "ts": round(time.time(), 3), **fields},
                      ensure_ascii=False, default=str)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


# ------------------------------------------------------------- the folder ----

def list_files(files_dir):
    out = []
    if not os.path.isdir(files_dir):
        return out
    for name in sorted(os.listdir(files_dir)):
        path = os.path.join(files_dir, name)
        if os.path.isfile(path):
            st = os.stat(path)
            out.append({"name": name, "size": st.st_size, "mtime": st.st_mtime})
    return out


def list_tree(root):
    """A shallow listing of the real working root, for the folder panel."""
    out = []
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        depth = dirpath[len(root):].count(os.sep)
        dirnames[:] = sorted(d for d in dirnames
                             if not d.startswith(".") and d != "__pycache__")
        if depth >= 3:
            dirnames[:] = []
        for name in dirnames:
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            out.append({"name": rel, "dir": True})
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            full = os.path.join(dirpath, name)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            out.append({"name": os.path.relpath(full, root), "size": size})
        if len(out) >= MAX_TREE_ENTRIES:
            return out[:MAX_TREE_ENTRIES]
    return out


def world_snapshot(world, mem, root=None):
    snap = {
        "emails": world.emails,
        "sent": world.sent_emails,
        "events": sorted(world.events, key=lambda e: (e["date"], e["start"])),
        "messages": world.messages,
        "reminders": world.reminders,
        "files": list_files(world.files_dir),
        "memory": mem.all(),
    }
    if root:
        snap["tree"] = list_tree(root)
    return snap


# ------------------------------------------------------------ confirmation ----

class Confirmer:
    """Ask the browser instead of the terminal. Emits a confirm event and
    blocks until the server writes the answer to stdin."""

    def __init__(self):
        self.n = 0

    def __call__(self, action, detail):
        self.n += 1
        cid = self.n
        emit("confirm", id=cid, action=action, detail=detail)
        while True:
            line = sys.stdin.readline()
            if not line:  # server went away
                return False
            try:
                ans = json.loads(line)
            except ValueError:
                continue
            if ans.get("id") == cid:
                return bool(ans.get("allow"))


# -------------------------------------------------------------------- run ----

def build_llm(cfg, args, log_dir):
    use_router = args.tiers or bool(cfg.get("router"))
    if not use_router:
        return LLM(cfg["model"], num_ctx=cfg.get("num_ctx", 8192)), None
    rcfg = cfg.get("router", {})
    roles = rcfg.get("roles") or default_roles(
        base=rcfg.get("base", cfg["model"]),
        small=args.small or rcfg.get("small"),
        deep=args.deep or rcfg.get("deep", "qwen2.5:14b"))
    os.makedirs(log_dir, exist_ok=True)
    router = ModelRouter(roles=roles, num_ctx=cfg.get("num_ctx", 8192),
                         log_path=os.path.join(log_dir, "model_calls.jsonl"))
    return router, router


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--agent", required=True)
    p.add_argument("--task", required=True)
    p.add_argument("--root", default=None)
    p.add_argument("--shell", action="store_true")
    p.add_argument("--yolo", action="store_true")
    p.add_argument("--max-calls", type=int, default=None)
    p.add_argument("--tiers", action="store_true")
    p.add_argument("--small", default=None)
    p.add_argument("--deep", default=None)
    p.add_argument("--with-office", action="store_true")
    args = p.parse_args()

    folder = os.path.join(AGENTS_DIR, args.agent)
    with open(os.path.join(folder, "config.json"), encoding="utf-8-sig") as f:
        cfg = json.load(f)
    assert "127.0.0.1" in OLLAMA_URL or "localhost" in OLLAMA_URL, "refusing non-local endpoint"

    root = args.root or cfg.get("root")
    if root:
        root = fs_tools.enable(root,
                               allow_shell=args.shell or bool(cfg.get("allow_shell")),
                               confirm=None if args.yolo else Confirmer())
        if not args.with_office:
            fs_tools.restrict_to_files()
        agent_mod.EXTRA_RULES = REAL_RULES.format(root=root)
        agent_mod.EXTRA_WRITE_TOOLS = fs_tools.WRITE_TOOLS
        today = datetime.date.today()
        agent_mod.SIM_TODAY = today
        agent_mod.SIM_TODAY_HUMAN = today.strftime("%A, %B %d, %Y")
    agent_mod.MAX_CALLS = args.max_calls or cfg.get("max_calls") or (40 if root else 14)

    world = World(os.path.join(folder, "workspace"), persistent=True)
    mem = MemoryStore(os.path.join(folder, "memory", "memory.jsonl"))
    log_dir = os.path.join(folder, "logs")
    llm, router = build_llm(cfg, args, log_dir)

    tiers = None
    if router:
        tiers = {"roles": {r: s["model"] for r, s in router.roles.items()},
                 "resident": router.resident_models(),
                 "note": adapters_note()}
    emit("banner", agent=args.agent, name=cfg["name"], model=cfg["model"],
         note=cfg.get("note", ""), budget=agent_mod.MAX_CALLS, task=args.task,
         endpoint=OLLAMA_URL, root=root, shell=bool(args.shell), yolo=bool(args.yolo),
         toolset=("files only" if root and not args.with_office
                  else "files + office world" if root else "office world"),
         tiers=tiers, today=agent_mod.SIM_TODAY_HUMAN,
         tools=sorted(tools_mod.TOOLS))
    emit("world", **world_snapshot(world, mem, root))

    # ---- hooks: narrate the run without changing it ----
    state = {"call": 0}

    def on_stream(event, payload):
        if event == "start":
            state["call"] += 1
            emit("llm_start", call=state["call"], budget=agent_mod.MAX_CALLS,
                 role=payload.get("role") or "driver", model=payload.get("model"))
        elif event == "token":
            emit("token", text=payload.get("text", ""))
        else:
            emit("llm_end", role=payload.get("role") or "driver",
                 ms=payload.get("ms", 0), output_tokens=payload.get("output_tokens", 0))

    def on_note(kind, content):
        emit("note", kind=kind, content=content)

    def on_tool(name, args_, ok, obs):
        emit("tool", name=name, args=args_, ok=ok, result=obs)
        emit("world", **world_snapshot(world, mem, root))

    llm_mod.STREAM_HOOK = on_stream
    agent_mod.EVENT_HOOK = on_note
    tools_mod.TOOL_HOOK = on_tool

    try:
        ep = run_harness(llm, world, mem, args.task)
    except Exception as e:
        emit("error", message=f"{type(e).__name__}: {e}", trace=traceback.format_exc())
        raise SystemExit(1)
    finally:
        llm_mod.STREAM_HOOK = agent_mod.EVENT_HOOK = tools_mod.TOOL_HOOK = None

    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"run_{len(os.listdir(log_dir)) + 1:03d}.json")
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump({"task": args.task, "root": root, "agent": args.agent,
                   "model": cfg["model"], "via": "webui",
                   "transcript": ep.transcript, "finished": ep.finished,
                   "summary": ep.done_summary}, f, indent=1, ensure_ascii=False)

    emit("world", **world_snapshot(world, mem, root))
    emit("end", finished=ep.finished, summary=ep.done_summary,
         calls=llm.calls, budget=agent_mod.MAX_CALLS,
         output_tokens=llm.output_tokens, prompt_tokens=llm.prompt_tokens,
         wall=round(llm.wall, 1), parse_failures=ep.parse_failures,
         invalid_calls=ep.invalid_calls, tool_errors=ep.tool_errors,
         actions=[a for a in world.actions if a["tool"] != "think"],
         usage_by_role=router.usage_by_role() if router else None,
         log=os.path.relpath(log_path, PROJECT))


if __name__ == "__main__":
    main()
