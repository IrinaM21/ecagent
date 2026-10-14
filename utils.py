import json
import requests

API_URL = "https://api.anthropic.com/v1/messages"
TOKEN_URL = API_URL + "/count_tokens"


class Utils:
    def __init__(self):
        pass
    
    def get_json(self, model, max_tokens, messages):
        return {"model": model,"max_tokens": max_tokens,"messages":messages}
    
    def get_headers(self, key):
        return {"x-api-key": key,"anthropic-version": "2023-06-01","content-type": "application/json",}

    def post_to_url(self, url, h, j):
        return requests.post(TOKEN_URL, headers=h, json=j)