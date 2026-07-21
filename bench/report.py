"""Aggregate results.json into per-model / per-capability / per-condition
summaries. Prints a markdown summary and writes summary.json for the HTML
report.

Usage: python -m bench.report [--outdir results]
"""
import argparse
import json
import os
from collections import defaultdict

CAP_LABELS = {
    "powerpoint": "PowerPoint",
    "excel": "Excel",
    "email": "Email (Gmail-style)",
    "calendar_write": "Calendar: writing",
    "calendar_read": "Calendar: reading",
    "thinking": "Thinking/reasoning",
    "messaging": "Messages",
    "reminders": "Reminders",
    "learning": "Learning (memory)",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="results")
    args = ap.parse_args()
    with open(os.path.join(args.outdir, "results.json"), encoding="utf-8") as f:
        results = json.load(f)

    models = []
    for r in results:
        if r["model"] not in models:
            models.append(r["model"])

    # ---- overall table ----
    overall = defaultdict(lambda: {"score": 0.0, "n": 0, "perfect": 0,
                                   "parse_failures": 0, "invalid_calls": 0,
                                   "tool_errors": 0, "calls": 0, "wall": 0.0,
                                   "out_tokens": 0})
    per_task = defaultdict(dict)
    per_cap = defaultdict(lambda: {"score": 0.0, "n": 0})

    for r in results:
        key = (r["model"], r["condition"])
        o = overall[key]
        o["score"] += r["score"]
        o["n"] += 1
        o["perfect"] += 1 if r["score"] >= 0.999 else 0
        o["parse_failures"] += r["parse_failures"]
        o["invalid_calls"] += r.get("invalid_calls", 0)
        o["tool_errors"] += r["tool_errors"]
        o["calls"] += r["llm_calls"]
        o["wall"] += r["wall_seconds"]
        o["out_tokens"] += r.get("output_tokens", 0)
        per_task[r["task"]][key] = r["score"]
        for cap in r["caps"]:
            c = per_cap[(cap, r["model"], r["condition"])]
            c["score"] += r["score"]
            c["n"] += 1

    lines = ["## Overall (mean task score / tasks fully passed)", "",
             "| model | raw | harness | delta |", "|---|---|---|---|"]
    summary = {"models": models, "overall": {}, "capabilities": {}, "tasks": {}}
    for m in models:
        row = {}
        for cond in ("raw", "harness"):
            o = overall.get((m, cond))
            row[cond] = {"mean": o["score"] / o["n"] if o and o["n"] else None,
                         "perfect": o["perfect"] if o else 0, "n": o["n"] if o else 0,
                         "parse_failures": o["parse_failures"] if o else 0,
                         "invalid_calls": o["invalid_calls"] if o else 0,
                         "tool_errors": o["tool_errors"] if o else 0,
                         "calls": o["calls"] if o else 0,
                         "wall": round(o["wall"], 1) if o else 0,
                         "out_tokens": o["out_tokens"] if o else 0}
        summary["overall"][m] = row
        raw_m, har_m = row["raw"]["mean"], row["harness"]["mean"]
        if raw_m is not None and har_m is not None:
            lines.append(f"| {m} | {raw_m:.2f} ({row['raw']['perfect']}/{row['raw']['n']}) "
                         f"| {har_m:.2f} ({row['harness']['perfect']}/{row['harness']['n']}) "
                         f"| {har_m - raw_m:+.2f} |")

    lines += ["", "## By capability (mean score)", "",
              "| capability | " + " | ".join(f"{m} raw | {m} harness" for m in models) + " |",
              "|" + "---|" * (1 + 2 * len(models))]
    for cap, label in CAP_LABELS.items():
        cells = [label]
        capd = {}
        for m in models:
            for cond in ("raw", "harness"):
                c = per_cap.get((cap, m, cond))
                val = c["score"] / c["n"] if c and c["n"] else None
                cells.append(f"{val:.2f}" if val is not None else "-")
                capd[f"{m}|{cond}"] = val
        summary["capabilities"][cap] = capd
        lines.append("| " + " | ".join(cells) + " |")

    lines += ["", "## By task", "",
              "| task | " + " | ".join(f"{m} raw | {m} harness" for m in models) + " |",
              "|" + "---|" * (1 + 2 * len(models))]
    for task, scores in per_task.items():
        cells = [task]
        taskd = {}
        for m in models:
            for cond in ("raw", "harness"):
                v = scores.get((m, cond))
                cells.append(f"{v:.2f}" if v is not None else "-")
                taskd[f"{m}|{cond}"] = v
        summary["tasks"][task] = taskd
        lines.append("| " + " | ".join(cells) + " |")

    md = "\n".join(lines)
    print(md)
    with open(os.path.join(args.outdir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)
    with open(os.path.join(args.outdir, "SUMMARY.md"), "w", encoding="utf-8") as f:
        f.write(md + "\n")


if __name__ == "__main__":
    main()
