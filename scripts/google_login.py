# one-time Google login: opens your browser and saves the login outside the repo
# run from the repo root: python -m scripts.google_login (redo it every 7 days while the app is in Testing mode)

import sys

from agent.google_auth import CLIENT_SECRET_PATH, SCOPES, TOKEN_PATH


def main():
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        sys.exit("Missing package: pip install google-auth-oauthlib")
    if not CLIENT_SECRET_PATH.exists():
        sys.exit(f"{CLIENT_SECRET_PATH} not found. Download the OAuth client (Desktop app) JSON from the "
                 f"Google Cloud console and save it there (see README, Google setup).")
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_PATH), SCOPES)
    # prompt=consent makes Google return a refresh token even if you logged in before
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    if not creds.refresh_token:
        sys.exit("Google returned no refresh token. Remove the app's access at myaccount.google.com/permissions and rerun.")
    TOKEN_PATH.write_text(creds.to_json())
    print(f"Saved login to {TOKEN_PATH}")


if __name__ == "__main__":
    main()
