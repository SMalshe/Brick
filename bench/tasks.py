"""The 12 benchmark tasks. Each: id, capabilities, prompt, grade(world).

grade(world) -> (score 0..1, [(check_description, passed), ...])
Tasks run in this order; the two learning tasks rely on that (store, then use,
in separate episodes sharing one memory file).
"""
from . import grade as g


def _score(checks):
    passed = sum(1 for _, ok in checks if ok)
    return (passed / len(checks) if checks else 0.0), checks


# ---------------------------------------------------------------- PowerPoint

def grade_pptx_basic(world):
    checks = []
    path = g._find_file(world.files_dir, "q3", ".pptx")
    checks.append(("q3_review.pptx was created", path is not None))
    if path:
        slides = g.pptx_slides(path)
        checks.append(("deck has exactly 5 slides", len(slides) == 5))
        wanted = ["q3 business review", "agenda", "sales", "marketing", "next steps"]
        for i, w in enumerate(wanted):
            ok = i < len(slides) and w in slides[i][0].lower()
            checks.append((f"slide {i + 1} titled '{w}'", ok))
        for i in range(1, 5):
            ok = i < len(slides) and len(slides[i][1]) >= 3
            checks.append((f"slide {i + 1} has >= 3 bullets", ok))
    return _score(checks)


def grade_pptx_from_email(world):
    checks = []
    path = g._find_file(world.files_dir, "sales_summary", ".pptx")
    checks.append(("sales_summary.pptx was created", path is not None))
    if path:
        slides = g.pptx_slides(path)
        text = g._norm_num_text(g.pptx_all_text(path))
        checks.append(("deck has >= 4 slides (title + 3 regions)", len(slides) >= 4))
        checks.append(("West revenue $1,240,000 appears", "1240000" in text or "1.24m" in text))
        checks.append(("East revenue $845,000 appears", "845000" in text or "845k" in text))
        checks.append(("Online revenue $610,000 appears", "610000" in text or "610k" in text))
        titles = " ".join(t.lower() for t, _ in slides)
        checks.append(("region names appear as slide titles",
                       all(r in titles for r in ("west", "east", "online"))))
    return _score(checks)


# -------------------------------------------------------------------- Excel

def grade_xlsx_basic(world):
    checks = []
    path = g._find_file(world.files_dir, "budget", ".xlsx")
    checks.append(("budget.xlsx was created", path is not None))
    if path:
        rows = g.xlsx_cells(path)
        nums = g.sheet_numbers(rows)
        text = g.sheet_text(rows)
        for item, cost in [("laptops", 3200), ("software", 1150), ("training", 800), ("travel", 2400)]:
            checks.append((f"row for {item} with cost {cost}", item in text and float(cost) in nums))
        checks.append(("a Total row exists", "total" in text))
        checks.append(("total equals 7550", 7550.0 in nums))
    return _score(checks)


def grade_xlsx_from_email(world):
    checks = []
    path = g._find_file(world.files_dir, "expenses", ".xlsx")
    checks.append(("expenses.xlsx was created", path is not None))
    if path:
        rows = g.xlsx_cells(path)
        nums = g.sheet_numbers(rows)
        text = g.sheet_text(rows)
        checks.append(("CloudHost $230.00 row", "cloudhost" in text and 230.0 in nums))
        checks.append(("OfficeMax $87.50 row", "officemax" in text and 87.5 in nums))
        checks.append(("Delta $412.30 row", "delta" in text and 412.3 in nums))
        checks.append(("header mentions Date/Vendor/Amount",
                       all(h in text for h in ("date", "vendor", "amount"))))
        checks.append(("total equals 729.80", 729.8 in nums))
    return _score(checks)


# -------------------------------------------------------------------- Email

def grade_email_reply(world):
    checks = []
    sent = world.sent_emails
    checks.append(("exactly one email was sent", len(sent) == 1))
    to_mia = [e for e in sent if "mia@corp.com" in e["to"].lower()]
    checks.append(("reply went to mia@corp.com (sender of the most recent Northwind email)",
                   bool(to_mia)))
    if to_mia:
        e = to_mia[0]
        blob = (e["subject"] + " " + e["body"]).lower()
        checks.append(("reply confirms attendance",
                       any(w in blob for w in ("confirm", "attend", "i will be there",
                                               "i'll be there", "yes", "make it", "count me in"))))
        checks.append(("reply references Northwind/kickoff",
                       "northwind" in blob or "kickoff" in blob))
    return _score(checks)


# ----------------------------------------------------------------- Calendar

def grade_cal_add(world):
    checks = []
    ev = g.find_event(world, title_has="design sync")
    checks.append(("an event titled 'Design sync' was added", ev is not None))
    if ev:
        checks.append(("date is Tuesday 2026-07-21", ev["date"] == "2026-07-21"))
        checks.append(("starts 14:00", ev["start"] == "14:00"))
        checks.append(("ends 15:00", ev["end"] == "15:00"))
        att = " ".join(a.lower() for a in ev["attendees"])
        checks.append(("alice@corp.com invited", "alice@corp.com" in att))
        checks.append(("bob@corp.com invited", "bob@corp.com" in att))
    return _score(checks)


def grade_cal_freeslot(world):
    checks = []
    ev = g.find_event(world, title_has="deep work")
    checks.append(("an event titled 'Deep work' was added", ev is not None))
    if ev:
        seeded_thu = [e for e in world.events if e["id"] in ("c5", "c6", "c7")]
        checks.append(("on Thursday 2026-07-23", ev["date"] == "2026-07-23"))
        dur = g.minutes(ev["end"]) - g.minutes(ev["start"])
        checks.append(("exactly one hour long", dur == 60))
        checks.append(("within 09:00-17:00",
                       g.minutes(ev["start"]) >= 540 and g.minutes(ev["end"]) <= 1020))
        checks.append(("does not overlap existing meetings", not g.overlaps(ev, seeded_thu)))
    return _score(checks)


def grade_cal_brief(world):
    checks = []
    msgs = [m for m in world.messages if "jordan" in m["to"].lower()]
    checks.append(("a chat message was sent to Jordan", bool(msgs)))
    if msgs:
        text = msgs[-1]["text"].lower()
        for t in ("design review", "1:1 with sam", "marketing sync"):
            checks.append((f"message mentions '{t}'", t.split(" with ")[0] in text or t in text))
        i1, i2, i3 = text.find("design review"), text.find("sam"), text.find("marketing")
        checks.append(("meetings listed in chronological order",
                       -1 < i1 < i2 < i3))
    return _score(checks)


# ----------------------------------------------------- Messages & reminders

def grade_remind_msg(world):
    checks = []
    rems = [r for r in world.reminders if "tps" in r["text"].lower()]
    checks.append(("a TPS-report reminder was set", bool(rems)))
    if rems:
        r = rems[0]
        checks.append(("reminder on Friday 2026-07-24", r["date"] == "2026-07-24"))
        checks.append(("reminder at 15:00", r["time"] == "15:00"))
    msgs = [m for m in world.messages if "casey" in m["to"].lower()]
    checks.append(("a message was sent to Casey", bool(msgs)))
    if msgs:
        text = msgs[-1]["text"].lower()
        checks.append(("message mentions the TPS report", "tps" in text))
        checks.append(("message mentions Friday / end of day",
                       "friday" in text or "eod" in text or "end of day" in text))
    return _score(checks)


# ----------------------------------------------------------------- Learning

def grade_learn_store(world, mem=None):
    checks = []
    facts = " | ".join(mem.all()).lower() if mem else ""
    checks.append(("save_memory was used", bool(mem and mem.all())))
    checks.append(("saved fact mentions 25-minute preference", "25" in facts))
    checks.append(("saved fact mentions the no-before-10am rule",
                   "10am" in facts or "10:00" in facts or "10 am" in facts))
    return _score(checks)


def grade_learn_use(world, mem=None):
    checks = []
    evs = g.new_events(world)
    ev = None
    for e in evs:
        if "priya" in (e["title"] + " " + " ".join(e["attendees"])).lower():
            ev = e
    if ev is None and evs:
        ev = evs[0]
    checks.append(("a sync event was booked", ev is not None))
    if ev:
        checks.append(("on tomorrow 2026-07-21", ev["date"] == "2026-07-21"))
        start = g.minutes(ev["start"])
        checks.append(("respects learned rule: not before 10:00 (but still morning)",
                       600 <= start <= 719))
        dur = g.minutes(ev["end"]) - start
        checks.append(("respects learned preference: 25 minutes long", dur == 25))
    return _score(checks)


# --------------------------------------------------------------- Multi-step

def grade_multi_offsite(world):
    checks = []
    ev = g.find_event(world, title_has="offsite")
    checks.append(("offsite added to calendar", ev is not None))
    if ev:
        checks.append(("on Friday 2026-07-24", ev["date"] == "2026-07-24"))
        checks.append(("9:00 to 16:00", ev["start"] == "09:00" and ev["end"] == "16:00"))
    sent = [e for e in world.sent_emails if "ceo@corp.com" in e["to"].lower()]
    checks.append(("reply sent to ceo@corp.com", bool(sent)))
    if sent:
        blob = (sent[-1]["subject"] + " " + sent[-1]["body"]).lower()
        checks.append(("reply confirms attendance",
                       any(w in blob for w in ("confirm", "attend", "be there", "join", "count me in", "yes"))))
    path = g._find_file(world.files_dir, "offsite", ".pptx")
    checks.append(("offsite.pptx created", path is not None))
    if path:
        text = g.pptx_all_text(path).lower()
        checks.append(("slide mentions the date (July 24)", "july 24" in text or "2026-07-24" in text or "jul 24" in text))
        checks.append(("slide mentions Lakeside Pavilion", "lakeside" in text))
    return _score(checks)


TASKS = [
    {"id": "pptx_basic", "caps": ["powerpoint"],
     "prompt": "Create a PowerPoint file named q3_review.pptx with exactly 5 slides in this order: "
               "(1) a title slide 'Q3 Business Review', (2) 'Agenda', (3) 'Sales', (4) 'Marketing', "
               "(5) 'Next Steps'. Slides 2 through 5 must each have at least 3 bullet points.",
     "grade": grade_pptx_basic},
    {"id": "pptx_from_email", "caps": ["powerpoint", "email"],
     "prompt": "Dana emailed me the final Q3 sales numbers. Create sales_summary.pptx with a title "
               "slide, then one slide per region (West, East, Online), each showing that region's "
               "revenue as a bullet point.",
     "grade": grade_pptx_from_email},
    {"id": "xlsx_basic", "caps": ["excel"],
     "prompt": "Create budget.xlsx with two columns, Item and Cost, containing: Laptops 3200, "
               "Software licenses 1150, Training 800, Travel 2400. Add a final Total row with the sum.",
     "grade": grade_xlsx_basic},
    {"id": "xlsx_from_email", "caps": ["excel", "email"],
     "prompt": "Find the three purchase receipts in my inbox and create expenses.xlsx with columns "
               "Date, Vendor, Amount - one row per receipt - plus a final Total row.",
     "grade": grade_xlsx_from_email},
    {"id": "email_reply", "caps": ["email"],
     "prompt": "Look through my inbox for the most recent email about the Northwind project and "
               "send a reply to its sender confirming that I will attend the kickoff.",
     "grade": grade_email_reply},
    {"id": "cal_add", "caps": ["calendar_write"],
     "prompt": "Add a meeting called 'Design sync' to my calendar on Tuesday July 21 from 2pm to 3pm "
               "with attendees alice@corp.com and bob@corp.com.",
     "grade": grade_cal_add},
    {"id": "cal_freeslot", "caps": ["calendar_write", "thinking"],
     "prompt": "Find a free one-hour slot in my calendar on Thursday July 23 between 9:00 and 17:00 "
               "and book it as 'Deep work'.",
     "grade": grade_cal_freeslot},
    {"id": "cal_brief", "caps": ["calendar_read", "messaging", "thinking"],
     "prompt": "Check my calendar for Wednesday July 22 and send Jordan a chat message summarizing "
               "my meetings that day in chronological order.",
     "grade": grade_cal_brief},
    {"id": "remind_msg", "caps": ["reminders", "messaging"],
     "prompt": "Set a reminder for Friday July 24 at 3pm to submit the TPS report, and send Casey a "
               "message letting them know the TPS report will be done by end of day Friday.",
     "grade": grade_remind_msg},
    {"id": "learn_store", "caps": ["learning"],
     "prompt": "Please remember these preferences for all future scheduling: I like meetings to be "
               "25 minutes long, and I never schedule anything before 10am.",
     "grade": grade_learn_store},
    {"id": "learn_use", "caps": ["learning", "calendar_write"],
     "prompt": "Book a quick sync with Priya tomorrow morning.",
     "grade": grade_learn_use},
    {"id": "multi_offsite", "caps": ["email", "calendar_write", "powerpoint"],
     "prompt": "The CEO emailed about the summer offsite. Add it to my calendar, reply to confirm "
               "I'll be there, and create a one-slide offsite.pptx titled 'Summer Offsite' with the "
               "date, time, and location as bullet points.",
     "grade": grade_multi_offsite},
]
