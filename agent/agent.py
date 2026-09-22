# TODO move generate_response to agent utils file
# TODO fix downstream imports

import requests

class ECAgent:
    def __init__(self, api_key):
        self.key = api_key
    
    # for eval pipeline tests
    def generate_response(self, user_input):
        ans = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-5",
                "max_tokens": 1024,
                "messages":[{"role":user, "content":user_input}]
            }
        )
        return ans.json()["content"][0]["text"].strip()