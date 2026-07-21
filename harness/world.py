"""Simulated office workspace: email, calendar, chat messages, reminders.

State is seeded from fixtures so every benchmark episode starts identically.
All mutations are logged to `actions` and snapshotted to state.json for grading.
The simulated clock is fixed so date reasoning is deterministic and gradeable.
"""
import datetime
import json
import os
import re

SIM_TODAY = datetime.date(2026, 7, 20)  # a Monday
SIM_TODAY_HUMAN = "Monday, July 20, 2026"


class ToolError(Exception):
    """Raised by world/tool operations with a message meant for the model."""


EMAILS = [
    {"id": "e1", "from": "newsletter@technews.com", "date": "2026-07-13 08:00",
     "subject": "Your weekly tech digest",
     "body": "Top stories this week: chip shortages ease, new frameworks released, and more. Click to read."},
    {"id": "e2", "from": "dana@corp.com", "date": "2026-07-16 14:12",
     "subject": "Q3 sales numbers - final",
     "body": "Hi! Final Q3 numbers are in: West region $1,240,000; East region $845,000; Online $610,000. "
             "Total $2,695,000, up 12% QoQ. Can you turn these into slides before Friday? Thanks, Dana"},
    {"id": "e3", "from": "receipts@cloudhost.com", "date": "2026-07-12 03:22",
     "subject": "Receipt: CloudHost subscription",
     "body": "Invoice #8841. Date: 2026-07-12. Vendor: CloudHost. Amount: $230.00. Thank you for your business."},
    {"id": "e4", "from": "noreply@officemax.com", "date": "2026-07-14 16:45",
     "subject": "Your OfficeMax receipt",
     "body": "Purchase date: 2026-07-14. Vendor: OfficeMax. Items: paper, toner. Total: $87.50."},
    {"id": "e5", "from": "receipts@delta.com", "date": "2026-07-15 09:10",
     "subject": "E-ticket receipt - confirmation KX93L",
     "body": "Travel date: 2026-07-15. Vendor: Delta. Ticket total: $412.30. Passenger: you."},
    {"id": "e6", "from": "jordan@corp.com", "date": "2026-07-15 11:30",
     "subject": "Northwind project kickoff details",
     "body": "The Northwind project kickoff will be Thursday July 23 at 13:30 in room 4B. Agenda to follow. Jordan"},
    {"id": "e7", "from": "mia@corp.com", "date": "2026-07-17 10:05",
     "subject": "Northwind kickoff - can you make it?",
     "body": "Hi! Are you able to attend the Northwind kickoff on Thursday? Need a headcount by Monday. Mia"},
    {"id": "e8", "from": "ceo@corp.com", "date": "2026-07-17 09:00",
     "subject": "Summer offsite - save the date",
     "body": "Team, our summer offsite is Friday July 24 from 9:00 to 16:00 at the Lakeside Pavilion. "
             "Please add it to your calendar and reply to confirm you can join. - Alex, CEO"},
    {"id": "e9", "from": "hr@corp.com", "date": "2026-07-13 12:00",
     "subject": "Benefits enrollment closes July 31",
     "body": "Reminder: annual benefits enrollment closes July 31. Log in to the portal to make elections."},
    {"id": "e10", "from": "promo@flashdeals.example", "date": "2026-07-18 05:00",
     "subject": "48-HOUR SALE - everything must go!!!",
     "body": "Huge discounts on everything. Unsubscribe at the link below."},
]

CALENDAR = [
    {"id": "c1", "title": "Team standup", "date": "2026-07-20", "start": "09:30", "end": "09:45",
     "location": "", "attendees": []},
    {"id": "c2", "title": "Design review", "date": "2026-07-22", "start": "10:00", "end": "11:00",
     "location": "room 2A", "attendees": []},
    {"id": "c3", "title": "1:1 with Sam", "date": "2026-07-22", "start": "14:00", "end": "14:30",
     "location": "", "attendees": ["sam@corp.com"]},
    {"id": "c4", "title": "Marketing sync", "date": "2026-07-22", "start": "15:00", "end": "16:00",
     "location": "room 3C", "attendees": []},
    {"id": "c5", "title": "Board prep", "date": "2026-07-23", "start": "09:00", "end": "11:00",
     "location": "", "attendees": []},
    {"id": "c6", "title": "Lunch with vendor", "date": "2026-07-23", "start": "12:00", "end": "13:00",
     "location": "Bistro 22", "attendees": []},
    {"id": "c7", "title": "Customer call", "date": "2026-07-23", "start": "15:00", "end": "16:00",
     "location": "", "attendees": []},
]

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")


def _check_date(value, field="date"):
    if not isinstance(value, str) or not DATE_RE.match(value.strip()):
        raise ToolError(f"{field} must be in YYYY-MM-DD format, got {value!r}")
    return value.strip()


def _check_time(value, field="time"):
    if not isinstance(value, str) or not TIME_RE.match(value.strip()):
        raise ToolError(f"{field} must be in 24-hour HH:MM format (e.g. 14:00), got {value!r}")
    h, m = value.strip().split(":")
    return f"{int(h):02d}:{m}"


class World:
    def __init__(self, workdir, persistent=False):
        """persistent=True (agent folders): state survives across runs via
        state.json. persistent=False (benchmark): fresh fixtures every episode."""
        self.workdir = workdir
        self.persistent = persistent
        self.files_dir = os.path.join(workdir, "files")
        os.makedirs(self.files_dir, exist_ok=True)
        state = None
        state_path = os.path.join(workdir, "state.json")
        if persistent and os.path.exists(state_path):
            with open(state_path, encoding="utf-8") as f:
                state = json.load(f)
        if state:
            self.emails = state.get("emails", [dict(e) for e in EMAILS])
            self.events = state.get("events", [dict(e) for e in CALENDAR])
            self.sent_emails = state.get("sent_emails", [])
            self.messages = state.get("messages", [])
            self.reminders = state.get("reminders", [])
        else:
            self.emails = [dict(e) for e in EMAILS]
            self.events = [dict(e) for e in CALENDAR]
            self.sent_emails = []
            self.messages = []
            self.reminders = []
        self.actions = []  # log of every mutating/inspecting tool call (per episode)

    # ---- email ----
    def list_emails(self):
        rows = sorted(self.emails, key=lambda e: e["date"], reverse=True)
        return [{"id": e["id"], "from": e["from"], "date": e["date"], "subject": e["subject"]} for e in rows]

    def read_email(self, email_id):
        if not isinstance(email_id, str):
            raise ToolError(f"id must be a string like 'e3', got {email_id!r}")
        for e in self.emails:
            if e["id"] == email_id.strip():
                return dict(e)
        raise ToolError(f"no email with id {email_id!r}; use list_emails to see valid ids")

    def send_email(self, to, subject, body):
        if not to or not isinstance(to, str):
            raise ToolError("'to' must be a recipient email address string")
        rec = {"to": to.strip(), "subject": str(subject or ""), "body": str(body or "")}
        self.sent_emails.append(rec)
        return rec

    # ---- calendar ----
    def list_events(self, date=None):
        events = self.events
        if date:
            date = _check_date(date)
            events = [e for e in events if e["date"] == date]
        return sorted(events, key=lambda e: (e["date"], e["start"]))

    def add_event(self, title, date, start_time, end_time, attendees=None, location=None):
        if not title or not isinstance(title, str):
            raise ToolError("'title' must be a non-empty string")
        date = _check_date(date)
        start = _check_time(start_time, "start_time")
        end = _check_time(end_time, "end_time")
        if end <= start:
            raise ToolError(f"end_time ({end}) must be after start_time ({start})")
        if attendees is None:
            attendees = []
        if isinstance(attendees, str):
            attendees = [a.strip() for a in attendees.split(",") if a.strip()]
        if not isinstance(attendees, list):
            raise ToolError("'attendees' must be a list of email addresses")
        ev = {"id": f"c{len(self.events) + 1}", "title": title.strip(), "date": date,
              "start": start, "end": end, "location": str(location or ""),
              "attendees": [str(a) for a in attendees]}
        self.events.append(ev)
        return ev

    # ---- messages & reminders ----
    def send_message(self, to, text):
        if not to or not isinstance(to, str):
            raise ToolError("'to' must be a contact name or address string")
        if not text or not isinstance(text, str):
            raise ToolError("'text' must be a non-empty string")
        rec = {"to": to.strip(), "text": text}
        self.messages.append(rec)
        return rec

    def set_reminder(self, text, date, time):
        if not text or not isinstance(text, str):
            raise ToolError("'text' must be a non-empty string")
        date = _check_date(date)
        time = _check_time(time)
        rec = {"text": text, "date": date, "time": time}
        self.reminders.append(rec)
        return rec

    # ---- bookkeeping ----
    def log(self, tool, args, ok, result_preview):
        self.actions.append({"tool": tool, "args": args, "ok": ok, "result": str(result_preview)[:300]})

    def snapshot(self):
        state = {"emails": self.emails, "sent_emails": self.sent_emails,
                 "events": self.events, "messages": self.messages,
                 "reminders": self.reminders, "actions": self.actions}
        with open(os.path.join(self.workdir, "state.json"), "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
