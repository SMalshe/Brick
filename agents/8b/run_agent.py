"""Self-contained ON-DEVICE agent for this folder's model.

Everything runs locally: inference via the local Ollama server on
127.0.0.1:11434 (weights in C:\\Users\\Lab User\\SAIL\\ollama), files and
memory stay in this folder. Nothing is sent to any cloud service.

Usage:
    run.ps1 "Summarize my Wednesday meetings and message Jordan"
    run.ps1            <- interactive prompt

Real-computer mode (off by default) gives the agent the same kind of access
Claude Code / Codex have - read, write, move, delete and search real files,
optionally run shell commands - scoped to one folder:

    run.ps1 --root C:\\Users\\Lab User\\Desktop\\sandbox "Tidy up these notes"
    run.ps1 --root . --shell "What changed in this project today?"

Without --root the agent only sees the simulated office world, as before.

Flags:
    --root PATH     working root; every path the agent touches must be inside it
    --shell         also allow run_command (PowerShell), still confirmed
    --yolo          skip confirmation prompts for overwrite/delete/move/shell
    --max-calls N   LLM call budget (default 14 simulated, 40 with --root)

State persists between runs:
    workspace/state.json   inbox, calendar, sent mail, messages, reminders
    workspace/files/       real .pptx / .xlsx the agent creates
    memory/memory.jsonl    long-term memory (learning)
"""
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, PROJECT)

from harness import agent as agent_mod  # noqa: E402
from harness import fs_tools  # noqa: E402
from harness.agent import run_harness  # noqa: E402
from harness.llm import LLM, OLLAMA_URL  # noqa: E402
from harness.memory import MemoryStore  # noqa: E402
from harness.model_router import ModelRouter, adapters_note, default_roles  # noqa: E402
from harness.world import World  # noqa: E402

REAL_RULES = """

You also have tools that act on the REAL computer, inside the working root
{root}. Paths are relative to that root.
- Look before you write: call list_dir or read_file first, so you change the
  file that actually exists instead of one you assumed.
- Never delete or overwrite anything the task did not ask you to change.
- The user is asked to confirm deletes, overwrites and shell commands. If one
  is declined, do not retry it - choose another approach."""


def parse_flags(argv):
    opts = {"root": None, "shell": False, "yolo": False, "max_calls": None,
            "tiers": False, "small": None, "deep": None, "office": False}
    rest = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--root" and i + 1 < len(argv):
            opts["root"] = argv[i + 1]
            i += 2
        elif a == "--shell":
            opts["shell"] = True
            i += 1
        elif a == "--yolo":
            opts["yolo"] = True
            i += 1
        elif a == "--max-calls" and i + 1 < len(argv):
            opts["max_calls"] = int(argv[i + 1])
            i += 2
        elif a == "--tiers":
            opts["tiers"] = True
            i += 1
        elif a == "--small" and i + 1 < len(argv):
            opts["small"] = argv[i + 1]
            opts["tiers"] = True
            i += 2
        elif a == "--deep" and i + 1 < len(argv):
            opts["deep"] = argv[i + 1]
            opts["tiers"] = True
            i += 2
        elif a == "--with-office":
            opts["office"] = True
            i += 1
        else:
            rest.append(a)
            i += 1
    return opts, " ".join(rest).strip()


def build_llm(cfg, opts):
    """A plain single-model LLM by default; a tiered ModelRouter when --tiers
    (or a config 'router' block) is set. The router's default lineup keeps ONE
    model resident (driver/router/verifier share the base); a heavier 'deep'
    tier is load-on-demand and evicted after use."""
    use_router = opts["tiers"] or bool(cfg.get("router"))
    if not use_router:
        return LLM(cfg["model"], num_ctx=cfg.get("num_ctx", 8192)), None
    rcfg = cfg.get("router", {})
    roles = rcfg.get("roles") or default_roles(
        base=rcfg.get("base", cfg["model"]),
        small=opts["small"] or rcfg.get("small"),
        deep=opts["deep"] or rcfg.get("deep", "qwen2.5:14b"))
    log_path = os.path.join(HERE, "logs", "model_calls.jsonl")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    router = ModelRouter(roles=roles, num_ctx=cfg.get("num_ctx", 8192), log_path=log_path)
    return router, router


def confirm(action, detail):
    print(f"\n  the agent wants to {action}:\n    {detail}")
    try:
        return input("  allow? [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def main():
    with open(os.path.join(HERE, "config.json"), encoding="utf-8-sig") as f:
        cfg = json.load(f)
    assert "127.0.0.1" in OLLAMA_URL or "localhost" in OLLAMA_URL, "refusing non-local endpoint"

    opts, task = parse_flags(sys.argv[1:])
    root = opts["root"] or cfg.get("root")
    if not task:
        task = input("Task for the agent: ").strip()
    if not task:
        print("No task given.")
        return

    if root:
        root = fs_tools.enable(root,
                               allow_shell=opts["shell"] or bool(cfg.get("allow_shell")),
                               confirm=None if opts["yolo"] else confirm)
        if not opts["office"]:
            fs_tools.restrict_to_files()  # a real-folder agent shouldn't fiddle with a fake inbox
        agent_mod.EXTRA_RULES = REAL_RULES.format(root=root)
        agent_mod.EXTRA_WRITE_TOOLS = fs_tools.WRITE_TOOLS
        # a real-file agent should reason about the real date, not the fixed
        # benchmark clock
        today = datetime.date.today()
        agent_mod.SIM_TODAY = today
        agent_mod.SIM_TODAY_HUMAN = today.strftime("%A, %B %d, %Y")
    agent_mod.MAX_CALLS = opts["max_calls"] or cfg.get("max_calls") or (40 if root else 14)

    world = World(os.path.join(HERE, "workspace"), persistent=True)
    mem = MemoryStore(os.path.join(HERE, "memory", "memory.jsonl"))
    llm, router = build_llm(cfg, opts)

    print(f"[{cfg['name']}] fully on-device via {OLLAMA_URL}")
    if router:
        print(f"  model tiers: " + ", ".join(f"{r}={s['model']}" for r, s in router.roles.items()))
        print(f"  resident at once: {', '.join(router.resident_models())}  (others load on demand, evict after)")
        print(f"  {adapters_note()}")
    else:
        print(f"  model: {cfg['model']}")
    if root:
        mode = "read/write" + (" + shell" if opts["shell"] or cfg.get("allow_shell") else "")
        toolset = "files + office world" if opts["office"] else "files only (office tools dropped)"
        print(f"  real files: {mode} inside {root}"
              + ("   [--yolo: confirmations off]" if opts["yolo"] else ""))
        print(f"  toolset: {toolset}")
    print(f"  budget: {agent_mod.MAX_CALLS} LLM calls")
    ep = run_harness(llm, world, mem, task)

    print("\n--- run finished ---")
    print(f"finished cleanly: {ep.finished}   llm calls: {llm.calls}   "
          f"tokens out: {llm.output_tokens}   wall: {llm.wall:.0f}s")
    if router:
        for role, u in router.usage_by_role().items():
            print(f"  tier {role:<8} {u['model']:<16} {u['calls']:>2} calls  "
                  f"{u['output_tokens']:>5} out-tok  {u['ms'] / 1000:>5.1f}s")
    if ep.done_summary:
        print(f"agent summary: {ep.done_summary}")
    acts = [a for a in world.actions if a["tool"] != "think"]
    if acts:
        print("actions taken:")
        for a in acts:
            print(f"  - {a['tool']}({json.dumps(a['args'], ensure_ascii=False, default=str)[:120]})"
                  f" -> {'ok' if a['ok'] else 'ERROR'}")
    print(f"files: {world.files_dir}")
    log_dir = os.path.join(HERE, "logs")
    os.makedirs(log_dir, exist_ok=True)
    n = len(os.listdir(log_dir)) + 1
    log_path = os.path.join(log_dir, f"run_{n:03d}.json")
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump({"task": task, "root": root, "transcript": ep.transcript,
                   "finished": ep.finished, "summary": ep.done_summary}, f,
                  indent=1, ensure_ascii=False)
    print(f"transcript: {log_path}")


if __name__ == "__main__":
    main()
