import unittest

from agent.memory import Memory
from unit_tests.helpers import LoggedTest


class FakeCompactor:
    def __init__(self):
        self.seen = []

    def compact_user_info(self, previous, msgs, usage=None):
        self.seen.append(msgs)
        return "1. summary"


def memory(compactor):
    m = Memory(compactor=compactor, max_tokens=50)
    m.count_tokens = lambda msgs, system=None: sum(len(x["content"]) for x in msgs) // 4
    return m


HISTORY = [{"role": "user", "content": "a" * 100}, {"role": "assistant", "content": "b" * 100},
           {"role": "user", "content": "c" * 100}, {"role": "assistant", "content": "d" * 100}]


class TestManageContext(LoggedTest):
    def test_compaction_keeps_latest_user_message(self):
        c = FakeCompactor()
        m = memory(c)
        m.resolve = lambda msgs, usage=None: None
        m.msgs = list(HISTORY)
        m.add_message({"role": "user", "content": "new request"})
        self.assertEqual([x["content"] for x in m.msgs], ["new request"])
        self.assertEqual(m.read_note("summary"), "1. summary")
        self.assertEqual(c.seen[-1], HISTORY)  # the new message was not summarized away

    def test_truncation_keeps_latest_user_message(self):
        m = memory(None)
        m.msgs = list(HISTORY)
        m.add_message({"role": "user", "content": "new request " + "x" * 300})
        self.assertEqual(len(m.msgs), 1)
        self.assertTrue(m.msgs[0]["content"].startswith("new request"))

    def test_truncation_starts_with_user_turn(self):
        m = memory(None)
        m.msgs = list(HISTORY)
        m.add_message({"role": "user", "content": "short"})
        self.assertEqual(m.msgs[0]["role"], "user")
        self.assertEqual(m.msgs[-1]["content"], "short")
        self.assertLessEqual(m.count, 50)


if __name__ == "__main__":
    unittest.main()
