"""Benchmark runner: models x conditions x tasks.

Usage:
    python -m bench.run_bench --models llama3.2:1b llama3.2:3b llama3.1:8b \
        --conditions raw harness [--tasks pptx_basic cal_add] [--outdir results]

Each (model, condition) pair gets a fresh memory file shared across its tasks
(so learn_store -> learn_use works), and each task gets a fresh seeded world.
Results append to <outdir>/results.json after every task, so partial runs are
still reportable. Transcripts are saved per task.
"""
import argparse
import inspect
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.agent import MAX_CALLS, run_harness, run_raw  # noqa: E402
from harness.llm import LLM  # noqa: E402
from harness.memory import MemoryStore  # noqa: E402
from harness.world import World  # noqa: E402
from bench.tasks import TASKS  # noqa: E402


def slug(model):
    return model.replace(":", "_").replace("/", "_")


def save_transcript(path, ep, task, model, condition, score, checks):
    lines = [f"# {task['id']}  |  {model}  |  {condition}",
             f"**Score: {score:.2f}**  (finished: {ep.finished})", "",
             "| check | passed |", "|---|---|"]
    for desc, ok in checks:
        lines.append(f"| {desc} | {'PASS' if ok else 'FAIL'} |")
    lines.append("")
    for item in ep.transcript:
        lines.append(f"### {item['kind']}")
        lines.append("```")
        lines.append(str(item["content"]))
        lines.append("```")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--conditions", nargs="+", default=["raw", "harness"])
    ap.add_argument("--tasks", nargs="+", default=None)
    ap.add_argument("--outdir", default="results")
    args = ap.parse_args()

    tasks = TASKS if not args.tasks else [t for t in TASKS if t["id"] in args.tasks]
    os.makedirs(args.outdir, exist_ok=True)
    results_path = os.path.join(args.outdir, "results.json")
    results = []
    if os.path.exists(results_path):
        with open(results_path, encoding="utf-8") as f:
            results = json.load(f)

    for model in args.models:
        for condition in args.conditions:
            run_dir = os.path.join(args.outdir, slug(model), condition)
            os.makedirs(run_dir, exist_ok=True)
            mem_path = os.path.join(run_dir, "memory.jsonl")
            if os.path.exists(mem_path):
                os.remove(mem_path)  # fresh memory per (model, condition) run
            for task in tasks:
                # skip if already recorded (lets us resume interrupted runs)
                if any(r["model"] == model and r["condition"] == condition
                       and r["task"] == task["id"] for r in results):
                    print(f"[skip] {model} {condition} {task['id']} already done", flush=True)
                    continue
                workdir = os.path.join(run_dir, task["id"])
                os.makedirs(workdir, exist_ok=True)
                world = World(workdir)
                mem = MemoryStore(mem_path)
                llm = LLM(model)
                t0 = time.time()
                err = None
                try:
                    runner = run_harness if condition == "harness" else run_raw
                    ep = runner(llm, world, mem, task["prompt"])
                except Exception as e:
                    from harness.agent import Episode
                    ep = Episode()
                    err = f"{type(e).__name__}: {e}"
                    ep.note("runner_error", err)
                    world.snapshot()
                wall = time.time() - t0
                try:
                    fn = task["grade"]
                    if "mem" in inspect.signature(fn).parameters:
                        score, checks = fn(world, mem=MemoryStore(mem_path))
                    else:
                        score, checks = fn(world)
                except Exception as e:
                    score, checks = 0.0, [(f"grader crashed: {e}", False)]
                rec = {"model": model, "condition": condition, "task": task["id"],
                       "caps": task["caps"], "score": round(score, 4),
                       "checks": [[d, bool(ok)] for d, ok in checks],
                       "finished": ep.finished, "llm_calls": llm.calls,
                       "parse_failures": ep.parse_failures,
                       "invalid_calls": ep.invalid_calls,
                       "tool_errors": ep.tool_errors,
                       "prompt_tokens": llm.prompt_tokens,
                       "output_tokens": llm.output_tokens,
                       "wall_seconds": round(wall, 1), "error": err,
                       "max_calls": MAX_CALLS}
                results.append(rec)
                with open(results_path, "w", encoding="utf-8") as f:
                    json.dump(results, f, indent=1)
                save_transcript(os.path.join(workdir, "transcript.md"),
                                ep, task, model, condition, score, checks)
                print(f"[{model} | {condition}] {task['id']}: score={score:.2f} "
                      f"calls={llm.calls} wall={wall:.0f}s"
                      + (f" ERROR={err}" if err else ""), flush=True)
    print("ALL DONE", flush=True)


if __name__ == "__main__":
    main()
