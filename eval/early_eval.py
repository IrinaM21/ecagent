# Early testing: rule-based vs single-pass LLM vs tool-using agent on the two easy tasks
# (match-interest, one-schedule), graded in code against eval_only in simulated-profiles.json.
#
# Run from the repo root:
#   python -m eval.early_eval --systems rule                 # no API key needed
#   python -m eval.early_eval                                # all three, needs ANTHROPIC_API_KEY
#
# Grading rules (one recommendation per run):
#   exists    event_id + date (or club_id) is in the frozen database snapshot      (C-exist)
#   fits      event lies inside one of the profile's available_windows             (C-busy, C-window, ...)
#             events with no listed time can't be verified and count as not fitting
#   interest  title/summary/type (or club name/categories/mission) has an interest_keyword
#   avoid     same text has an avoid_keyword (dislikes, superseded interests)      -> hard fail
#   match-interest passes if exists, interest, no avoid, and (club, or event that fits)
#   one-schedule   passes if exists, is an event that fits, and no avoid
# Hard constraints the database can't show (cost, alcohol, format, weekly caps) are not checked.

import argparse
import json
import os
import re
import sqlite3
from datetime import date as Date
from pathlib import Path

from scraper import db
from scraper.tools import DB_TOOLS

ROOT = Path(__file__).resolve().parent.parent
PROFILES = ROOT / "eval" / "simulated-profiles.json"
TASKS = ROOT / "eval" / "benchmark-tasks.json"
TASK_IDS = ["match-interest", "one-schedule"]
WEEK_START, WEEK_END = "2026-11-02", "2026-11-08"
DEFAULT_MINUTES = 60  # length assumed for events that only list a start time

MODEL = "claude-sonnet-5"
PRICE_PER_MTOK = {"input": 2.00, "output": 10.00}
MAX_AGENT_TURNS = 10
REQUEST_TIMEOUT = 300  # seconds per API call, so one stuck request can't hang the run

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
STOPWORDS = {"and", "the", "for", "with", "into"}


def minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def event_span(event):
    if not event["start_time"]:
        return None
    start = minutes(event["start_time"])
    end = minutes(event["end_time"]) if event["end_time"] else start + DEFAULT_MINUTES
    return start, end


def has_keyword(text, keywords):
    # keywords match at the start of a word, so "sustainab" hits "sustainability" but "bar" misses "Barbara"
    return any(re.search(r"\b" + re.escape(k), text, re.I) for k in keywords)


def item_text(item):
    keys = ["title", "summary", "event_type"] if "event_id" in item else ["name", "categories", "mission"]
    return " ".join(item[k] or "" for k in keys)


# ---------- grading ----------

def lookup(conn, selection):
    try:
        if selection.get("kind") == "event":
            row = conn.execute("SELECT * FROM events WHERE event_id = ? AND date = ?",
                               (int(selection["event_id"]), selection["date"])).fetchone()
        else:
            row = conn.execute("SELECT * FROM clubs WHERE club_id = ?",
                               (int(selection["club_id"]),)).fetchone()
    except (KeyError, TypeError, ValueError):
        return None
    return dict(row) if row else None


def grade(conn, profile, task_id, selection):
    truth = profile["eval_only"]
    item = lookup(conn, selection) if selection else None
    g = {"exists": item is not None, "kind": selection.get("kind") if selection else None,
         "fits": None, "interest": False, "avoid": False}
    if item:
        text = item_text(item)
        g["interest"] = has_keyword(text, truth["interest_keywords"])
        g["avoid"] = has_keyword(text, truth.get("avoid_keywords") or [])
        if g["kind"] == "event":
            span = event_span(item)
            g["fits"] = span is not None and any(
                w["date"] == item["date"] and minutes(w["start"]) <= span[0] and span[1] <= minutes(w["end"])
                for w in truth["available_windows"])
    if task_id == "match-interest":
        g["pass"] = g["exists"] and g["interest"] and not g["avoid"] and (g["kind"] == "club" or bool(g["fits"]))
    else:
        g["pass"] = g["exists"] and g["kind"] == "event" and bool(g["fits"]) and not g["avoid"]
    return g


# ---------- rule-based baseline (sees only the intake profile, never eval_only) ----------

def intake_fits(intake, day, span):
    if span is None:
        return False
    weekday = DAYS[Date.fromisoformat(day).weekday()]
    earliest, latest, buffer = 0, 24 * 60, 0
    busy = []
    for h in intake["hard_constraints"]:
        if h["type"] == "earliest_start":
            earliest = minutes(h["time"])
        elif h["type"] == "latest_end":
            latest = minutes(h["time"])
        elif h["type"] == "buffer_minutes":
            buffer = h["minutes"]
        elif h["type"] == "unavailable" and weekday in h["days"]:
            busy.append((minutes(h["start"]), minutes(h["end"])))
    for r in intake["schedule"].get("recurring", []):
        if weekday in r["days"]:
            busy.append((minutes(r["start"]) - buffer, minutes(r["end"]) + buffer))
    for o in intake["schedule"].get("one_off", []):
        if o["date"] == day:
            busy.append((minutes(o["start"]) - buffer, minutes(o["end"]) + buffer))
    start, end = span
    return earliest <= start and end <= latest and all(end <= b0 or start >= b1 for b0, b1 in busy)


def rule_based(conn, profile, task_id):
    intake = profile["agent_visible"]["intake_profile"]
    terms = {w for i in intake["interests"] for w in re.findall(r"[a-z]+", i.lower())
             if len(w) > 3 and w not in STOPWORDS}
    events = [dict(r) for r in conn.execute(
        "SELECT * FROM events WHERE date BETWEEN ? AND ? ORDER BY date, start_time", (WEEK_START, WEEK_END))]
    fitting = [e for e in events if intake_fits(intake, e["date"], event_span(e))]
    matching = [e for e in fitting if has_keyword(item_text(e), terms)]
    if matching:
        pick = matching[0]
    elif task_id == "match-interest":
        clubs = [dict(r) for r in conn.execute("SELECT * FROM clubs ORDER BY name")]
        pick = next((c for c in clubs if has_keyword(item_text(c), terms)), None)
    else:
        pick = fitting[0] if fitting else None
    if pick is None:
        return None, {"note": "no candidate found"}
    if "event_id" in pick:
        return {"kind": "event", "event_id": pick["event_id"], "date": pick["date"]}, {"title": pick["title"]}
    return {"kind": "club", "club_id": pick["club_id"]}, {"title": pick["name"]}


# ---------- LLM systems ----------

def system_prompt(profile, catalog=None):
    intake = profile["agent_visible"]["intake_profile"]
    prompt = f"""You help UIUC students find extracurricular activities.
Today is Monday, November 2, 2026. "This week" means November 2-8, 2026 (America/Chicago).

Student profile collected at sign-up (the conversation may have newer information):
{json.dumps(intake, indent=1)}

Recommend exactly one activity: a campus event or a student club. Only recommend events or clubs
that appear in the data you are given. End your reply with your pick in exactly this format:
<selection>{{"kind": "event", "event_id": 123, "date": "YYYY-MM-DD"}}</selection>
or
<selection>{{"kind": "club", "club_id": 456}}</selection>"""
    if catalog:
        prompt += "\n\n" + catalog
    return prompt


def catalog_text(conn):
    lines = ["Events this week (event_id | date | time | title | type | location):"]
    for e in conn.execute("SELECT * FROM events WHERE date BETWEEN ? AND ? ORDER BY date, start_time",
                          (WEEK_START, WEEK_END)):
        when = f"{e['start_time']}-{e['end_time'] or '?'}" if e["start_time"] else e["time_text"]
        lines.append(f"{e['event_id']} | {e['date']} | {when} | {e['title']} | {e['event_type']} | {e['location'] or ''}")
    lines.append("\nStudent clubs (club_id | name | categories):")
    for c in conn.execute("SELECT club_id, name, categories FROM clubs ORDER BY name"):
        lines.append(f"{c['club_id']} | {c['name']} | {c['categories']}")
    return "\n".join(lines)


def parse_selection(text):
    m = re.search(r"<selection>\s*(\{.*?\})\s*</selection>", text, re.S)
    try:
        return json.loads(m.group(1)) if m else None
    except json.JSONDecodeError:
        return None


def call(client, usage, **kwargs):
    response = client.messages.create(model=MODEL, max_tokens=16000, **kwargs)
    usage["input_tokens"] += response.usage.input_tokens
    usage["output_tokens"] += response.usage.output_tokens
    return response


def final_text(response):
    return "".join(b.text for b in response.content if b.type == "text")


def single_pass(client, conn, profile, query, catalog):
    usage = {"input_tokens": 0, "output_tokens": 0}
    messages = profile["agent_visible"]["conversation_history"] + [{"role": "user", "content": query}]
    response = call(client, usage, system=system_prompt(profile, catalog), messages=messages)
    text = final_text(response)
    return parse_selection(text), {"stop_reason": response.stop_reason, "reply": text, **usage}


def run_db_tool(snapshot, name, args):
    if name == "search_events":
        args = {"start_date": WEEK_START, **args}  # pin "today" to the reference week
        return db.search_events(path=snapshot, **args)
    return db.search_clubs(path=snapshot, **args)


def agent(client, conn, profile, query, snapshot):
    usage = {"input_tokens": 0, "output_tokens": 0}
    messages = profile["agent_visible"]["conversation_history"] + [{"role": "user", "content": query}]
    tool_calls = []
    for _ in range(MAX_AGENT_TURNS):
        response = call(client, usage, system=system_prompt(profile), tools=DB_TOOLS, messages=messages)
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            break
        results = []
        for block in response.content:
            if block.type == "tool_use":
                allowed = DB_TOOLS[0 if block.name == "search_events" else 1]["input_schema"]["properties"]
                args = {k: v for k, v in block.input.items() if k in allowed}
                tool_calls.append({"tool": block.name, "input": args})
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": json.dumps(run_db_tool(snapshot, block.name, args))})
        messages.append({"role": "user", "content": results})
    text = final_text(response)
    return parse_selection(text), {"stop_reason": response.stop_reason, "reply": text,
                                   "tool_calls": tool_calls, **usage}


# ---------- main ----------

def summarize(records):
    print(f"\n{'system':8} {'task':16} {'pass':>5} {'exists':>7} {'fits':>5} {'interest':>9} {'avoid':>6} {'cost':>7}")
    for system in dict.fromkeys(r["system"] for r in records):
        for task_id in TASK_IDS:
            rs = [r for r in records if r["system"] == system and r["task"] == task_id]
            if not rs:
                continue
            count = lambda key: sum(bool(r["grade"][key]) for r in rs)
            cost = sum(r.get("cost_usd", 0) for r in rs)
            print(f"{system:8} {task_id:16} {count('pass'):>3}/{len(rs)} {count('exists'):>5}/{len(rs)} "
                  f"{count('fits'):>3}/{len(rs)} {count('interest'):>7}/{len(rs)} {count('avoid'):>4}/{len(rs)} ${cost:>6.3f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--systems", default="rule,single,agent")
    parser.add_argument("--db", default=str(ROOT / "data" / "snapshot-2026-10-27.db"))
    parser.add_argument("--out", default=str(ROOT / "eval" / "early-results.json"))
    parser.add_argument("--resume", action="store_true", help="keep runs already in --out, do the rest")
    args = parser.parse_args()
    systems = args.systems.split(",")

    client = None
    if {"single", "agent"} & set(systems):
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise SystemExit("Set ANTHROPIC_API_KEY to run the LLM systems, or use --systems rule")
        import anthropic
        client = anthropic.Anthropic(timeout=REQUEST_TIMEOUT)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    profiles = json.load(open(PROFILES))
    queries = json.load(open(TASKS))
    catalog = catalog_text(conn) if "single" in systems else None

    records = json.load(open(args.out)) if args.resume and Path(args.out).exists() else []
    done = {(r["system"], r["profile"], r["task"]) for r in records}
    for system in systems:
        for profile_id, profile in profiles.items():
            for task_id in TASK_IDS:
                if task_id not in profile["eval_only"]["applicable_tasks"] or (system, profile_id, task_id) in done:
                    continue
                query = queries[task_id]
                if system == "rule":
                    selection, info = rule_based(conn, profile, task_id)
                elif system == "single":
                    selection, info = single_pass(client, conn, profile, query, catalog)
                else:
                    selection, info = agent(client, conn, profile, query, args.db)
                if "input_tokens" in info:
                    info["cost_usd"] = round((info["input_tokens"] * PRICE_PER_MTOK["input"]
                                              + info["output_tokens"] * PRICE_PER_MTOK["output"]) / 1e6, 4)
                g = grade(conn, profile, task_id, selection)
                item = lookup(conn, selection) if g["exists"] else None
                records.append({"system": system, "profile": profile_id, "task": task_id,
                                "selection": selection,
                                "picked": (item.get("title") or item.get("name")) if item else None,
                                "grade": g, **info})
                print(f"{system:7} {profile_id:16} {task_id:15} {'PASS' if g['pass'] else 'fail'}  "
                      f"{records[-1]['picked']}")
                # save as we go so a crash or interrupt keeps the finished runs
                json.dump(records, open(args.out, "w"), indent=1)

    summarize(records)
    print(f"\nWrote {len(records)} runs to {args.out}")


if __name__ == "__main__":
    main()
