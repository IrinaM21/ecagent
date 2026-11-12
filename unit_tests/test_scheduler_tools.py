import unittest

from agent.calendars import CalendarError, SimulatedCalendar
from agent.scheduler import Scheduler
from unit_tests.helpers import LoggedTest

STATE = {"hard_constraints": [{"type": "earliest_start", "time": "08:00"}, {"type": "latest_end", "time": "22:00"},
                              {"type": "max_activities_per_week", "value": 1}],
         "schedule": {"reference_week": {"start": "2026-11-02"}, "recurring": [],
                      "one_off": [{"id": "U1", "date": "2026-11-05", "start": "18:00", "end": "20:00"}]}}
ACT = {"kind": "event", "id": 7, "title": "Workshop", "date": "2026-11-06", "start": "17:00", "end": None, "source": "db"}


class TestScheduler(LoggedTest):
    def setUp(self):
        super().setUp()
        self.s = Scheduler(today="2026-11-02")

    def test_busy_blocks_use_default_length(self):
        busy = self.s.busy_blocks(STATE, [{"date": "2026-11-06", "start": "17:00", "end": None},
                                          {"date": "2026-11-07", "start": None}])
        self.assertIn({"date": "2026-11-06", "start": "17:00", "end": "18:00", "source": "calendar"}, busy)
        self.assertEqual(len(busy), 2)  # the one_off + the timed event; all-day skipped

    def test_find_conflicts(self):
        planned = {"id": "sim-1", "title": "Board games", "date": "2026-11-05", "start": "18:30", "end": "19:30", "agent_added": True}
        fine = {"id": "sim-2", "title": "Talk", "date": "2026-11-05", "start": "12:00", "end": "13:00", "agent_added": True}
        cls = {"id": "g1", "title": "Class", "date": "2026-11-05", "start": "12:00", "end": "13:00", "agent_added": False}
        self.assertEqual(self.s.find_conflicts(STATE, [planned, fine]), [planned])
        self.assertEqual(self.s.find_conflicts(STATE, [fine, cls]), [fine])  # overlaps a class

    def test_caps_count_only_agent_added(self):
        cls = {"title": "Class", "date": "2026-11-03", "start": "09:00", "end": "10:00", "agent_added": False}
        busy = self.s.busy_blocks(STATE, [cls])
        planned = [e for e in [cls] if e.get("agent_added", True)]
        self.assertEqual(self.s.check_plan([ACT], STATE, busy, planned), [])
        self.assertTrue(self.s.check_plan([ACT], STATE, busy, [dict(cls, agent_added=True)]))  # cap of 1 reached

    def test_add_verified(self):
        cal = SimulatedCalendar()
        [r] = self.s.add_to_calendar([ACT], cal)
        self.assertTrue(r["verified"])
        self.assertIsNone(r["error"])

    def test_add_retry_after_failure(self):
        cal = SimulatedCalendar(fail_on={"add_event": [1]})
        [r] = self.s.add_to_calendar([ACT], cal)
        self.assertTrue(r["verified"])
        self.assertEqual(len(cal.get_events()), 1)

    def test_failed_call_that_went_through_is_not_duplicated(self):
        class Flaky(SimulatedCalendar):
            def add_event(self, activity):
                super().add_event(activity)
                raise CalendarError("timeout after write")
        cal = Flaky()
        [r] = self.s.add_to_calendar([ACT], cal)
        self.assertTrue(r["verified"])
        self.assertEqual(len(cal.get_events()), 1)

    def test_add_fails_twice(self):
        cal = SimulatedCalendar(fail_on={"add_event": [1, 2]})
        [r] = self.s.add_to_calendar([ACT], cal)
        self.assertFalse(r["verified"])
        self.assertIn("simulated", r["error"])
        self.assertIn("NOT added 1", self.s.summarize_added([r]))

    def test_unverifiable_add(self):
        cal = SimulatedCalendar(fail_on={"get_events": [1, 2]})
        [r] = self.s.add_to_calendar([ACT], cal)
        self.assertFalse(r["verified"])
        self.assertIn("read back", r["error"])

    def test_remove(self):
        cal = SimulatedCalendar(events=[{"id": "sim-1", "title": "Old", "date": "2026-11-05", "start": "18:30"}])
        [r] = self.s.remove_from_calendar(cal.get_events(), cal)
        self.assertTrue(r["removed"])
        self.assertEqual(cal.get_events(), [])
        self.assertIn("removed: Old", self.s.summarize_added([], [r]))


if __name__ == "__main__":
    unittest.main()
