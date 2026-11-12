# agent tool steps with the LLM stubbed: approval, registration retry, verified adds, replacing conflicts

import unittest

from agent.agent import ECAgent
from agent.calendars import SimulatedCalendar
from agent.registration import SimulatedRegistration
from unit_tests.helpers import LoggedTest, llm_text

PLAN = [{"kind": "event", "id": 11, "title": "Resume Workshop", "date": "2026-11-05", "start": "12:00",
         "end": "13:00", "location": "Union 210", "description": "", "url": "https://example.com", "source": "db"}]
STATE = {"hard_constraints": [], "schedule": {"reference_week": {"start": "2026-11-02"}, "recurring": [],
                                              "one_off": [{"id": "U1", "date": "2026-11-05", "start": "18:00", "end": "20:00"}]}}


def fake_llm(approve="YES"):
    def call(messages, system=None, **kw):
        prompt = messages[0]["content"] if messages and isinstance(messages[0]["content"], str) else ""
        if "Reply with only YES or NO" in prompt:
            return llm_text(approve)
        if "Verify if the response" in prompt:
            return llm_text('{"is_supported": true, "confidence": 1, "explanation": "ok"}')
        return llm_text("Done! Here's what happened.")
    return call


class FakeSource:
    def __init__(self, commitments):
        self.commitments = commitments

    def scan(self, start, end, usage=None):
        return {"activities": [], "commitments": self.commitments}


def agent(route, approve="YES", calendar=None, registration=None, inbox=None):
    a = ECAgent(no_compact=True, today="2026-11-02", calendar=calendar or SimulatedCalendar(),
                registration=registration or SimulatedRegistration(fail_on={1}), inbox=inbox, use_web=False)
    a.call = fake_llm(approve)
    a.m.count_tokens = lambda msgs, system=None: 10
    a.m.input_limit = lambda: 200_000  # no Models API call
    a.m.write_note("state", STATE)
    names = iter(route)
    a.route_calls = []

    def scripted(task, results, usage=None, subagents=None):
        a.route_calls.append([s["name"] for s in subagents])
        return {"name": next(names, "user")}
    a.route = scripted
    return a


class TestAgentTools(LoggedTest):
    def test_approved_register_then_calendar(self):
        a = agent(["register", "calendar", "user"])
        a.m.write_note("plan", PLAN)
        reply, selection = a.run("Yes, register me and add it!")
        self.assertEqual([e["title"] for e in a.calendar.get_events()], ["Resume Workshop"])
        self.assertEqual(selection[0]["title"], "Resume Workshop")
        self.assertIsNone(a.m.read_note("plan"))
        steps = [e["step"] for e in self.log_entries() if e["step"] in ("register", "add_event")]
        self.assertEqual(steps, ["register", "register", "add_event"])  # failed try, retry, then the calendar
        self.assertEqual([e["ok"] for e in self.log_entries("register")], [False, True])
        self.assertIn("register", a.route_calls[0])
        self.assertNotIn("register", a.route_calls[1])  # once per turn
        self.assertNotIn("calendar", a.route_calls[2])  # plan already added

    def test_not_approved_offers_no_calendar(self):
        a = agent(["calendar", "user"], approve="NO")
        a.m.write_note("plan", PLAN)
        a.run("hmm maybe, what else is there?")
        self.assertEqual(a.calendar.get_events(), [])
        self.assertTrue(all("calendar" not in opts and "register" not in opts for opts in a.route_calls))
        self.assertIsNone(a.m.read_note("plan"))  # approval must come right after the proposal

    def test_no_plan_no_approval_call(self):
        a = agent(["user"])
        a.check_approval = lambda *args, **kw: self.fail("no plan pending, nothing to approve")
        a.run("Can you find me an activity?")

    def test_calendar_failure_is_reported_not_claimed(self):
        a = agent(["calendar", "user"], calendar=SimulatedCalendar(fail_on={"add_event": [1, 2]}))
        a.m.write_note("plan", PLAN)
        reply, selection = a.run("yes")
        self.assertEqual(selection, [])
        self.assertIn("NOT added 1", self.log_entries("run")[-1]["results"][-1])

    def test_replaces_conflicting_activity(self):
        old = {"id": "sim-1", "title": "Board Game Night", "date": "2026-11-05", "start": "18:30", "end": "19:30"}
        a = agent([], calendar=SimulatedCalendar(events=[old]))
        a.s.schedule = lambda task, state, acts, n_items=1, existing=(), usage=None: \
            self.assertEqual(existing, []) or [PLAN]  # the conflicting activity is not busy time
        data = {}
        summary = a.step_scheduler("redo my plan", data)
        self.assertIn("now conflicting", summary)
        self.assertEqual([e["id"] for e in data["replaces"]], ["sim-1"])
        a.step_user("redo my plan", data, [summary])
        self.assertEqual(a.m.read_note("replaces")[0]["id"], "sim-1")
        out = a.step_calendar("yes", {})
        self.assertEqual([e["title"] for e in a.calendar.get_events()], ["Resume Workshop"])
        self.assertIn("removed: Board Game Night", out)
        self.assertIsNone(a.m.read_note("replaces"))

    def test_email_commitments_are_busy_time(self):
        interview = {"id": "gmail:m:0", "title": "Interview", "date": "2026-11-05", "start": "12:00", "end": "12:30",
                     "source": "email", "agent_added": False}
        a = agent([], inbox=FakeSource([interview]))
        seen = {}
        a.s.schedule = lambda task, state, acts, n_items=1, existing=(), usage=None: seen.update(existing=existing) or []
        a.step_scheduler("find something", {})
        self.assertEqual(seen["existing"], [interview])

    def test_unreadable_calendar_is_reported(self):
        a = agent([], calendar=SimulatedCalendar(fail_on={"get_events": [1]}))
        a.s.schedule = lambda *args, **kw: []
        self.assertIn("WARNING calendar could not be read", a.step_scheduler("find something", {}))

    def test_check_approval(self):
        a = agent([], approve="YES")
        self.assertTrue(a.check_approval("sure!", PLAN))
        a.call = fake_llm("NO")
        self.assertFalse(a.check_approval("what about Friday?", PLAN))


class TestRetrieverEmail(LoggedTest):
    def test_email_candidates_without_database(self):
        from agent.retriever import Retriever
        email_act = {"kind": "event", "id": "gmail:m:0", "title": "Photo Walk", "date": "2026-11-14", "start": "14:00",
                     "end": "16:00", "location": None, "description": "", "categories": "email", "url": "u", "source": "email"}

        class Src:
            def scan(self, start, end, usage=None):
                self.window = (start, end)
                return {"activities": [dict(email_act)], "commitments": []}
        src = Src()
        r = Retriever(db_path="/nonexistent/campus.db", today="2026-11-11", inbox=src, use_web=False)
        r.keywords = lambda q, s, usage=None: ["photo"]
        r.score = lambda q, s, cands, usage=None: [dict(c, score=8) for c in cands]
        out = r.retrieve("photo walk", {"interests": ["photography"]})
        self.assertEqual([c["title"] for c in out], ["Photo Walk"])
        self.assertEqual(src.window, ("2026-11-11", "2026-11-17"))
        log = self.log_entries("retrieve")[-1]
        self.assertEqual((log["email_found"], log["web_used"]), (1, False))
        self.assertIn("not found", log["db_error"])
        self.assertIn("1 from email", r.summarize(out))


if __name__ == "__main__":
    unittest.main()
