# two full turns with a fake LLM: request -> plan from an email activity -> "yes" -> event on the calendar

import unittest

from agent.agent import ECAgent
from agent.calendars import SimulatedCalendar
from unit_tests.helpers import LoggedTest, llm_text

INTAKE = {"student": {"name": "Test"}, "interests": ["photography"], "goals": [], "dislikes": [],
          "hard_constraints": [{"id": "H1", "type": "earliest_start", "time": "08:00"},
                               {"id": "H2", "type": "latest_end", "time": "22:00"}],
          "soft_preferences": [], "schedule": {"timezone": "America/Chicago", "recurring": [],
                                               "one_off": [{"id": "O1", "date": "2026-11-14", "start": "09:00", "end": "13:00"}]}}
WALK = {"kind": "event", "id": "gmail:m:0", "title": "Photo Walk", "date": "2026-11-14", "start": "14:00", "end": "16:00",
        "location": "Alma Mater", "description": "Bring a camera", "categories": "email",
        "url": "https://mail.google.com/mail/u/0/#all/m", "source": "email"}


class Inbox:
    def scan(self, start, end, usage=None):
        return {"activities": [dict(WALK)], "commitments": []}


class TestAgentFlow(LoggedTest):
    def test_request_then_approve(self):
        routes = iter(["retriever", "scheduler", "user",   # turn 1
                       "calendar", "user"])                # turn 2

        def call(messages, system=None, tools=None, **kw):
            prompt = messages[0]["content"] if messages and isinstance(messages[0]["content"], str) else ""
            if system and "LLM router" in system:
                return llm_text(next(routes))
            if "search keywords" in prompt:
                return llm_text("photo, camera")
            if "Rate each activity" in prompt:
                return llm_text("1: 9")
            if "You are a scheduler" in prompt:
                return llm_text("<plans>[[1]]</plans>")
            if "Reply with only YES or NO" in prompt:
                return llm_text("YES")
            if prompt.startswith("Verify if the response"):
                return llm_text('{"is_supported": true, "confidence": 0.9, "explanation": "ok"}')
            return llm_text("How about the Photo Walk on Saturday 14:00? Want me to add it?")

        cal = SimulatedCalendar()
        a = ECAgent(no_compact=True, today="2026-11-11", calendar=cal, inbox=Inbox(), use_web=False,
                    db_path="/nonexistent/campus.db")
        for module in (a, a.m, a.r, a.rec, a.s):
            module.call = call
        a.m.count_tokens = lambda msgs, system=None: 10
        a.load([], INTAKE, run_id="test/flow")

        reply, shown = a.run("Can you find me a photography activity this week?")
        self.assertEqual([x["title"] for x in shown], ["Photo Walk"])
        self.assertEqual(a.m.read_note("plan")[0]["title"], "Photo Walk")
        self.assertEqual(cal.get_events(), [])  # nothing added before approval

        reply, added = a.run("Yes please!")
        self.assertEqual([x["title"] for x in added], ["Photo Walk"])
        self.assertEqual([(e["title"], e["date"], e["start"]) for e in cal.get_events()], [("Photo Walk", "2026-11-14", "14:00")])
        self.assertIsNone(a.m.read_note("plan"))
        steps = [e["step"] for e in self.log_entries() if e["run_id"] == "test/flow"]
        for required in ("retrieve", "schedule", "check_approval", "add_event", "add_to_calendar"):
            self.assertIn(required, steps)
        self.assertLess(steps.index("retrieve"), steps.index("schedule"))
        self.assertLess(steps.index("schedule"), steps.index("add_event"))


if __name__ == "__main__":
    unittest.main()
