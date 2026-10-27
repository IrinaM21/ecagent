"""Validate eval/simulated-profiles.json against the schema in docs/benchmark.md.

Runs on its own (standard library only, does not import generate_profiles.py), so it also
catches hand edits to the JSON. Ground truth is checked by recomputing busy_blocks and
available_windows from each profile's current_truth with an independent implementation.

Usage:
    python eval/validate_profiles.py [--profiles PATH] [--tasks PATH]
Exit code 0 if every check passes, 1 otherwise.
"""
import argparse
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
TOP_KEYS = {"id", "schema_version", "label", "description", "agent_visible", "runtime_updates", "eval_only"}
STATE_KEYS = {"student", "interests", "goals", "dislikes", "hard_constraints", "soft_preferences", "schedule"}
EVAL_KEYS = {"tests", "applicable_tasks", "current_truth", "busy_blocks", "available_windows",
             "grading_checks", "superseded_facts", "known_good_activities", "known_good_activities_status"}
HARD_TYPES = {"earliest_start", "latest_end", "unavailable", "buffer_minutes", "max_activities_per_week",
              "max_hours_per_week", "max_event_duration_hours", "max_cost_usd", "exclude", "format", "location"}
# constraint types enforced through busy_blocks / available_windows rather than a C-<id> check
TIME_TYPES = {"earliest_start", "latest_end", "unavailable", "buffer_minutes"}
LEAK_WORDS = ["eval_only", "ground truth", "grading", "superseded", "busy_block", "known_good", "rubric"]
MIN_WINDOW = 30  # minutes; shorter gaps are not listed as available windows


class Checker:
    def __init__(self):
        self.errors = []
        self.count = 0

    def __call__(self, ok, pid, msg):
        self.count += 1
        if not ok:
            self.errors.append(f"{pid}: {msg}")
        return ok


def mins(t):
    return int(t[:2]) * 60 + int(t[3:])


def hhmm(x):
    return f"{x // 60:02d}:{x % 60:02d}"


def week_dates(schedule):
    start = date.fromisoformat(schedule["reference_week"]["start"])
    return [start + timedelta(days=i) for i in range(7)]


def applies(item, d):
    """Does a recurring item / constraint apply on date d?"""
    if "date" in item:
        return item["date"] == d.isoformat()
    if DAYS[d.weekday()] not in item.get("days", []):
        return False
    return item.get("valid_from", "0000") <= d.isoformat() <= item.get("valid_until", "9999")


def expected_busy(truth):
    sched = truth["schedule"]
    sources = sched["recurring"] + sched["one_off"] + \
        [c for c in truth["hard_constraints"] if c["type"] == "unavailable"]
    out = [{"date": d.isoformat(), "start": s["start"], "end": s["end"], "source": s["id"]}
           for d in week_dates(sched) for s in sources if applies(s, d)]
    return sorted(out, key=lambda b: (b["date"], b["start"]))


def expected_windows(truth, busy):
    hard = {c["type"]: c for c in truth["hard_constraints"]}
    lo, hi = mins(hard["earliest_start"]["time"]), mins(hard["latest_end"]["time"])
    buf = hard.get("buffer_minutes", {}).get("minutes", 0)
    out = []
    for d in week_dates(truth["schedule"]):
        ds = d.isoformat()
        merged = []  # busy intervals for the day, padded by the buffer and merged
        for s, e in sorted((mins(b["start"]) - buf, mins(b["end"]) + buf) for b in busy if b["date"] == ds):
            if merged and s <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])
        free_start = lo
        for s, e in merged + [[hi, hi]]:
            s, e = max(s, lo), max(e, lo)
            if min(s, hi) - free_start >= MIN_WINDOW:
                out.append({"date": ds, "start": hhmm(free_start), "end": hhmm(min(s, hi)),
                            "minutes": min(s, hi) - free_start})
            free_start = max(free_start, e)
    return out


def check_state(ck, pid, where, s):
    if not ck(set(s) == STATE_KEYS, pid, f"{where}: keys {sorted(s)} != {sorted(STATE_KEYS)}"):
        return
    for k in ("year", "major"):
        ck(bool(s["student"].get(k)), pid, f"{where}.student.{k} missing")
    sched = s["schedule"]
    ck(sched.get("timezone") == "America/Chicago", pid, f"{where}: timezone must be America/Chicago")
    ws = date.fromisoformat(sched["reference_week"]["start"])
    ck(ws.weekday() == 0, pid, f"{where}: reference_week.start must be a Monday")
    ck(sched["reference_week"]["end"] == (ws + timedelta(days=6)).isoformat(), pid,
       f"{where}: reference_week must span 7 days")
    week = {d.isoformat() for d in week_dates(sched)}

    ranks = [p["rank"] for p in s["soft_preferences"]]
    ck(ranks == list(range(1, len(ranks) + 1)), pid, f"{where}: soft ranks not 1..n: {ranks}")
    types = [c["type"] for c in s["hard_constraints"]]
    ck(set(types) <= HARD_TYPES, pid, f"{where}: unknown hard constraint type {set(types) - HARD_TYPES}")
    ck(types.count("earliest_start") == 1, pid, f"{where}: needs exactly one earliest_start")
    ck(not set(s["interests"]) & set(s["dislikes"]), pid, f"{where}: interest also listed as dislike")

    items = sched["recurring"] + sched["one_off"] + s["hard_constraints"] + s["soft_preferences"]
    ids = [i["id"] for i in items]
    ck(len(ids) == len(set(ids)), pid, f"{where}: duplicate ids")
    for item in items:
        for k in ("start", "end", "time"):
            if k in item:
                ck(bool(TIME.match(item[k])), pid, f"{where}: bad 24h time {item[k]!r} in {item['id']}")
        if "start" in item and "end" in item:
            ck(item["start"] < item["end"], pid, f"{where}: start >= end in {item['id']}")
        if "date" in item:
            ck(bool(DATE.match(item["date"])) and item["date"] in week, pid,
               f"{where}: {item['id']} date {item['date']} outside reference week")
        if "days" in item:
            ck(set(item["days"]) <= set(DAYS), pid, f"{where}: bad days in {item['id']}")
    rec = sched["recurring"]
    for i, x in enumerate(rec):
        for y in rec[i + 1:]:
            clash = set(x["days"]) & set(y["days"]) and x["start"] < y["end"] and y["start"] < x["end"]
            ck(not clash, pid, f"{where}: recurring overlap {x['id']} / {y['id']}")


def check_history(ck, pid, h, ev):
    for i, m in enumerate(h):
        ck(set(m) == {"role", "content"} and isinstance(m["content"], str) and m["content"].strip(),
           pid, f"msg {i} malformed")
        ck(m["role"] == ("user" if i % 2 == 0 else "assistant"), pid, f"msg {i} breaks user/assistant alternation")
    ck(h[-1]["role"] == "assistant", pid, "history must end with assistant so a task query can be appended")

    def is_user_msg(i):
        return isinstance(i, int) and 0 <= i < len(h) and h[i]["role"] == "user"

    for f in ev["superseded_facts"]:
        ok = is_user_msg(f["stated_in_msg"]) and is_user_msg(f["superseded_in_msg"])
        ck(ok and f["stated_in_msg"] < f["superseded_in_msg"], pid,
           f"superseded fact {f['field']}: needs user msgs with stated < superseded")
    buried = [f["msg"] for f in ev.get("buried_facts", [])]
    for i in buried:
        ck(is_user_msg(i), pid, f"buried fact msg {i} is not a user message")
    key_msgs = set(buried) | {f[k] for f in ev["superseded_facts"] for k in ("stated_in_msg", "superseded_in_msg")}
    irrelevant = set(ev.get("irrelevant_msgs", []))
    ck(not key_msgs & irrelevant, pid, f"msgs {sorted(key_msgs & irrelevant)} are both key facts and irrelevant")
    ck(all(0 <= i < len(h) for i in irrelevant), pid, "irrelevant_msgs index out of range")

    if "message_dates" in ev:  # ranges must tile 0..len-1 in order, with non-decreasing dates
        spans = sorted((int(k.split("-")[0]), int(k.split("-")[1]), v) for k, v in ev["message_dates"].items())
        nxt, last = 0, ""
        for a, b, d in spans:
            ck(a == nxt and b >= a and d >= last, pid, f"message_dates range {a}-{b} misaligned")
            nxt, last = b + 1, d
        ck(nxt == len(h), pid, "message_dates do not cover the whole history")


def check_profile(ck, pid, p, tasks):
    if not ck(set(p) == TOP_KEYS, pid, f"top-level keys {sorted(p)} != {sorted(TOP_KEYS)}"):
        return
    ck(p["id"] == pid, pid, "id does not match key")
    av, ev, updates = p["agent_visible"], p["eval_only"], p["runtime_updates"]
    if not ck(set(av) == {"intake_profile", "conversation_history"}, pid, f"agent_visible keys {sorted(av)}"):
        return
    if not ck(EVAL_KEYS <= set(ev), pid, f"eval_only missing {EVAL_KEYS - set(ev)}"):
        return
    intake, truth = av["intake_profile"], ev["current_truth"]
    check_state(ck, pid, "intake_profile", intake)
    check_state(ck, pid, "current_truth", truth)
    check_history(ck, pid, av["conversation_history"], ev)

    # ground truth consistency
    busy = expected_busy(truth)
    ck(ev["busy_blocks"] == busy, pid, "busy_blocks do not match current_truth (regenerate)")
    ck(ev["available_windows"] == expected_windows(truth, busy), pid,
       "available_windows do not match current_truth (regenerate)")
    if not ev["superseded_facts"]:
        ck(intake == truth, pid, "intake_profile differs from current_truth but no superseded_facts explain why")
    else:
        ck(intake != truth, pid, "superseded_facts listed but current_truth equals intake_profile")
    check_ids = {c["id"] for c in ev["grading_checks"]}
    for c in truth["hard_constraints"]:
        if c["type"] not in TIME_TYPES:
            ck(f"C-{c['id']}" in check_ids, pid, f"hard constraint {c['id']} has no grading check C-{c['id']}")
    for g in ev["grading_checks"]:
        ck(g.get("kind") in {"hard", "soft", "behavior"}, pid, f"grading check {g.get('id')} has bad kind")
    for a in ev["known_good_activities"]:  # never invented: each must cite the DB or a fetched URL
        ck(any(a.get(k) for k in ("event_id", "club_id", "url")), pid, f"known_good_activity without a source: {a}")

    # tasks and runtime updates
    ck(set(ev["applicable_tasks"]) <= tasks, pid, f"unknown task ids {set(ev['applicable_tasks']) - tasks}")
    effects = ev.get("runtime_update_effects", {})
    ck({u["id"] for u in updates} == set(effects), pid, "runtime_updates ids != runtime_update_effects keys")
    for u in updates:
        ck(u["message"].get("role") == "user" and u["message"].get("content"), pid, f"update {u['id']} message malformed")
        ck(set(u["for_tasks"]) <= set(ev["applicable_tasks"]), pid, f"update {u['id']} targets a non-applicable task")

    # nothing evaluator-only may be visible to the agent
    visible = json.dumps([av, [u["message"] for u in updates]]).lower()
    for w in LEAK_WORDS:
        ck(w not in visible, pid, f"possible ground-truth leak: {w!r} in agent-visible content")
    for g in ev["grading_checks"]:
        ck(g["check"].lower() not in visible, pid, f"grading check {g['id']} text appears in agent-visible content")


def main():
    ap = argparse.ArgumentParser(description="Validate simulated student profiles.")
    ap.add_argument("--profiles", type=Path, default=HERE / "simulated-profiles.json")
    ap.add_argument("--tasks", type=Path, default=HERE / "benchmark-tasks.json")
    args = ap.parse_args()

    try:
        data = json.loads(args.profiles.read_text())
        tasks = set(json.loads(args.tasks.read_text()))
    except (OSError, json.JSONDecodeError) as e:
        sys.exit(f"could not load input: {e}")

    ck = Checker()
    if ck(isinstance(data, dict) and data, "<file>", "top level must be a non-empty object keyed by profile id"):
        for pid, p in data.items():
            check_profile(ck, pid, p, tasks)
            h = p.get("agent_visible", {}).get("conversation_history", [])
            chars = sum(len(m.get("content", "")) for m in h)
            print(f"  {pid:16} msgs={len(h):2}  est_tokens~{chars // 4:5} (chars/4)  "
                  f"updates={len(p.get('runtime_updates', []))}")
            if "compaction" in " ".join(p.get("eval_only", {}).get("tests", [])):
                ck(chars // 4 >= 2 * 1024, pid, "long-context history should be at least ~2x the 1024-token memory limit")

    print(f"\n{ck.count - len(ck.errors)}/{ck.count} checks passed")
    for e in ck.errors:
        print("  FAIL", e)
    sys.exit(1 if ck.errors else 0)


if __name__ == "__main__":
    main()
