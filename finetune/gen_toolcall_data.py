"""Source A: synthetic training data for the tool-calling LoRA (backbone).

Generates (context -> correct tool-call JSON) pairs by slot-filling natural
tasks and emitting the ground-truth call deterministically. Because we choose
the slot values, the target call is known-correct with no verification needed.

Each row carries the REAL serving system prompt (harness tool docs), so the
adapter trains on exactly the context it will see at inference. The assistant
turn is the pristine call in the harness SHAPE. Output is chat-format JSONL,
ready for Unsloth / trl SFTTrainer with assistant-only loss.

Deliberately does NOT use the 12 benchmark tasks — keep those as held-out eval.
Sources B (harvest results/ transcripts) and C (distill from 32B) are separate
scripts; this is the free, exact, fully-local backbone.

    python -m finetune.gen_toolcall_data --n 1200 --out finetune/data/toolcall.jsonl
"""
import argparse
import datetime
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from harness.agent import HARNESS_SYSTEM, SHAPE  # noqa: E402
from harness.tools import tool_docs  # noqa: E402
from harness.world import SIM_TODAY, SIM_TODAY_HUMAN  # noqa: E402

SYSTEM = HARNESS_SYSTEM.format(today=SIM_TODAY_HUMAN, shape=SHAPE,
                               docs=tool_docs(with_examples=True),
                               memory_block="", extra_rules="")

PEOPLE = [("Sam", "sam@corp.com"), ("Dana", "dana@corp.com"), ("Priya", "priya@corp.com"),
          ("Jordan", "jordan@corp.com"), ("Mia", "mia@corp.com"), ("Alex", "alex@corp.com")]
TITLES = ["Budget review", "Design sync", "1:1", "Sprint planning", "Client call",
          "Retro", "Standup", "Roadmap review", "Interview", "Vendor call"]
SUBJECTS = ["Q3 numbers", "the proposal", "the launch plan", "the invoice",
            "meeting notes", "the contract", "next steps", "the schedule"]
MSG_TEXTS = ["Running 5 minutes late.", "On my way.", "Can we push to 3pm?",
             "Confirmed, see you then.", "Sending the file now.", "Thanks, got it."]
REMINDERS = ["send the invoice", "call the vendor", "submit the report",
             "renew the license", "follow up with Dana", "book the venue"]
TIMES = [("9am", "09:00"), ("9:30am", "09:30"), ("10am", "10:00"), ("11am", "11:00"),
         ("noon", "12:00"), ("1pm", "13:00"), ("2pm", "14:00"), ("2:30pm", "14:30"),
         ("3pm", "15:00"), ("4pm", "16:00"), ("4:30pm", "16:30"), ("5pm", "17:00")]
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


def pick_date(rng):
    """Return (natural_phrase, iso) that agree given 'today' in the system prompt."""
    d = rng.randint(1, 7)
    date = SIM_TODAY + datetime.timedelta(days=d)
    iso = date.isoformat()
    weekday = date.strftime("%A")
    opts = [f"on {MONTHS[date.month - 1]} {date.day}"]
    if d == 1:
        opts += ["tomorrow", "tomorrow"]
    else:
        opts += [weekday, f"next {weekday}", weekday]
    return rng.choice(opts), iso


def add_minutes(hhmm, minutes):
    t = datetime.datetime.strptime(hhmm, "%H:%M") + datetime.timedelta(minutes=minutes)
    return t.strftime("%H:%M")


def g_add_event(rng):
    title = rng.choice(TITLES)
    name, email = rng.choice(PEOPLE)
    phrase, iso = pick_date(rng)
    tphrase, start = rng.choice(TIMES)
    dur = rng.choice([30, 60])
    end = add_minutes(start, dur)
    dur_txt = "30 minutes" if dur == 30 else "an hour"
    task = f"Schedule a {dur_txt} {title.lower()} with {name} {phrase} at {tphrase}."
    args = {"title": title, "date": iso, "start_time": start, "end_time": end,
            "attendees": [email]}
    return task, "add_event", args, f"Booking the {title.lower()} on {iso} at {start}."


def g_set_reminder(rng):
    text = rng.choice(REMINDERS)
    phrase, iso = pick_date(rng)
    tphrase, tm = rng.choice(TIMES)
    task = f"Remind me to {text} {phrase} at {tphrase}."
    return task, "set_reminder", {"text": text, "date": iso, "time": tm}, \
        f"Setting a reminder for {iso} at {tm}."


def g_send_message(rng):
    name, _ = rng.choice(PEOPLE)
    text = rng.choice(MSG_TEXTS)
    task = f"Message {name}: {text}"
    return task, "send_message", {"to": name.lower(), "text": text}, \
        f"Messaging {name}."


def g_send_email(rng):
    name, email = rng.choice(PEOPLE)
    subj = rng.choice(SUBJECTS)
    body = rng.choice(["Sounds good, thanks.", "Please see the attached.",
                       "Let's confirm the details.", "Here are the latest figures."])
    task = f"Email {name} about {subj}."
    args = {"to": email, "subject": subj[0].upper() + subj[1:], "body": body}
    return task, "send_email", args, f"Emailing {name} about {subj}."


def g_list_events(rng):
    phrase, iso = pick_date(rng)
    task = rng.choice([f"What's on my calendar {phrase}?",
                       f"List my meetings {phrase}.",
                       f"Show my schedule {phrase}."])
    return task, "list_events", {"date": iso}, f"Listing events for {iso}."


def g_recover_after_error(rng):
    """Teach in-weights repair: previous turn errored, target is the fixed call."""
    title = rng.choice(TITLES)
    _, iso = pick_date(rng)
    _, start = rng.choice(TIMES)
    end = add_minutes(start, 60)
    good = {"title": title, "date": iso, "start_time": start, "end_time": end}
    bad = json.dumps({"tool": "add_event", "title": title, "date": iso,
                      "start_time": start})  # args at top level, missing end_time
    err = ("ERROR: missing required parameter 'end_time' (string 24h HH:MM). "
           "Correct shape: put fields under 'args'.")
    return ("__REPAIR__", "add_event", good,
            f"Fixing the call: nesting args and adding end_time {end}.",
            bad, err)


GENERATORS = [g_add_event, g_set_reminder, g_send_message, g_send_email, g_list_events]


def build_row(gen, rng):
    out = gen(rng)
    if out[0] == "__REPAIR__":  # multi-turn: user task, bad assistant, error, fixed call
        _, tool, args, thought, bad_reply, err = out
        title = args["title"]
        user = f"TASK: Book the {title.lower()} on {args['date']} from {args['start_time']}."
        target = json.dumps({"thought": thought, "tool": tool, "args": args}, ensure_ascii=False)
        return {"messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user},
            {"role": "assistant", "content": bad_reply},
            {"role": "user", "content": f"OBSERVATION: {err}"},
            {"role": "assistant", "content": target},
        ]}
    task, tool, args, thought = out
    target = json.dumps({"thought": thought, "tool": tool, "args": args}, ensure_ascii=False)
    return {"messages": [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"TASK: {task}\n\nMake the first tool call now. "
                                    f"Reply with exactly one JSON object: {SHAPE}"},
        {"role": "assistant", "content": target},
    ]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1200)
    ap.add_argument("--out", default=os.path.join(HERE, "data", "toolcall.jsonl"))
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--repair-frac", type=float, default=0.15)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    rows, counts = [], {}
    for i in range(args.n):
        gen = g_recover_after_error if rng.random() < args.repair_frac else rng.choice(GENERATORS)
        row = build_row(gen, rng)
        rows.append(row)
        tool = json.loads(row["messages"][-1]["content"])["tool"]
        counts[tool] = counts.get(tool, 0) + 1

    with open(args.out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"wrote {len(rows)} examples -> {args.out}")
    print("tool distribution:", dict(sorted(counts.items())))
    print("\n--- 2 sample rows (assistant target on last line) ---")
    for r in rows[:2]:
        print(f"\nUSER: {r['messages'][1]['content'][:90]}...")
        print(f"TARGET: {r['messages'][-1]['content']}")


if __name__ == "__main__":
    main()
