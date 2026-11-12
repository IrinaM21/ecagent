# Live tests (real Claude API calls, cost money): one eval trial per agent/ablation on schedule-eca / P1,
# through the real Evaluator (scripted turns, fixture DB, auto_grade). Skipped unless ECAGENT_LIVE_TESTS=1,
# so a normal unittest run never spends anything. The key comes from ANTHROPIC_API_KEY (never stored in files).
#
#   ECAGENT_LIVE_TESTS=1 ANTHROPIC_API_KEY=... python -m unittest unit_tests.test_live -v

import json
import os
import unittest
from datetime import datetime

from unit_tests.helpers import LoggedTest

from eval.eval import AGENTS, ROOT, Evaluator  # noqa: E402
from eval.fixtures import build_fixtures  # noqa: E402

LIVE = os.environ.get("ECAGENT_LIVE_TESTS") == "1"
RAW = ROOT / "eval" / "results" / "live-test.json"  # raw records of every live test run, timestamped


@unittest.skipUnless(LIVE, "live API test: set ECAGENT_LIVE_TESTS=1")
class TestLiveTrial(LoggedTest):
    @classmethod
    def setUpClass(cls):
        build_fixtures.main()
        cls.ev = Evaluator("live test")
        cls.records = {}
        for agent in AGENTS:
            record = cls.ev.run_trial(agent, "P1-simple", "schedule-eca", 1)
            record["auto_grade"] = cls.ev.auto_grade(record)
            cls.records[agent] = record
        # raw per-trial records, one timestamped run appended per test run (earlier runs are kept)
        runs = json.loads(RAW.read_text()) if RAW.exists() else []
        runs = runs if isinstance(runs, list) else [runs]  # the first run was saved on its own
        runs.append({"run": datetime.now().isoformat(timespec="seconds"), "records": cls.records})
        RAW.write_text(json.dumps(runs, indent=1))

    def test_every_agent_completes_the_scripted_turns(self):
        turns = len(self.ev.tasks["schedule-eca"]["turns"])
        for agent, r in self.records.items():
            with self.subTest(agent=agent):
                self.assertIsNone(r["error"])
                self.assertEqual(len(r["turns"]), turns)
                self.assertTrue(all(t["reply"] for t in r["turns"]))
                self.assertGreater(r["usage"].get("input_tokens", 0), 0)

    def test_picks_come_from_the_fixture(self):
        # whatever an agent picks must be a real fixture row (exists), never an invented activity
        for agent, r in self.records.items():
            if r["picked"]:
                with self.subTest(agent=agent):
                    self.assertTrue(r["auto_grade"]["checks"]["exists"], r["picked"])

    def test_full_agent_only_adds_after_approval(self):
        r = self.records["full_agent"]
        first, second = r["turns"]
        self.assertNotIn("sim-", " ".join(str(a.get("id")) for a in first["selection"]))  # proposal, not added yet
        if r["picked"]:
            self.assertTrue(r["calendar"], "approved plan should be on the simulated calendar")

    def test_single_pass_never_acts(self):
        self.assertEqual(self.records["single_pass"]["calendar"], [])
        self.assertIsNone(self.records["single_pass"]["auto_grade"]["action_pass"])

    @classmethod
    def tearDownClass(cls):
        for agent, r in cls.records.items():  # printed for a human look at what happened
            g = r["auto_grade"]
            print(f"\n{agent}: picked={[a['title'] for a in r['picked']]} pass={g['pass']} checks={g['checks']} "
                  f"actions={g['action_checks']} tokens={r['usage']}")


if __name__ == "__main__":
    unittest.main()
