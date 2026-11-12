import json
import tempfile
import unittest
from pathlib import Path

from agent.google_auth import GoogleAPIError, GoogleAuth
from unit_tests.helpers import FakeResponse


class FakeHTTP:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


TOKEN = FakeResponse(200, {"access_token": "at", "expires_in": 3600})


class TestGoogleAuth(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "token.json"
        self.path.write_text(json.dumps({"client_id": "id", "client_secret": "s", "refresh_token": "rt", "token": "old"}))

    def tearDown(self):
        self.tmp.cleanup()

    def auth(self, responses):
        a = GoogleAuth(self.path, http=FakeHTTP(responses))
        a.sleep = lambda s: None
        return a

    def test_missing_token_file(self):
        with self.assertRaises(FileNotFoundError):
            GoogleAuth(Path(self.tmp.name) / "nope.json")

    def test_refresh_then_request(self):
        a = self.auth([TOKEN, FakeResponse(200, {"ok": 1})])
        self.assertEqual(a.request("GET", "https://x"), {"ok": 1})
        method, url, kw = a.http.calls[0]
        self.assertEqual(kw["data"]["grant_type"], "refresh_token")
        self.assertEqual(a.http.calls[1][2]["headers"]["Authorization"], "Bearer at")

    def test_retry_on_server_error_and_401(self):
        a = self.auth([TOKEN, FakeResponse(503, {}), FakeResponse(401, {}), TOKEN, FakeResponse(200, {"ok": 2})])
        self.assertEqual(a.request("GET", "https://x"), {"ok": 2})

    def test_empty_body_and_errors(self):
        a = self.auth([TOKEN, FakeResponse(204, None), FakeResponse(404, {"error": "nf"})])
        self.assertIsNone(a.request("DELETE", "https://x"))
        with self.assertRaises(GoogleAPIError) as e:
            a.request("GET", "https://x")
        self.assertEqual(e.exception.status, 404)

    def test_refresh_failure_explains_relogin(self):
        a = self.auth([FakeResponse(400, {"error": "invalid_grant"})])
        with self.assertRaises(GoogleAPIError) as e:
            a.request("GET", "https://x")
        self.assertIn("google_login", str(e.exception))


if __name__ == "__main__":
    unittest.main()
