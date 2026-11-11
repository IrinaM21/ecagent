# calendar tools: SimulatedCalendar for eval, GoogleCalendar for real use
# both have get_events / add_event / remove_event, so the agent works the same on either

from datetime import date, timedelta
from urllib.parse import quote

from agent.google_auth import GoogleAPIError, GoogleAuth
from utils import Logged

TZ = "America/Chicago"
DEFAULT_MINUTES = 60  # length to assume when there's no end time (same as scheduler.py)
API = "https://www.googleapis.com/calendar/v3"


class CalendarError(RuntimeError):
    pass


# end time, or start + 60 min if the activity doesn't list one
def end_of(activity):
    if activity.get("end"):
        return activity["end"]
    m = int(activity["start"][:2]) * 60 + int(activity["start"][3:]) + DEFAULT_MINUTES
    return "23:59" if m >= 24 * 60 else f"{m // 60:02d}:{m % 60:02d}"


# fake calendar for eval: can start with events and fail on purpose
class SimulatedCalendar(Logged):
    # fail_on={"add_event": [1]} makes the first add fail (api_key is ignored, kept for old calls)
    def __init__(self, api_key=None, events=(), fail_on=None):
        self.events = []
        for e in events:
            self.events.append(dict(e, id=e.get("id") or f"sim-{len(self.events) + 1}",
                                    agent_added=e.get("agent_added", True)))
        self.fail_on = {op: set(calls) for op, calls in (fail_on or {}).items()}
        self.calls = {}
        self.next_id = len(self.events) + 1

    # raises on the call numbers listed in fail_on
    def maybe_fail(self, op):
        n = self.calls[op] = self.calls.get(op, 0) + 1
        if n in self.fail_on.get(op, ()):
            self.log(op, ok=False, call=n, error="simulated failure")
            raise CalendarError(f"simulated {op} failure (call {n})")

    def get_events(self, start=None, end=None):
        self.maybe_fail("get_events")
        return [dict(e) for e in self.events
                if (start is None or e["date"] >= start) and (end is None or e["date"] <= end)]

    # add an approved activity, returns the stored event
    def add_event(self, activity):
        self.maybe_fail("add_event")
        if not activity.get("date") or not activity.get("start"):
            raise CalendarError(f"{activity.get('title')}: no date/start time, can't be added")
        event = dict(activity, id=f"sim-{self.next_id}", source_id=activity.get("id"), agent_added=True)
        self.next_id += 1
        self.events.append(event)
        self.log("add_event", ok=True, event_id=event["id"], title=event["title"], date=event["date"], start=event["start"])
        return dict(event)

    # False if the event wasn't there
    def remove_event(self, event_id):
        self.maybe_fail("remove_event")
        before = len(self.events)
        self.events = [e for e in self.events if e["id"] != event_id]
        removed = len(self.events) < before
        self.log("remove_event", ok=removed, event_id=event_id)
        return removed


# real Google Calendar: reads your calendars as busy time, adds to a separate ECAgent calendar
class GoogleCalendar(Logged):
    # write_calendar="primary" adds to the main calendar instead
    def __init__(self, auth=None, read_calendars=("primary",), write_calendar="ECAgent", tz=TZ):
        self.auth = auth or GoogleAuth()
        self.read_ids = list(read_calendars)
        self.write_name = write_calendar
        self.tz = tz
        self.write_cal = None

    # Google errors -> CalendarError
    def call(self, method, path, **kwargs):
        try:
            return self.auth.request(method, API + path, **kwargs)
        except GoogleAPIError as e:
            raise CalendarError(str(e)) from e

    # id of the ECAgent calendar (made the first time if it doesn't exist)
    def write_id(self):
        if self.write_cal is None:
            if self.write_name == "primary":
                self.write_cal = "primary"
                return self.write_cal
            token = None
            while True:
                page = self.call("GET", "/users/me/calendarList", params={"pageToken": token} if token else {})
                for c in page.get("items", []):
                    if c.get("summary") == self.write_name and c.get("accessRole") in ("owner", "writer"):
                        self.write_cal = c["id"]
                token = page.get("nextPageToken")
                if self.write_cal or not token:
                    break
            if self.write_cal is None:
                made = self.call("POST", "/calendars", json={"summary": self.write_name, "timeZone": self.tz})
                self.write_cal = made["id"]
                self.log("create_calendar", calendar=self.write_name)
        return self.write_cal

    # Google's event format -> ours (times come back in Chicago time)
    def to_event(self, item, cal):
        s, e = item["start"], item["end"]
        if "dateTime" in s:
            day, start = s["dateTime"][:10], s["dateTime"][11:16]
            end = e["dateTime"][11:16] if e["dateTime"][:10] == day else "23:59"  # runs past midnight
        else:
            day, start, end = s["date"], None, None  # all-day: no time to block
        private = item.get("extendedProperties", {}).get("private", {})
        return {"id": item["id"], "calendar_id": cal, "title": item.get("summary", "(no title)"),
                "date": day, "start": start, "end": end, "location": item.get("location"),
                "source_id": private.get("source_id"), "source": "google",
                "agent_added": cal == self.write_cal or private.get("ecagent") == "1"}

    # your events for the week, minus cancelled, "free" and declined ones
    def get_events(self, start=None, end=None):
        first = date.fromisoformat(start) if start else date.today()
        last = date.fromisoformat(end) if end else first + timedelta(days=6)
        # pad a day on each side, then keep only this week's local dates (no timezone library needed)
        params = {"singleEvents": "true", "orderBy": "startTime", "timeZone": self.tz, "maxResults": 250,
                  "timeMin": f"{first - timedelta(days=1)}T00:00:00Z", "timeMax": f"{last + timedelta(days=2)}T00:00:00Z"}
        out = []
        for cal in dict.fromkeys(self.read_ids + [self.write_id()]):  # dedupe, keep order
            token = None
            while True:
                page = self.call("GET", f"/calendars/{quote(cal, safe='')}/events",
                                 params=dict(params, pageToken=token) if token else params)
                for item in page.get("items", []):
                    declined = any(a.get("self") and a.get("responseStatus") == "declined" for a in item.get("attendees", []))
                    if item.get("status") == "cancelled" or item.get("transparency") == "transparent" or declined:
                        continue
                    ev = self.to_event(item, cal)
                    if first.isoformat() <= ev["date"] <= last.isoformat():
                        out.append(ev)
                token = page.get("nextPageToken")
                if not token:
                    break
        return sorted(out, key=lambda e: (e["date"], e["start"] or ""))

    # add an approved activity to the ECAgent calendar
    def add_event(self, activity):
        if not activity.get("date") or not activity.get("start"):
            raise CalendarError(f"{activity.get('title')}: no date/start time, can't be added")
        details = [activity.get("description") or "", activity.get("url") or "", "Added by ECAgent"]
        body = {"summary": activity["title"], "location": activity.get("location") or "",
                "description": "\n\n".join(d for d in details if d),
                "start": {"dateTime": f"{activity['date']}T{activity['start']}:00", "timeZone": self.tz},
                "end": {"dateTime": f"{activity['date']}T{end_of(activity)}:00", "timeZone": self.tz},
                "extendedProperties": {"private": {"ecagent": "1", "source_id": str(activity.get("id"))}}}
        cal = self.write_id()
        item = self.call("POST", f"/calendars/{quote(cal, safe='')}/events", json=body)
        event = self.to_event(item, cal)
        self.log("add_event", ok=True, event_id=event["id"], title=event["title"], date=event["date"], start=event["start"])
        return event

    # only removes from the ECAgent calendar; False if it was already gone
    def remove_event(self, event_id):
        cal = self.write_id()
        try:
            self.auth.request("DELETE", f"{API}/calendars/{quote(cal, safe='')}/events/{quote(event_id, safe='')}")
        except GoogleAPIError as e:
            if e.status in (404, 410):
                self.log("remove_event", ok=False, event_id=event_id, error="not found")
                return False
            raise CalendarError(str(e)) from e
        self.log("remove_event", ok=True, event_id=event_id)
        return True
