# gmail as a source of activities (to recommend) and commitments (busy time), read only
# the LLM pulls events out of emails, then code double checks them; .ics invites are parsed by code

import base64
import json
import re
from datetime import date, datetime, timedelta

from bs4 import BeautifulSoup

from agent.google_auth import GoogleAuth
from utils import Logged, Utils

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
TZ = "America/Chicago"
# Gmail search: recent inbox messages that look like events, commitments or invites
DEFAULT_QUERY = ("in:inbox newer_than:21d (meeting OR meet OR meets OR event OR events OR workshop OR session "
                 "OR rsvp OR register OR registration OR invite OR invitation OR review OR interview OR volunteer "
                 "OR volunteers OR club OR social OR info OR filename:ics)")
MAX_TEXT = 4000  # characters of each email given to the model
BATCH = 8  # emails per extraction call
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


# gmail's base64url -> text
def b64(data):
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


# lowercase + clean up dashes/spaces so the evidence check isn't too picky
def norm(text):
    text = text.lower().replace("–", "-").replace("—", "-").replace("’", "'").replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------- calendar invites (.ics)

# .ics time -> (date, HH:MM) in Chicago time
def to_local(value, tzid):
    m = re.match(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(Z?)$", value)
    if not m:
        return None
    dt = datetime(*map(int, m.groups()[:6]))
    if m.group(7) or (tzid and tzid != TZ):
        try:
            from zoneinfo import ZoneInfo  # needs the tzdata package on Windows (requirements.txt)
            src = ZoneInfo("UTC") if m.group(7) else ZoneInfo(tzid)
            dt = dt.replace(tzinfo=src).astimezone(ZoneInfo(TZ))
        except Exception:
            return None
    return dt.date().isoformat(), dt.strftime("%H:%M")


# events from an .ics file (all-day events get no times)
def parse_ics(text):
    text = re.sub(r"\r?\n[ \t]", "", text)  # unfold continuation lines
    unescape = lambda v: v.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")
    events = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S):
        fields = {}
        for line in block.splitlines():
            if ":" not in line:
                continue
            name, value = line.split(":", 1)
            key, *params = name.split(";")
            fields[key.upper()] = (value.strip(), dict(p.split("=", 1) for p in params if "=" in p))
        if fields.get("STATUS", ("",))[0].upper() == "CANCELLED" or "DTSTART" not in fields:
            continue
        value, params = fields["DTSTART"]
        ev = {"title": unescape(fields.get("SUMMARY", ("(no title)",))[0]),
              "location": unescape(fields.get("LOCATION", ("",))[0]) or None,
              "description": unescape(fields.get("DESCRIPTION", ("",))[0]) or None,
              "date": None, "start": None, "end": None}
        if re.match(r"^\d{8}$", value):  # all-day
            ev["date"] = f"{value[:4]}-{value[4:6]}-{value[6:]}"
        else:
            start = to_local(value, params.get("TZID"))
            if not start:
                continue
            ev["date"], ev["start"] = start
            if "DTEND" in fields:
                end = to_local(fields["DTEND"][0], fields["DTEND"][1].get("TZID"))
                if end and end[0] == ev["date"]:
                    ev["end"] = end[1]
        events.append(ev)
    return events


# ---------------------------------------------------------------- Gmail

class GmailInbox(Logged):
    def __init__(self, auth=None, query=DEFAULT_QUERY, max_messages=25):
        self.auth = auth or GoogleAuth()
        self.query = query
        self.max = max_messages

    # matching emails, newest first
    def fetch(self):
        listing = self.auth.request("GET", f"{GMAIL}/messages", params={"q": self.query, "maxResults": self.max})
        messages = [self.read(m["id"]) for m in (listing or {}).get("messages", [])]
        self.log("gmail_fetch", query=self.query, n_messages=len(messages))
        return messages

    # one email -> subject, text and any .ics attachments
    def read(self, msg_id):
        msg = self.auth.request("GET", f"{GMAIL}/messages/{msg_id}", params={"format": "full"})
        headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
        plain, html, ics = [], [], []
        stack = [msg["payload"]]
        while stack:
            part = stack.pop(0)
            stack += part.get("parts", [])
            mime, body = part.get("mimeType", ""), part.get("body", {})
            filename = (part.get("filename") or "").lower()
            if mime == "text/calendar" or filename.endswith(".ics"):
                data = body.get("data")
                if not data and body.get("attachmentId"):
                    data = self.auth.request("GET", f"{GMAIL}/messages/{msg_id}/attachments/{body['attachmentId']}")["data"]
                if data:
                    ics.append(b64(data))
            elif mime == "text/plain" and body.get("data") and not filename:
                plain.append(b64(body["data"]))
            elif mime == "text/html" and body.get("data") and not filename:
                html.append(BeautifulSoup(b64(body["data"]), "html.parser").get_text(" "))
        sent = datetime.fromtimestamp(int(msg["internalDate"]) / 1000).date().isoformat()
        return {"id": msg_id, "subject": headers.get("subject", ""), "sender": headers.get("from", ""),
                "sent": sent, "text": "\n".join(plain or html)[:MAX_TEXT], "ics": ics}


# turns emails into activities and commitments (off in eval, the profiles have no inbox)
class EmailSource(Utils):
    # inbox just needs fetch(), so tests can pass a fake
    def __init__(self, inbox=None, api_key=None):
        super().__init__(api_key)
        self.inbox = inbox if inbox is not None else GmailInbox()
        self.cache = {}  # message id -> extracted items, so each email only goes to the model once

    # activities + commitments dated start..end (undated activities are kept but can't be scheduled)
    def scan(self, start, end, usage=None):
        messages = self.inbox.fetch()
        new = [m for m in messages if m["id"] not in self.cache]
        for m in new:
            if m["ics"]:  # invites are parsed by code; the body usually just repeats them
                self.cache[m["id"]] = [dict(ev, type="activity", evidence="calendar invite")
                                       for text in m["ics"] for ev in parse_ics(text)]
        llm = [m for m in new if not m["ics"]]
        for i in range(0, len(llm), BATCH):
            self.extract(llm[i:i + BATCH], usage)
        activities, commitments = [], []
        for m in messages:
            for n, it in enumerate(self.cache.get(m["id"], [])):
                in_week = it["date"] is not None and start <= it["date"] <= end
                item_id = f"gmail:{m['id']}:{n}"
                if it["type"] == "activity" and (in_week or it["date"] is None):
                    activities.append({"kind": "event", "id": item_id, "title": it["title"], "date": it["date"],
                                       "start": it["start"], "end": it["end"], "location": it.get("location"),
                                       "description": it.get("description") or f"From an email: {m['subject']}",
                                       "categories": "email", "url": f"https://mail.google.com/mail/u/0/#all/{m['id']}",
                                       "source": "email"})
                elif it["type"] == "commitment" and in_week:
                    commitments.append({"id": item_id, "title": it["title"], "date": it["date"], "start": it["start"],
                                        "end": it["end"], "location": it.get("location"), "source": "email",
                                        "agent_added": False})
        self.log("email_scan", n_messages=len(messages), n_new=len(new), activities=[a["title"] for a in activities],
                 commitments=[c["title"] for c in commitments])
        return {"activities": activities, "commitments": commitments}

    # one LLM call per batch of emails, results cached by message id
    def extract(self, messages, usage=None):
        listing = "\n\n".join(f"<email n=\"{i + 1}\" sent=\"{m['sent']}\">\nSubject: {m['subject']}\nFrom: {m['sender']}\n"
                              f"{m['text']}\n</email>" for i, m in enumerate(messages))
        prompt = f"""You read a UIUC student's emails and list the events in them, for an extracurricular planner.

Two types:
- "activity": something the student could choose to attend: club meetings, workshops, socials, talks,
  volunteering, performances, info sessions (announcements and invitations).
- "commitment": an obligation for this student specifically: a required review or exam, an interview,
  an appointment, a work shift, a meeting they must attend.
Ignore anything that is not an event (deliveries, receipts, deadlines with no event, tips, newsletters
without events) and events that already happened before the email's sent date.

For each event give:
- email: the email's n
- type: "activity" or "commitment"
- title: short name
- date: YYYY-MM-DD, or null if the email gives no usable date. Resolve relative dates ("thurs",
  "tomorrow", "this Saturday") from the email's sent date. Never guess.
- start, end: 24-hour HH:MM, or null if not stated. Never guess. If only a length is given, compute the end.
- location: as written, or null
- description: one short sentence
- evidence: the exact words from the email that give the date and time, copied character for character
  (null if there is no date/time)

<emails>
{listing}
</emails>

Reply with a JSON list inside <items></items> tags (an empty list if there are no events)."""
        text = self.text_of(self.call([{"role": "user", "content": prompt}], max_tokens=4000, usage=usage))
        match = re.search(r"<items>\s*(\[.*\])\s*</items>", text, re.S)
        try:
            raw = json.loads(match.group(1))
        except (AttributeError, json.JSONDecodeError):
            raw = []
        for m in messages:
            self.cache[m["id"]] = []
        dropped = 0
        for it in raw if isinstance(raw, list) else []:
            try:
                m = messages[int(it["email"]) - 1]
            except (KeyError, TypeError, ValueError, IndexError):
                dropped += 1
                continue
            item = self.validate(it, m)
            if item:
                self.cache[m["id"]].append(item)
            else:
                dropped += 1
        self.log("email_extract", n_messages=len(messages), n_items=len(raw) if isinstance(raw, list) else 0, dropped=dropped)

    # code checks on what the LLM pulled out: real dates/times, evidence actually in the email
    @staticmethod
    def validate(it, message):
        if not isinstance(it, dict) or it.get("type") not in ("activity", "commitment") or not it.get("title"):
            return None
        item = {"type": it["type"], "title": str(it["title"])[:200], "location": it.get("location"),
                "description": it.get("description"), "date": None, "start": None, "end": None}
        try:
            day = date.fromisoformat(str(it.get("date"))) if it.get("date") else None
        except ValueError:
            day = None
        start = it.get("start") if isinstance(it.get("start"), str) and HHMM.match(it["start"]) else None
        end = it.get("end") if isinstance(it.get("end"), str) and HHMM.match(it["end"]) else None
        if start and end and end <= start:
            end = None
        evidence = it.get("evidence")
        grounded = isinstance(evidence, str) and len(evidence.strip()) >= 3 and \
            norm(evidence) in norm(message["subject"] + " " + message["text"])
        if day and grounded:
            if day < date.fromisoformat(message["sent"]) - timedelta(days=1):
                return None  # already happened when the email was sent
            item.update(date=day.isoformat(), start=start, end=end if start else None)
        if item["type"] == "commitment" and not (item["date"] and item["start"]):
            return None  # can't block time without a verified date and time
        return item
