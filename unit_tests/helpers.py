# shared test setup: dummy API key, temp action log, fakes for Google and the LLM

import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")  # modules need a key; tests never call the API

import utils  # noqa: E402


# writes the action log to a temp file instead of agent/model_log.json
class LoggedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_log = utils.LOG_PATH
        utils.LOG_PATH = Path(self.tmp.name) / "log.json"

    def tearDown(self):
        utils.LOG_PATH = self.old_log
        self.tmp.cleanup()

    def log_entries(self, step=None):
        if not utils.LOG_PATH.exists():
            return []
        rows = [json.loads(l) for l in utils.LOG_PATH.read_text().splitlines()]
        return [r for r in rows if step is None or r["step"] == step]


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self.text = "" if body is None else json.dumps(body)

    def json(self):
        return json.loads(self.text)


# stands in for GoogleAuth: (method, url part) -> canned response, and records the calls
class FakeAuth:
    def __init__(self, routes):
        self.routes = routes  # list of (method, url_part, result or callable(kwargs) -> result)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        for m, part, result in self.routes:
            if m == method and part in url:
                result = result(kwargs) if callable(result) else result
                if isinstance(result, Exception):
                    raise result
                return result
        raise AssertionError(f"unexpected request {method} {url}")


# a fake model reply with one text block
def llm_text(text):
    return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
            "usage": {"input_tokens": 0, "output_tokens": 0}}
