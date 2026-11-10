import json
import os
import time
from datetime import datetime
from pathlib import Path

import requests

API_URL = "https://api.anthropic.com/v1/messages"
TOKEN_URL = API_URL + "/count_tokens"
LOG_PATH = Path(__file__).resolve().parent / "agent" / "model_log.json"

MODEL = "claude-sonnet-4-6"  # one place to change the model for every module
MAX_TOKENS = 16000
TIMEOUT = 300  # seconds per request, so one stuck call can't hang an eval run
RETRY_STATUS = {429, 500, 502, 503, 529}  # rate limit / server errors worth retrying


class Utils:
    def __init__(self, api_key=None):
        # fall back to the environment so modules can be created without passing a key around
        self.key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.key:
            raise ValueError("No API key: pass api_key or set ANTHROPIC_API_KEY")
        self.run_id = None  # set by the agent so log entries from one run can be grouped

    def log(self, step, **data):
        """
        Append one trajectory step / update to agent/model_log.json.
        One JSON object per line, so entries can be added without rewriting the file.
        """
        entry = {"time": datetime.now().isoformat(timespec="seconds"), "run_id": self.run_id,
                 "module": type(self).__name__, "step": step, **data}
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def get_headers(self):
        return {"x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"}

    def post_to_url(self, url, body, retries=3):
        """POST body to url, retrying rate-limit/server errors with backoff. Returns the response json."""
        for attempt in range(retries):
            response = requests.post(url, headers=self.get_headers(), json=body, timeout=TIMEOUT)
            if response.status_code not in RETRY_STATUS or attempt == retries - 1:
                break
            time.sleep(2 ** attempt)  # 1s, 2s, ...
        if not response.ok:
            raise RuntimeError(f"API error {response.status_code}: {response.text}")
        return response.json()

    def call(self, messages, system=None, tools=None, max_tokens=MAX_TOKENS, usage=None):
        """One Messages API call. Adds this call's token counts to `usage` if given (for eval cost)."""
        body = {"model": MODEL, "max_tokens": max_tokens, "messages": messages}
        if system:
            body["system"] = system
        if tools:
            body["tools"] = tools
        ans = self.post_to_url(API_URL, body)
        if usage is not None:
            usage["input_tokens"] = usage.get("input_tokens", 0) + ans["usage"]["input_tokens"]
            usage["output_tokens"] = usage.get("output_tokens", 0) + ans["usage"]["output_tokens"]
        return ans

    def count_tokens(self, messages, system=None):
        """Exact token count, used by Memory to decide when to compact."""
        body = {"model": MODEL, "messages": messages}
        if system:
            body["system"] = system
        return self.post_to_url(TOKEN_URL, body)["input_tokens"]

    @staticmethod
    def text_of(ans):
        """All text blocks in a response, joined (skips tool_use and server-tool blocks)."""
        return "".join(b["text"] for b in ans["content"] if b["type"] == "text").strip()
