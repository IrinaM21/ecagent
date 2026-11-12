# Google login for the calendar + gmail tools (requests only, no Google library)
# secrets stay outside the repo: ../client_secret.json and ../google-token.json (from scripts/google_login.py)

import json
import os
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
SECRETS_DIR = Path(os.environ.get("ECAGENT_SECRETS_DIR", ROOT.parent))
CLIENT_SECRET_PATH = Path(os.environ.get("ECAGENT_GOOGLE_CLIENT_SECRET", SECRETS_DIR / "client_secret.json"))
TOKEN_PATH = Path(os.environ.get("ECAGENT_GOOGLE_TOKEN", SECRETS_DIR / "google-token.json"))

TOKEN_URL = "https://oauth2.googleapis.com/token"
# calendar: read the student's calendars + create/write the ECAgent calendar; gmail: read only, never send
SCOPES = ["https://www.googleapis.com/auth/calendar", "https://www.googleapis.com/auth/gmail.readonly"]
RETRY_STATUS = {429, 500, 502, 503, 504}
TIMEOUT = 30  # seconds per request


class GoogleAPIError(RuntimeError):
    def __init__(self, status, message):
        super().__init__(f"Google API error {status}: {message}")
        self.status = status


class GoogleAuth:
    # http is requests (tests pass a fake)
    def __init__(self, token_path=TOKEN_PATH, http=requests):
        self.token_path = Path(token_path)
        self.http = http
        self.sleep = time.sleep  # tests replace this so retries don't wait
        if not self.token_path.exists():
            raise FileNotFoundError(f"{self.token_path} not found: run `python -m scripts.google_login` first")
        info = json.loads(self.token_path.read_text())
        self.client_id, self.client_secret = info["client_id"], info["client_secret"]
        self.refresh_token = info["refresh_token"]
        self.access_token, self.expires_at = None, 0.0

    # fresh access token when the old one is missing or about to expire
    def token(self):
        if self.access_token is None or time.time() > self.expires_at - 60:
            r = self.http.request("POST", TOKEN_URL, timeout=TIMEOUT, data={
                "client_id": self.client_id, "client_secret": self.client_secret,
                "refresh_token": self.refresh_token, "grant_type": "refresh_token"})
            if r.status_code != 200:
                raise GoogleAPIError(r.status_code, f"token refresh failed ({r.text[:200]}). If the login "
                                                    f"expired (every 7 days in Testing mode), rerun `python -m scripts.google_login`.")
            ans = r.json()
            self.access_token = ans["access_token"]
            self.expires_at = time.time() + ans.get("expires_in", 3600)
        return self.access_token

    # authorized request: retries rate limits/server errors, refreshes once on 401
    def request(self, method, url, retries=3, **kwargs):
        for attempt in range(retries):
            r = self.http.request(method, url, headers={"Authorization": f"Bearer {self.token()}"},
                                  timeout=TIMEOUT, **kwargs)
            if r.status_code == 401 and attempt < retries - 1:
                self.access_token = None  # revoked or expired early: refresh and try again
                continue
            if r.status_code not in RETRY_STATUS or attempt == retries - 1:
                break
            self.sleep(2 ** attempt)
        if r.status_code >= 400:
            raise GoogleAPIError(r.status_code, r.text[:300])
        return r.json() if r.text.strip() else None
