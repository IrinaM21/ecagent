# Build the per-task fixture databases (data/fixtures/<task>.db) from eval/fixtures/<task>.json,
# after checking every fixture against its task (eval/benchmark-tasks.json) and the profiles.
#
# Run from the repo root (no flags):
#   python -m eval.fixtures.build_fixtures
#
# A fixture is a small, clean, synthetic catalog + an answer key written before any eval run
# (docs/design-decisions.md, Evaluation). The DB only gets the activities, never the answer key.
# Checks: schema, ids, dates in the reference week, answer key covers exactly the task's profiles,
# and each profile's reference_solution is exactly what the key + the profile's free time allow.
# Nothing is written if any check fails.

import json
import sys
from datetime import date
from pathlib import Path

from scraper import db

ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES = ROOT / "eval" / "fixtures"
OUT = ROOT / "data" / "fixtures"
TASKS = ROOT / "eval" / "benchmark-tasks.json"
PROFILES = ROOT / "eval" / "profiles" / "simulated-profiles.json"
WEEK = ("2026-11-16", "2026-11-22")
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
EVENT_FIELDS = {"event_id", "date", "title", "start_time", "end_time", "location", "event_type", "summary"}
CLUB_FIELDS = {"club_id", "name", "categories", "mission"}
LEAK_WORDS = ("distractor", "reference solution", "answer key", "correct answer", "near miss")  # grader words


def mins(t):
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def key_of(kind, row):
    return f"event:{row['event_id']}" if kind == "event" else f"club:{row['club_id']}"


def fits(e, windows, extra_busy=()):
    """Inside one available window on its date, not overlapping extra busy time (runtime updates)."""
    s, t = mins(e["start_time"]), mins(e["end_time"])
    return any(w["date"] == e["date"] and mins(w["start"]) <= s and t <= mins(w["end"]) for w in windows) and \
        not any(b["date"] == e["date"] and s < mins(b["end"]) and mins(b["start"]) < t for b in extra_busy)


def overlap(a, b):
    return a["date"] == b["date"] and mins(a["start_time"]) < mins(b["end_time"]) and mins(b["start_time"]) < mins(a["end_time"])


def check_rows(errors, name, fx):
    """Schema, unique ids, reference week, times, and no grader words in what the agent will see."""
    for e in fx["events"]:
        if missing := EVENT_FIELDS - set(e):
            errors.append(f"{name}: event {e.get('event_id')} missing {sorted(missing)}")
            continue
        if not WEEK[0] <= e["date"] <= WEEK[1]:
            errors.append(f"{name}: event {e['event_id']} date {e['date']} outside the reference week")
        if mins(e["end_time"]) <= mins(e["start_time"]):
            errors.append(f"{name}: event {e['event_id']} ends before it starts")
    for c in fx["clubs"]:
        if missing := CLUB_FIELDS - set(c):
            errors.append(f"{name}: club {c.get('club_id')} missing {sorted(missing)}")
    ids = [key_of("event", e) for e in fx["events"]] + [key_of("club", c) for c in fx["clubs"]]
    if len(ids) != len(set(ids)):
        errors.append(f"{name}: duplicate activity ids")
    visible = json.dumps([fx["events"], fx["clubs"]]).lower()
    for w in LEAK_WORDS:
        if w in visible:
            errors.append(f"{name}: grader word {w!r} in agent-visible activity text")


def relevant_ids(key):
    """All relevant activity ids (multiple-schedule splits relevant by requested interest)."""
    r = key["relevant"]
    return [i for ids in r.values() for i in ids] if isinstance(r, dict) else list(r)


def correct_set(task_id, key, profile, events, clubs):
    """
    What the answer key + the profile's free time allow: relevant, not a dislike, breaks nothing, and fits
    (events). Task setup narrows it: overlap_set, restrictions, the runtime update's busy time.
    """
    ev = profile["eval_only"]
    windows, extra = ev["available_windows"], []
    if task_id == "adapt-schedule":  # the replacement must fit after the update
        for u in profile["runtime_updates"]:
            if task_id in u["for_tasks"]:
                extra += ev["runtime_update_effects"][u["id"]].get("adds_busy", [])
    ok = []
    for k in relevant_ids(key):
        if k in key["dislike"] or k in key["breaks"]:
            continue
        if k.startswith("club:"):
            if task_id == "match-interest" and k in clubs:  # only task where a club is a full answer
                ok.append(k)
            continue
        e = events.get(k)
        if not e or not fits(e, windows, extra):
            continue
        if "overlap_set" in key and k not in key["overlap_set"]:
            continue
        if "restrictions" in key:
            r = key["restrictions"]
            if e["event_type"] != r["event_type"] or DAYS[date.fromisoformat(e["date"]).weekday()] != r["weekday"]:
                continue
        if k in key.get("superseded_traps", {}):
            continue
        if "starting_calendar" in key and any(k == f"event:{s['id']}" for s in key["starting_calendar"]):
            continue  # the planned activity that now conflicts isn't its own replacement
        ok.append(k)
    return ok


def check_answer_key(errors, name, fx, task, profiles):
    events = {key_of("event", e): e for e in fx["events"]}
    clubs = {key_of("club", c): c for c in fx["clubs"]}
    keys = {p: k for p, k in fx["answer_key"].items() if not p.startswith("_")}
    if set(keys) != set(task["profiles"]):
        errors.append(f"{name}: answer key profiles {sorted(keys)} != task profiles {sorted(task['profiles'])}")
    for pid, key in keys.items():
        for field in ("relevant", "dislike", "breaks", "reference_solution"):
            if field not in key:
                errors.append(f"{name}/{pid}: answer key missing {field}")
                return
        named = set(relevant_ids(key)) | set(key["dislike"]) | set(key["breaks"]) | set(key["reference_solution"])
        if unknown := named - set(events) - set(clubs):
            errors.append(f"{name}/{pid}: answer key names unknown activities {sorted(unknown)}")
            continue
        truth = profiles[pid]["eval_only"]["current_truth"]
        for k, ids in key["breaks"].items():  # every broken constraint must be one this student has
            if unknown := set(ids) - {h["id"] for h in truth["hard_constraints"]}:
                errors.append(f"{name}/{pid}: {k} breaks unknown constraint ids {sorted(unknown)}")
        ok = correct_set(name, key, profiles[pid], events, clubs)
        ref = key["reference_solution"]
        if name == "multiple-schedule":  # two picks: both allowed, no overlap, one per requested interest
            groups = list(key["relevant"].values())
            if len(groups) == 1:  # one-interest profile (P1): two distinct activities for that interest
                groups = groups * 2
            per_interest = len(ref) == 2 and ref[0] != ref[1] and (ref[0] in groups[0] and ref[1] in groups[1] or
                                                                   ref[1] in groups[0] and ref[0] in groups[1])
            if not per_interest or not set(ref) <= set(ok) or overlap(events[ref[0]], events[ref[1]]):
                errors.append(f"{name}/{pid}: reference pair {ref} not one allowed, non-overlapping event per interest (allowed: {ok})")
        elif name == "resolve-conflicting":  # the top-ranked preference decides among the allowed ones
            if len(ref) != 1 or ref[0] not in ok:
                errors.append(f"{name}/{pid}: reference {ref} not allowed (allowed: {ok})")
        elif name == "adapt-schedule":  # each valid replacement + the planned activities that stay must fit the cap
            if sorted(ok) != sorted(ref):
                errors.append(f"{name}/{pid}: reference_solution {sorted(ref)} != what the key allows {sorted(ok)}")
            extra = [b for u in profiles[pid]["runtime_updates"] if name in u["for_tasks"]
                     for b in profiles[pid]["eval_only"]["runtime_update_effects"][u["id"]].get("adds_busy", [])]
            stays = [s for s in key["starting_calendar"] if fits(events[f"event:{s['id']}"], profiles[pid]["eval_only"]["available_windows"], extra)]
            cap = next((h["value"] for h in truth["hard_constraints"] if h["type"] == "max_activities_per_week"), None)
            if cap is not None and len(stays) + 1 > cap:
                errors.append(f"{name}/{pid}: {len(stays)} kept planned activities + 1 replacement is over the cap {cap}")
        elif sorted(ok) != sorted(ref):
            errors.append(f"{name}/{pid}: reference_solution {sorted(ref)} != what the key allows {sorted(ok)}")


def build(name, fx):
    """Write data/fixtures/<task>.db with the scraper's schema (activities only, no answer key)."""
    path = OUT / f"{name}.db"
    path.unlink(missing_ok=True)  # always rebuilt from the json, never edited by hand
    events = [dict(e, time_text=f"{e['start_time']} - {e['end_time']}", recurring=0, calendar="fixture",
                   url=f"https://fixtures.ecagent.test/{name}/events/{e['event_id']}", scraped_at="fixture")
              for e in fx["events"]]
    clubs = [dict(c, group_type="Student Organization", website=f"https://fixtures.ecagent.test/{name}/clubs/{c['club_id']}",
                  benefits=None, scraped_at="fixture") for c in fx["clubs"]]
    db.save_events(events, path)
    db.save_clubs(clubs, path)
    return path


def main():
    tasks = json.loads(TASKS.read_text())
    profiles = json.loads(PROFILES.read_text())
    fixtures, errors = {}, []
    for task_id, task in tasks.items():
        f = FIXTURES / f"{task_id}.json"
        if not f.exists():
            errors.append(f"{task_id}: no fixture ({f.relative_to(ROOT)})")
            continue
        fx = json.loads(f.read_text())
        check_rows(errors, task_id, fx)
        check_answer_key(errors, task_id, fx, task, profiles)
        fixtures[task_id] = fx
    if errors:
        print("\n".join(f"  FAIL {e}" for e in errors))
        sys.exit(f"{len(errors)} fixture problems, nothing built")
    for task_id, fx in fixtures.items():
        path = build(task_id, fx)
        print(f"  {task_id:20} {len(fx['events']):3} events {len(fx['clubs']):2} clubs -> {path.relative_to(ROOT)}")
    print(f"\nbuilt {len(fixtures)} fixture databases")


if __name__ == "__main__":
    main()
