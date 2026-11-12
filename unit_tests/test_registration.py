import unittest

from agent.registration import ManualRegistration, RegistrationError, SimulatedRegistration
from unit_tests.helpers import LoggedTest

ACT = {"id": 3, "title": "Resume Workshop", "date": "2026-11-16", "source": "db", "url": "https://example.com/r"}


class TestRegistration(LoggedTest):
    def test_fail_once_then_register(self):
        reg = SimulatedRegistration(fail_on={1})
        with self.assertRaises(RegistrationError):
            reg.register(ACT)
        self.assertFalse(reg.is_registered(ACT))
        self.assertEqual(reg.register(ACT), {"status": "registered", "confirmation": "REG-0001"})
        self.assertTrue(reg.is_registered(ACT))
        self.assertEqual(reg.register(ACT)["confirmation"], "REG-0001")  # same activity, same confirmation
        self.assertEqual([e["ok"] for e in self.log_entries("register")], [False, True, True])

    def test_manual_never_claims_registration(self):
        reg = ManualRegistration()
        self.assertEqual(reg.register(ACT), {"status": "link", "url": "https://example.com/r"})
        self.assertFalse(reg.is_registered(ACT))


if __name__ == "__main__":
    unittest.main()
