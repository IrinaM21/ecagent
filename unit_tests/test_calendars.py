import unittest

from agent.calendars import CalendarError, GoogleCalendar, SimulatedCalendar, end_of
from agent.google_auth import GoogleAPIError
from unit_tests.helpers import FakeAuth, LoggedTest

ACT = {"kind": "event", "id": 42, "title": "Photo Walk", "date": "2026-11-14", "start": "14:00", "end": "16:00",
       "location": "Alma Mater", "description": "Bring a camera", "url": "https://example.com", "source": "db"}


class TestEndOf(unittest.TestCase):
    def test_default_length_and_cap(self):
        self.assertEqual(end_of({"start": "19:00"}), "20:00")
        self.assertEqual(end_of({"start": "23:30"}), "23:59")
        self.assertEqual(end_of({"start": "19:00", "end": "19:45"}), "19:45")


class TestSimulatedCalendar(LoggedTest):
    def test_add_get_remove(self):
        cal = SimulatedCalendar(events=[{"title": "Old plan", "date": "2026-11-05", "start": "18:30", "end": "19:30"}])
        ev = cal.add_event(ACT)
        self.assertEqual(ev["source_id"], 42)
        self.assertTrue(ev["agent_added"])
        self.assertEqual([e["title"] for e in cal.get_events("2026-11-14", "2026-11-14")], ["Photo Walk"])
        self.assertEqual(len(cal.get_events()), 2)
        self.assertTrue(cal.remove_event(ev["id"]))
        self.assertFalse(cal.remove_event(ev["id"]))
        self.assertEqual([e["step"] for e in self.log_entries()], ["add_event", "remove_event", "remove_event"])

    def test_old_positional_api_key_still_works(self):
        self.assertEqual(SimulatedCalendar("some-key").get_events(), [])

    def test_fail_on_call_number(self):
        cal = SimulatedCalendar(fail_on={"add_event": [1]})
        with self.assertRaises(CalendarError):
            cal.add_event(ACT)
        cal.add_event(ACT)  # second call works
        self.assertEqual(len(cal.get_events()), 1)
        self.assertEqual([e["ok"] for e in self.log_entries("add_event")], [False, True])

    def test_untimed_activity_rejected(self):
        with self.assertRaises(CalendarError):
            SimulatedCalendar().add_event(dict(ACT, start=None))


def google(routes, **kw):
    cal = GoogleCalendar(auth=FakeAuth(routes), **kw)
    return cal, cal.auth


LIST = ("GET", "/users/me/calendarList", {"items": [{"id": "ecagent@group", "summary": "ECAgent", "accessRole": "owner"}]})


class TestGoogleCalendar(LoggedTest):
    def test_finds_existing_write_calendar(self):
        cal, _ = google([LIST])
        self.assertEqual(cal.write_id(), "ecagent@group")

    def test_creates_write_calendar_when_missing(self):
        cal, auth = google([("GET", "/users/me/calendarList", {"items": []}),
                            ("POST", "/calendars", {"id": "new@group"})])
        self.assertEqual(cal.write_id(), "new@group")
        self.assertEqual(auth.calls[-1][2]["json"]["summary"], "ECAgent")

    def test_get_events_parsing_and_filters(self):
        primary = {"items": [
            {"id": "c1", "summary": "CS 225", "start": {"dateTime": "2026-11-12T10:00:00-06:00"},
             "end": {"dateTime": "2026-11-12T10:50:00-06:00"}},
            {"id": "c2", "summary": "Holiday", "start": {"date": "2026-11-13"}, "end": {"date": "2026-11-14"}},
            {"id": "c3", "summary": "Cancelled", "status": "cancelled", "start": {"dateTime": "2026-11-12T12:00:00-06:00"},
             "end": {"dateTime": "2026-11-12T13:00:00-06:00"}},
            {"id": "c4", "summary": "Free time", "transparency": "transparent",
             "start": {"dateTime": "2026-11-12T12:00:00-06:00"}, "end": {"dateTime": "2026-11-12T13:00:00-06:00"}},
            {"id": "c5", "summary": "Declined", "attendees": [{"self": True, "responseStatus": "declined"}],
             "start": {"dateTime": "2026-11-12T14:00:00-06:00"}, "end": {"dateTime": "2026-11-12T15:00:00-06:00"}},
            {"id": "c6", "summary": "Late shift", "start": {"dateTime": "2026-11-12T22:00:00-06:00"},
             "end": {"dateTime": "2026-11-13T02:00:00-06:00"}},
            {"id": "c7", "summary": "Outside window", "start": {"dateTime": "2026-11-30T10:00:00-06:00"},
             "end": {"dateTime": "2026-11-30T11:00:00-06:00"}}]}
        mine = {"items": [{"id": "a1", "summary": "Photo Walk", "start": {"dateTime": "2026-11-14T14:00:00-06:00"},
                           "end": {"dateTime": "2026-11-14T16:00:00-06:00"}}]}
        cal, auth = google([LIST, ("GET", "/calendars/primary/events", primary),
                            ("GET", "/calendars/ecagent%40group/events", mine)])
        events = cal.get_events("2026-11-11", "2026-11-17")
        self.assertEqual([e["id"] for e in events], ["c1", "c6", "c2", "a1"])
        by_id = {e["id"]: e for e in events}
        self.assertEqual((by_id["c1"]["start"], by_id["c1"]["end"], by_id["c1"]["agent_added"]), ("10:00", "10:50", False))
        self.assertEqual(by_id["c6"]["end"], "23:59")
        self.assertIsNone(by_id["c2"]["start"])
        self.assertTrue(by_id["a1"]["agent_added"])
        params = [c[2]["params"] for c in auth.calls if "/events" in c[1]][0]
        self.assertEqual((params["singleEvents"], params["timeZone"]), ("true", "America/Chicago"))

    def test_pagination(self):
        pages = iter([{"items": [], "nextPageToken": "p2"},
                      {"items": [{"id": "x", "summary": "X", "start": {"dateTime": "2026-11-12T10:00:00-06:00"},
                                  "end": {"dateTime": "2026-11-12T11:00:00-06:00"}}]}])
        cal, _ = google([LIST, ("GET", "/calendars/primary/events", lambda kw: next(pages))],
                        write_calendar="primary")
        self.assertEqual([e["id"] for e in cal.get_events("2026-11-11", "2026-11-17")], ["x"])

    def test_add_event_body(self):
        def created(kw):
            body = kw["json"]
            return {"id": "new1", "summary": body["summary"], "start": {"dateTime": body["start"]["dateTime"] + "-06:00"},
                    "end": {"dateTime": body["end"]["dateTime"] + "-06:00"}, "extendedProperties": body["extendedProperties"]}
        cal, auth = google([LIST, ("POST", "/calendars/ecagent%40group/events", created)])
        ev = cal.add_event(dict(ACT, end=None))
        body = auth.calls[-1][2]["json"]
        self.assertEqual(body["start"], {"dateTime": "2026-11-14T14:00:00", "timeZone": "America/Chicago"})
        self.assertEqual(body["end"]["dateTime"], "2026-11-14T15:00:00")  # default 60 minutes
        self.assertEqual(body["extendedProperties"]["private"], {"ecagent": "1", "source_id": "42"})
        self.assertIn("Added by ECAgent", body["description"])
        self.assertEqual((ev["id"], ev["start"], ev["agent_added"], ev["source_id"]), ("new1", "14:00", True, "42"))

    def test_api_errors_become_calendar_errors(self):
        cal, _ = google([LIST, ("POST", "/events", GoogleAPIError(403, "forbidden"))])
        with self.assertRaises(CalendarError):
            cal.add_event(ACT)

    def test_remove_missing_event(self):
        cal, _ = google([LIST, ("DELETE", "/events/gone", GoogleAPIError(404, "not found")),
                         ("DELETE", "/events/ok", None)])
        self.assertFalse(cal.remove_event("gone"))
        self.assertTrue(cal.remove_event("ok"))


if __name__ == "__main__":
    unittest.main()
