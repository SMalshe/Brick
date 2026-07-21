"""Self-contained training-data generator for the tool-calling LoRA.

Unlike the project's finetune/gen_toolcall_data.py (which imports the live
harness), this script depends on NOTHING outside this folder — it reads the
frozen serving prompt from system_prompt.txt and emits chat-format JSONL where
the assistant turn is the correct tool call. So the training package can
regenerate its own data on any machine.

    python make_data.py --n 1200 --out data/toolcall.jsonl

The frozen system_prompt.txt is the context the adapter trains against; it must
match what you serve. If you change the harness's tools or prompt, re-snapshot
it (see README) and regenerate.
"""
import argparse
import datetime
import json
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
SHAPE = '{"thought": "<why>", "tool": "<tool_name>", "args": { ... }}'
# Anchor matches the "Today is ..." line baked into system_prompt.txt.
TODAY = datetime.date(2026, 7, 20)  # Monday

with open(os.path.join(HERE, "system_prompt.txt"), encoding="utf-8") as f:
    SYSTEM = f.read()

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
    # 1..6 so the referenced weekday is never today's (Monday) — avoids the
    # "Monday said on a Monday = today or next week?" ambiguity in the labels.
    d = rng.randint(1, 6)
    date = TODAY + datetime.timedelta(days=d)
    weekday = date.strftime("%A")
    opts = [f"on {MONTHS[date.month - 1]} {date.day}"]
    if d == 1:
        opts += ["tomorrow", "tomorrow"]
    else:
        opts += [weekday, f"next {weekday}", weekday]
    return rng.choice(opts), date.isoformat()


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
    return f"Message {name}: {text}", "send_message", {"to": name.lower(), "text": text}, \
        f"Messaging {name}."


def g_send_email(rng):
    name, email = rng.choice(PEOPLE)
    subj = rng.choice(SUBJECTS)
    body = rng.choice(["Sounds good, thanks.", "Please see the attached.",
                       "Let's confirm the details.", "Here are the latest figures."])
    args = {"to": email, "subject": subj[0].upper() + subj[1:], "body": body}
    return f"Email {name} about {subj}.", "send_email", args, f"Emailing {name} about {subj}."


def g_list_events(rng):
    phrase, iso = pick_date(rng)
    task = rng.choice([f"What's on my calendar {phrase}?", f"List my meetings {phrase}.",
                       f"Show my schedule {phrase}."])
    return task, "list_events", {"date": iso}, f"Listing events for {iso}."


def g_recover_after_error(rng):
    title = rng.choice(TITLES)
    _, iso = pick_date(rng)
    _, start = rng.choice(TIMES)
    end = add_minutes(start, 60)
    good = {"title": title, "date": iso, "start_time": start, "end_time": end}
    bad = json.dumps({"tool": "add_event", "title": title, "date": iso, "start_time": start})
    err = ("ERROR: missing required parameter 'end_time' (string 24h HH:MM). "
           "Correct shape: put fields under 'args'.")
    return ("__REPAIR__", "add_event", good,
            f"Fixing the call: nesting args and adding end_time {end}.", bad, err)


GENERATORS = [g_add_event, g_set_reminder, g_send_message, g_send_email, g_list_events]


def build_row(gen, rng):
    out = gen(rng)
    if out[0] == "__REPAIR__":
        _, tool, args, thought, bad_reply, err = out
        user = f"TASK: Book the {args['title'].lower()} on {args['date']} from {args['start_time']}."
        target = json.dumps({"thought": thought, "tool": tool, "args": args}, ensure_ascii=False)
        return {"messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user},
            {"role": "assistant", "content": bad_reply},
            {"role": "user", "content": f"OBSERVATION: {err}"},
            {"role": "assistant", "content": target}]}
    task, tool, args, thought = out
    target = json.dumps({"thought": thought, "tool": tool, "args": args}, ensure_ascii=False)
    return {"messages": [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"TASK: {task}\n\nMake the first tool call now. "
                                    f"Reply with exactly one JSON object: {SHAPE}"},
        {"role": "assistant", "content": target}]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1200)
    ap.add_argument("--out", default=os.path.join(HERE, "data", "toolcall.jsonl"))
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--repair-frac", type=float, default=0.15)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)

    counts, n = {}, 0
    with open(a.out, "w", encoding="utf-8") as f:
        for _ in range(a.n):
            gen = g_recover_after_error if rng.random() < a.repair_frac else rng.choice(GENERATORS)
            row = build_row(gen, rng)
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            tool = json.loads(row["messages"][-1]["content"])["tool"]
            counts[tool] = counts.get(tool, 0) + 1
            n += 1
    print(f"wrote {n} examples -> {a.out}")
    print("tool distribution:", dict(sorted(counts.items())))


if __name__ == "__main__":
    main()
