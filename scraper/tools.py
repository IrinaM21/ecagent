# Tool definitions for the agent: query the scraped database first, fall back to web search +
# fetch for anything more specific (a single club's meetings, events not on the calendars).
#
# Usage in a Messages API call:  tools=TOOLS
# When the model returns a tool_use block for search_events / search_clubs, call
# run_tool(block.name, block.input) and send the string back as the tool_result.
# web_search / web_fetch run on Anthropic's side, no handling needed.

import json

from scraper import db

DB_TOOLS = [
    {
        "name": "search_events",
        "description": "Search general campus events (university + Illini Union calendars, "
                       "scraped weekly). Returns title, date, start/end time, location, summary, "
                       "type and url. Check scraped_at and re-fetch the url before adding an "
                       "event to a calendar, since details can change.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keywords, all must match. Empty for everything."},
                "start_date": {"type": "string", "description": "YYYY-MM-DD, defaults to today"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD, inclusive"},
                "limit": {"type": "integer", "description": "Max results, default 20"},
            },
        },
    },
    {
        "name": "search_clubs",
        "description": "Search the directory of registered student organizations (RSOs). Returns "
                       "name, categories, website, mission and member benefits. Does not include "
                       "meeting times or events, use web_search / web_fetch on the club's website "
                       "for those.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keywords, all must match. Empty for everything."},
                "limit": {"type": "integer", "description": "Max results, default 20"},
            },
        },
    },
]

# Anthropic server tools. These versions are for Sonnet 5; Haiku 4.5 needs the older
# web_search_20250305 / web_fetch_20250910. max_uses keeps cost down (search is billed per use).
WEB_TOOLS = [
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 3},
    {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 3, "max_content_tokens": 5000},
]

TOOLS = DB_TOOLS + WEB_TOOLS


HANDLERS = {"search_events": db.search_events, "search_clubs": db.search_clubs}


def run_tool(name, tool_input):
    if name not in HANDLERS:
        raise ValueError(f"Unknown tool: {name}")
    # only pass through arguments the tool's schema declares
    schema = next(t for t in DB_TOOLS if t["name"] == name)["input_schema"]
    args = {k: v for k, v in tool_input.items() if k in schema["properties"]}
    return json.dumps(HANDLERS[name](**args))
