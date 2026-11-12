import base64
import json
import unittest
from pathlib import Path

from agent.gmail import EmailSource, GmailInbox, parse_ics
from unit_tests.helpers import FakeAuth, LoggedTest, llm_text

ICS = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART;TZID=America/Chicago:20261114T090000\r\n"
       "DTEND;TZID=America/Chicago:20261114T120000\r\nSUMMARY:Habitat for Humanity Build\r\n"
       "LOCATION:1201 W Bradley Ave\\, Champaign\r\nDESCRIPTION:Wear closed-toe\r\n  shoes.\r\nEND:VEVENT\r\n"
       "BEGIN:VEVENT\r\nDTSTART:20261113T010000Z\r\nDTEND:20261113T020000Z\r\nSUMMARY:UTC event\r\nEND:VEVENT\r\n"
       "BEGIN:VEVENT\r\nDTSTART;VALUE=DATE:20261115\r\nSUMMARY:All day\r\nEND:VEVENT\r\n"
       "BEGIN:VEVENT\r\nSTATUS:CANCELLED\r\nDTSTART:20261116T100000\r\nSUMMARY:Cancelled\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")


def enc(text):
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


class TestParseIcs(unittest.TestCase):
    def test_events(self):
        events = parse_ics(ICS)
        self.assertEqual([e["title"] for e in events], ["Habitat for Humanity Build", "UTC event", "All day"])
        habitat, utc, allday = events
        self.assertEqual((habitat["date"], habitat["start"], habitat["end"]), ("2026-11-14", "09:00", "12:00"))
        self.assertEqual(habitat["location"], "1201 W Bradley Ave, Champaign")
        self.assertEqual(habitat["description"], "Wear closed-toe shoes.")  # folded line joined (the first space is dropped)
        self.assertEqual((utc["date"], utc["start"], utc["end"]), ("2026-11-12", "19:00", "20:00"))  # UTC -> CST
        self.assertEqual((allday["date"], allday["start"]), ("2026-11-15", None))

    def test_sample_file_in_folder(self):
        path = Path(__file__).resolve().parents[2] / "gmail-test-emails" / "habitat-build.ics"
        if not path.exists():
            self.skipTest("sample .ics not next to the repo")
        self.assertEqual(parse_ics(path.read_text())[0]["start"], "09:00")


class TestGmailInbox(LoggedTest):
    def test_fetch_and_read_parts(self):
        msg = {"id": "m1", "internalDate": "1794412800000",  # 2026-11-11
               "payload": {"headers": [{"name": "Subject", "value": "Invite"}, {"name": "From", "value": "a@b.c"}],
                           "parts": [{"mimeType": "multipart/alternative", "parts": [
                               {"mimeType": "text/plain", "body": {"data": enc("See you Saturday!")}},
                               {"mimeType": "text/html", "body": {"data": enc("<p>See you <b>Saturday</b>!</p>")}}]},
                               {"mimeType": "application/octet-stream", "filename": "build.ics",
                                "body": {"attachmentId": "att1"}}]}}
        auth = FakeAuth([("GET", "/messages/m1/attachments/att1", {"data": enc(ICS)}),
                         ("GET", "/messages/m1", msg),
                         ("GET", "/messages", {"messages": [{"id": "m1"}]})])
        [m] = GmailInbox(auth).fetch()
        self.assertEqual((m["id"], m["subject"], m["sender"]), ("m1", "Invite", "a@b.c"))
        self.assertEqual(m["text"], "See you Saturday!")  # plain text preferred over html
        self.assertEqual(len(m["ics"]), 1)
        self.assertIn("newer_than", auth.calls[0][2]["params"]["q"])

    def test_html_only(self):
        msg = {"id": "m2", "internalDate": "1794412800000",
               "payload": {"mimeType": "text/html", "headers": [], "body": {"data": enc("<p>Club <b>meeting</b></p>")}}}
        m = GmailInbox(FakeAuth([("GET", "/messages/m2", msg)])).read("m2")
        self.assertEqual(m["text"].split(), ["Club", "meeting"])


def message(mid, text, subject="", sent="2026-11-11", ics=()):
    return {"id": mid, "subject": subject, "sender": "x@y.z", "sent": sent, "text": text, "ics": list(ics)}


class FakeInbox:
    def __init__(self, messages):
        self.messages = messages

    def fetch(self):
        return self.messages


class TestValidate(unittest.TestCase):
    m = message("m", "Robotics club is meeting thurs at 7 in Siebel 1404. Review is Tuesday Nov 17 from 6-8pm.")

    def item(self, **kw):
        base = {"email": 1, "type": "activity", "title": "Robotics", "date": "2026-11-12", "start": "19:00",
                "end": None, "location": "Siebel 1404", "evidence": "meeting thurs at 7"}
        base.update(kw)
        return EmailSource.validate(base, self.m)

    def test_grounded_activity_kept(self):
        it = self.item()
        self.assertEqual((it["date"], it["start"], it["end"]), ("2026-11-12", "19:00", None))

    def test_ungrounded_activity_loses_time(self):
        it = self.item(evidence="Friday at noon")
        self.assertEqual((it["date"], it["start"]), (None, None))

    def test_dash_normalization(self):
        it = self.item(type="commitment", date="2026-11-17", start="18:00", end="20:00", evidence="Tuesday Nov 17 from 6–8pm")
        self.assertEqual(it["end"], "20:00")

    def test_commitment_needs_verified_time(self):
        self.assertIsNone(self.item(type="commitment", evidence="not in the email"))
        self.assertIsNone(self.item(type="commitment", start=None))

    def test_bad_values(self):
        self.assertIsNone(self.item(type="party"))
        self.assertIsNone(self.item(title=""))
        self.assertIsNone(self.item(date="2026-11-04"))  # already past when sent
        self.assertEqual(self.item(start="7pm")["start"], None)
        self.assertEqual(self.item(end="18:00")["end"], None)  # end before start
        self.assertEqual(self.item(date="2026-13-40")["date"], None)


class TestEmailSource(LoggedTest):
    def test_scan_split_window_and_cache(self):
        msgs = [message("a", "Robotics club is meeting thurs at 7 in Siebel 1404"),
                message("b", "Your interview is confirmed for Friday, November 13 at 3:00 PM (30 minutes)."),
                message("c", "Next month: gala on Dec 5 at 7pm"),
                message("d", "invite attached", ics=[ICS])]
        items = [{"email": 1, "type": "activity", "title": "Robotics", "date": "2026-11-12", "start": "19:00",
                  "end": None, "evidence": "meeting thurs at 7"},
                 {"email": 2, "type": "commitment", "title": "Interview", "date": "2026-11-13", "start": "15:00",
                  "end": "15:30", "evidence": "Friday, November 13 at 3:00 PM"},
                 {"email": 3, "type": "activity", "title": "Gala", "date": "2026-12-05", "start": "19:00",
                  "end": None, "evidence": "Dec 5 at 7pm"},
                 {"email": 9, "type": "activity", "title": "bad index"}]
        src = EmailSource(FakeInbox(msgs))
        prompts = []
        src.call = lambda messages, **kw: prompts.append(messages[0]["content"]) or llm_text(f"<items>{json.dumps(items)}</items>")
        out = src.scan("2026-11-11", "2026-11-17")
        self.assertEqual([a["title"] for a in out["activities"]], ["Robotics", "Habitat for Humanity Build", "UTC event", "All day"])
        self.assertEqual([c["title"] for c in out["commitments"]], ["Interview"])
        self.assertFalse(out["commitments"][0]["agent_added"])
        robo = out["activities"][0]
        self.assertEqual((robo["source"], robo["id"], robo["kind"]), ("email", "gmail:a:0", "event"))
        self.assertIn("#all/a", robo["url"])
        self.assertEqual(len(prompts), 1)
        self.assertNotIn("invite attached", prompts[0])  # .ics emails are parsed by code, not sent to the model
        src.scan("2026-11-11", "2026-11-17")
        self.assertEqual(len(prompts), 1)  # cached: no second model call

    def test_unparseable_reply(self):
        src = EmailSource(FakeInbox([message("a", "hi")]))
        src.call = lambda messages, **kw: llm_text("sorry")
        self.assertEqual(src.scan("2026-11-11", "2026-11-17"), {"activities": [], "commitments": []})


if __name__ == "__main__":
    unittest.main()
