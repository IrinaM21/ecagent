# eval grading tests (offline, no API calls): auto_grade against the fixture answer keys, manual checks,
# pass^3 and the step 7 report. Builds the fixture databases first (python -m eval.fixtures.build_fixtures).

import unittest

from unit_tests.helpers import LoggedTest

from eval.eval import AGENTS, NOTHING_FITS, Evaluator, manual_checks  # noqa: E402
from eval.fixtures import build_fixtures  # noqa: E402


def setUpModule():
    build_fixtures.main()  # fails loudly if any fixture is inconsistent with its task/profiles


def common(a):
    """Fixture row -> the common format the agent returns (what run_trial records as picked)."""
    return {"kind": a["kind"], "id": a["id"], "date": a.get("date"), "title": a["title"], "source": "db"}


class TestAutoGrade(LoggedTest):
    @classmethod
    def setUpClass(cls):
        cls.ev = Evaluator("test", api_key="test-key")

    def activity(self, task, key_id):
        kind, _, n = key_id.partition(":")
        fx = self.ev.fixtures[task]
        row = next(e for e in fx["events"] if e["event_id"] == int(n)) if kind == "event" else \
            next(c for c in fx["clubs"] if c["club_id"] == int(n))
        return common(self.ev.lookup({"kind": kind, "id": int(n), "date": row.get("date")}, task))

    def record(self, task, profile, picked, agent="full_agent", calendar=None, registered=(), error=None):
        return {"agent": agent, "profile": profile, "task": task, "trial": 1, "picked": picked, "error": error,
                "calendar": calendar or [], "registered": list(registered), "turns": [], "reply": ""}

    def test_reference_solution_passes_every_task(self):
        # the fixture's own answer must pass the automatic checks for every eval_instance
        for task, spec in self.ev.tasks.items():
            for profile in spec["profiles"]:
                ref = self.ev.answer_key(task, profile)["reference_solution"]
                picked = [self.activity(task, k) for k in ref]
                g = self.ev.auto_grade(self.record(task, profile, picked, agent="single_pass"))
                with self.subTest(task=task, profile=profile):
                    self.assertTrue(g["pass"], g["checks"])
                    self.assertTrue(all(v is not None for v in g["checks"].values()), g["checks"])

    def test_each_distractor_kind_fails(self):
        key = self.ev.answer_key("schedule-eca", "P3-picky")
        for bad, check in (("event:1006", "hard_constraints"),  # Zoom only, P3 is in-person only
                           ("event:1009", "hard_constraints"),  # $120 fee over P3's $25 cap
                           ("event:1007", "no_avoid"),          # networking night, a dislike
                           ("event:1008", "fits_schedule"),     # Friday after 16:30 (Shabbat)
                           ("event:1010", "interest")):         # investing, not P3's interest
            g = self.ev.auto_grade(self.record("schedule-eca", "P3-picky", [self.activity("schedule-eca", bad)]))
            with self.subTest(bad=bad):
                self.assertFalse(g["pass"])
                self.assertFalse(g["checks"][check], g["checks"])
        self.assertEqual(key["reference_solution"], ["event:1005"])

    def test_not_in_fixture_fails_exists(self):
        g = self.ev.auto_grade(self.record("schedule-eca", "P1-simple",
                                           [{"kind": "event", "id": 999999, "date": "2026-11-18", "title": "Made up"}]))
        self.assertFalse(g["checks"]["exists"])
        self.assertFalse(g["pass"])

    def test_empty_pick_goes_to_manual(self):
        r = self.record("schedule-eca", "P1-simple", [])
        g = self.ev.auto_grade(r)
        self.assertTrue(all(v is None for v in g["checks"].values()))
        self.assertIn(NOTHING_FITS, manual_checks(r, self.ev.tasks["schedule-eca"], {}))

    def test_actions_scored_for_full_agent_only(self):
        pick = self.activity("schedule-eca", "event:1001")
        added = [dict(pick, id="sim-1", start="18:00", end="19:30")]
        full = self.ev.auto_grade(self.record("schedule-eca", "P1-simple", [pick], calendar=added))
        single = self.ev.auto_grade(self.record("schedule-eca", "P1-simple", [pick], agent="single_pass"))
        self.assertTrue(full["action_checks"]["events_added:1"])
        self.assertTrue(full["action_pass"])
        self.assertIsNone(single["action_pass"])  # not compared to baselines
        self.assertEqual(full["checks"], single["checks"])  # same compared checks for every agent

    def test_adapt_schedule_weekly_cap_counts_kept_activities(self):
        # P4: max 2 activities/week. 8013 stays on the calendar, so one replacement is fine, two break the cap
        kept = next(e for e in self.ev.starting_events("adapt-schedule", "P4-changing") if e["id"] == 8013)
        one = [self.activity("adapt-schedule", "event:8002")]
        two = one + [self.activity("adapt-schedule", "event:8014")]
        for picked, ok in ((one, True), (two, False)):
            added = [dict(a, id=f"sim-{i}") for i, a in enumerate(picked, 3)]
            g = self.ev.auto_grade(self.record("adapt-schedule", "P4-changing", picked, calendar=[kept] + added))
            with self.subTest(n=len(picked)):
                self.assertEqual(g["checks"]["hard_constraints"], ok, g["checks"])
                self.assertTrue(g["action_checks"]["planned_event_removed"])  # 8001 (clashes with U1) is gone

    def test_error_fails(self):
        pick = self.activity("schedule-eca", "event:1001")
        self.assertFalse(self.ev.auto_grade(self.record("schedule-eca", "P1-simple", [pick], error="boom"))["pass"])


class TestPassK(LoggedTest):
    @classmethod
    def setUpClass(cls):
        cls.ev = Evaluator("test", api_key="test-key")

    def trial(self, agent, n, auto_pass, manual=None, quality=4):
        return {"agent": agent, "profile": "P1-simple", "task": "schedule-eca", "trial": n, "picked": [{"x": 1}],
                "auto_grade": {"pass": auto_pass, "action_pass": None}, "manual": manual or {}, "quality": quality}

    def test_pass_k_needs_every_trial(self):
        ok = {"reply_matches_calendar": True}
        records = [self.trial("full_agent", i, True, ok) for i in (1, 2, 3)] + \
                  [self.trial("single_pass", 1, True, ok), self.trial("single_pass", 2, False, ok),
                   self.trial("single_pass", 3, True, ok)] + \
                  [self.trial("no_compact", 1, True, ok), self.trial("no_compact", 2, True, {})]  # label missing
        pk = self.ev.pass_k(records)
        self.assertEqual(pk["full_agent"]["pass_k"], 1.0)
        self.assertEqual(pk["single_pass"]["pass_k"], 0.0)
        self.assertEqual(pk["no_compact"]["pending"], 1)  # undecided, not counted as a pass or a fail

    def test_report_overall(self):
        ok = {"reply_matches_calendar": True}
        records = []
        for agent, passes in (("full_agent", True), ("no_compact", False), ("single_pass", False)):
            records += [self.trial(agent, i, passes, ok) for i in (1, 2, 3)]
        rep = self.ev.report(records)
        self.assertEqual(set(rep["pass^3"]), set(AGENTS))
        self.assertTrue(rep["pass_overall"])
        records[0]["quality"] = None  # a missing score -> undecided, never a guess
        self.assertIsNone(self.ev.report(records)["pass_overall"])


if __name__ == "__main__":
    unittest.main()
