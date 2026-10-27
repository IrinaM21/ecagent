"""Generate eval/simulated-profiles.json, the five simulated student profiles.

This file is the source of truth for the profiles: edit the data here, regenerate, then
run validate_profiles.py. busy_blocks and available_windows in eval_only are *derived*
from each profile's current_truth schedule + hard constraints, and message indices are
looked up from message text, so they cannot drift out of sync. Output is deterministic
(no timestamps or randomness), so regenerating unchanged data is byte-identical.

Usage (standard library only):
    python eval/generate_profiles.py --check     # does the JSON file match this script?
    python eval/generate_profiles.py --force     # overwrite eval/simulated-profiles.json
    python eval/generate_profiles.py -o out.json # write somewhere else (new file)
See docs/benchmark.md for the schema.
"""
import argparse
import copy
import json
import sys
from datetime import date, timedelta
from pathlib import Path

DEFAULT_OUT = Path(__file__).resolve().parent / "simulated-profiles.json"

TZ = "America/Chicago"
WEEK_START = date(2026, 11, 2)  # Monday; DST ended 2026-11-01, so the whole week is CST
WEEK = [WEEK_START + timedelta(days=i) for i in range(7)]
DAY = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
TASKS = ["match-interest", "one-schedule", "schedule-eca", "multiple-schedule",
         "adapt-schedule", "resolve-conflicting", "multi-step"]


def u(text):
    return {"role": "user", "content": text}


def a(text):
    return {"role": "assistant", "content": text}


def sched(recurring, one_off):
    return {"timezone": TZ,
            "reference_week": {"start": WEEK[0].isoformat(), "end": WEEK[-1].isoformat()},
            "recurring": recurring, "one_off": one_off}


def rec(id, title, type, days, start, end, location=None):
    r = {"id": id, "title": title, "type": type, "days": days, "start": start, "end": end}
    if location:
        r["location"] = location
    return r


def one(id, title, type, d, start, end, location=None):
    r = {"id": id, "title": title, "type": type, "date": d, "start": start, "end": end}
    if location:
        r["location"] = location
    return r


# ---------------------------------------------------------------- derivation
def m(t):
    h, mm = t.split(":")
    return int(h) * 60 + int(mm)


def hm(x):
    return f"{x // 60:02d}:{x % 60:02d}"


def hc(constraints, type):
    return [c for c in constraints if c["type"] == type]


def derive(truth):
    """Expand the reference week into busy blocks + available windows."""
    s, hard = truth["schedule"], truth["hard_constraints"]
    earliest = m(hc(hard, "earliest_start")[0]["time"])
    latest = m(hc(hard, "latest_end")[0]["time"])
    buf = hc(hard, "buffer_minutes")[0]["minutes"] if hc(hard, "buffer_minutes") else 0
    busy = []
    for d, dn in zip(WEEK, DAY):
        ds = d.isoformat()
        for r in s["recurring"]:
            if dn in r["days"]:
                busy.append({"date": ds, "start": r["start"], "end": r["end"], "source": r["id"]})
        for o in s["one_off"]:
            if o["date"] == ds:
                busy.append({"date": ds, "start": o["start"], "end": o["end"], "source": o["id"]})
        for c in hc(hard, "unavailable"):
            if ("days" in c and dn in c["days"]) or c.get("date") == ds:
                busy.append({"date": ds, "start": c["start"], "end": c["end"], "source": c["id"]})
    busy.sort(key=lambda b: (b["date"], b["start"]))

    windows = []
    for d in WEEK:
        ds = d.isoformat()
        blocks = sorted((max(0, m(b["start"]) - buf), min(24 * 60, m(b["end"]) + buf))
                        for b in busy if b["date"] == ds)
        cur = earliest
        for bs, be in blocks + [(latest, latest)]:
            bs = min(bs, latest)
            if bs - cur >= 30:  # ignore gaps shorter than 30 min
                windows.append({"date": ds, "start": hm(cur), "end": hm(bs),
                                "minutes": bs - cur})
            cur = max(cur, be)
            if cur >= latest:
                break
    return busy, windows


# ================================================================== P1
p1_intake = {
    "student": {"name": "Maya", "year": "first-year", "major": "Journalism",
                "college": "College of Media", "residence": "University residence hall (on campus)"},
    "interests": ["photography"],
    "goals": ["Build a photography portfolio", "Meet other first-years with similar interests"],
    "dislikes": [],
    "hard_constraints": [
        {"id": "H1", "type": "earliest_start", "time": "08:00"},
        {"id": "H2", "type": "latest_end", "time": "22:00"},
    ],
    "soft_preferences": [
        {"rank": 1, "id": "S1", "category": "interest", "preference": "Photography-related activities"},
        {"rank": 2, "id": "S2", "category": "format", "preference": "Beginner-friendly; no prior experience or portfolio required"},
        {"rank": 3, "id": "S3", "category": "format", "preference": "Open to both one-time events and recurring clubs"},
    ],
    "schedule": sched(
        [rec("R1", "RHET 105", "class", ["Mon", "Wed", "Fri"], "09:00", "09:50"),
         rec("R2", "JOUR 200", "class", ["Tue", "Thu"], "11:00", "12:15"),
         rec("R3", "PSYC 100", "class", ["Mon", "Wed", "Fri"], "13:00", "13:50"),
         rec("R4", "MACS 100", "class", ["Tue", "Thu"], "14:00", "15:15")],
        [one("O1", "Family visiting campus", "personal", "2026-11-07", "10:00", "16:00")]),
}
p1_history = [
    u("Hi! I'm Maya, a first-year journalism major. I'm really into photography and I want to "
      "find something photography-related to get involved in on campus."),
    a("Nice to meet you, Maya! I can help you find photography-related activities. What does "
      "your weekly schedule look like, and are there any times you can't do things?"),
    u("I have RHET 105 Monday/Wednesday/Friday 9:00-9:50 and PSYC 100 MWF 13:00-13:50. Tuesday "
      "and Thursday I have JOUR 200 from 11:00 to 12:15 and MACS 100 from 14:00 to 15:15. I don't "
      "have a job. I'd rather not do anything before 8 or after 10 at night. Also my family is "
      "visiting Saturday November 7 from 10:00 to 16:00."),
    a("Got it. Classes MWF 09:00-09:50 and 13:00-13:50, TR 11:00-12:15 and 14:00-15:15, nothing "
      "before 08:00 or after 22:00, and you're busy Saturday, November 7 from 10:00 to 16:00. Any "
      "other preferences?"),
    u("I'm a total beginner so it should be beginner-friendly. One-time events or a club are "
      "both fine with me."),
    a("Sounds good. I'll look for beginner-friendly photography activities, either one-time "
      "events or clubs, that fit around your classes."),
]

# ================================================================== P2
p2_intake = {
    "student": {"name": "Daniel", "year": "junior", "major": "Computer Engineering",
                "college": "Grainger College of Engineering", "residence": "Off-campus apartment; commutes by bus"},
    "interests": ["robotics", "embedded systems", "entrepreneurship", "pickup basketball"],
    "goals": ["Get hands-on hardware/robotics experience for internship applications",
              "Keep total extracurricular time small during a heavy semester"],
    "dislikes": [],
    "hard_constraints": [
        {"id": "H1", "type": "earliest_start", "time": "08:00"},
        {"id": "H2", "type": "latest_end", "time": "21:30"},
        {"id": "H3", "type": "buffer_minutes", "minutes": 15,
         "note": "Bus commute; needs 15 minutes before and after every commitment"},
        {"id": "H4", "type": "max_activities_per_week", "value": 2},
        {"id": "H5", "type": "max_hours_per_week", "value": 3},
    ],
    "soft_preferences": [
        {"rank": 1, "id": "S1", "category": "interest", "preference": "Robotics or embedded systems"},
        {"rank": 2, "id": "S2", "category": "goal", "preference": "Career / resume value"},
        {"rank": 3, "id": "S3", "category": "location", "preference": "On or near the Engineering Quad"},
        {"rank": 4, "id": "S4", "category": "time", "preference": "Keep Sundays free"},
        {"rank": 5, "id": "S5", "category": "interest", "preference": "Casual social activity such as pickup basketball"},
    ],
    "schedule": sched(
        [rec("R1", "ECE 391 lecture", "class", ["Tue", "Thu"], "09:30", "10:45", "Engineering Quad"),
         rec("R2", "ECE 310", "class", ["Mon", "Wed", "Fri"], "10:00", "10:50", "Engineering Quad"),
         rec("R3", "MATH 415", "class", ["Mon", "Wed", "Fri"], "12:00", "12:50"),
         rec("R4", "ECE 385 lab", "lab", ["Mon", "Wed"], "14:00", "16:50", "Engineering Quad"),
         rec("R5", "ECE 391 discussion", "discussion", ["Fri"], "14:00", "14:50"),
         rec("R6", "Library student assistant shift", "work", ["Tue", "Thu"], "13:00", "17:00", "Grainger Engineering Library"),
         rec("R7", "Library student assistant shift", "work", ["Sat"], "09:00", "13:00", "Grainger Engineering Library"),
         rec("R8", "ECE 120 office hours (course staff)", "work", ["Wed"], "18:00", "20:00")],
        [one("O1", "ECE 391 midterm exam", "exam", "2026-11-03", "19:00", "21:00"),
         one("O2", "Virtual internship interview", "personal", "2026-11-05", "08:00", "09:00"),
         one("O3", "ECE 385 project group meeting", "academic", "2026-11-06", "17:00", "19:00")]),
}
p2_history = [
    u("Hey, I'm Daniel, a junior in computer engineering. My schedule is packed but I'd like to "
      "do something with robotics or embedded systems. I'm also into entrepreneurship, and I "
      "play pickup basketball sometimes."),
    a("Happy to help, Daniel. Since your schedule is tight, can you walk me through your classes, "
      "work, and any limits on how much time you can spend?"),
    u("Classes: ECE 310 MWF 10:00-10:50, MATH 415 MWF 12:00-12:50, ECE 391 lecture Tue/Thu "
      "9:30-10:45, ECE 391 discussion Friday 14:00-14:50, and ECE 385 lab Mon/Wed 14:00-16:50. "
      "I work at the Grainger library Tue/Thu 13:00-17:00 and Saturdays 9:00-13:00, and I hold "
      "ECE 120 office hours Wednesdays 18:00-20:00."),
    a("Thanks. That's a dense week. Are there hard limits I should respect, like earliest or "
      "latest times, or a cap on the number of activities?"),
    u("I live off campus and take the bus, so I need 15 minutes before and after anything. "
      "Nothing before 8:00, and I have to be done by 21:30. Max 2 activities a week and no more "
      "than 3 hours total. This week I also have my ECE 391 midterm Tuesday Nov 3 from 19:00 to "
      "21:00, a virtual interview Thursday Nov 5 from 8:00 to 9:00, and an ECE 385 group meeting "
      "Friday Nov 6 from 17:00 to 19:00."),
    a("Understood: 15-minute buffers, nothing before 08:00 or ending after 21:30, at most 2 "
      "activities and 3 hours per week, plus the midterm, interview, and group meeting. How "
      "should I prioritize your interests?"),
    u("Robotics or embedded first, then anything that helps my resume. Engineering Quad is best "
      "location-wise. I'd like to keep Sundays free if possible, and basketball is last priority."),
    a("Got it. I'll prioritize robotics or embedded systems, then career-relevant options, prefer "
      "the Engineering Quad, try to keep Sundays free, and only fill leftover time with something "
      "social."),
]

# ================================================================== P3
p3_intake = {
    "student": {"name": "Leah", "year": "sophomore", "major": "Molecular and Cellular Biology (pre-med)",
                "college": "College of Liberal Arts and Sciences", "residence": "Apartment near South Quad; walks or takes the bus, no car"},
    "interests": ["healthcare volunteering", "contemporary dance", "environmental sustainability"],
    "goals": ["Gain clinical/healthcare volunteering hours for medical school applications",
              "Have one low-pressure creative outlet"],
    "dislikes": ["competitive sports", "Greek life recruitment events", "career fairs and networking-heavy events",
                 "large lecture-style talks"],
    "hard_constraints": [
        {"id": "H1", "type": "earliest_start", "time": "09:00"},
        {"id": "H2", "type": "latest_end", "time": "21:00",
         "note": "Does not want to walk home alone after 21:00"},
        {"id": "H3", "type": "unavailable", "days": ["Fri"], "start": "16:30", "end": "23:59",
         "note": "Observes Shabbat from Friday before sundown"},
        {"id": "H4", "type": "unavailable", "days": ["Sat"], "start": "00:00", "end": "18:00",
         "note": "Observes Shabbat until Saturday evening"},
        {"id": "H5", "type": "exclude", "rule": "No events centered on alcohol (bar crawls, tailgates, parties with drinking)"},
        {"id": "H6", "type": "max_cost_usd", "value": 25, "per": "semester",
         "note": "Membership dues or fees no more than $25 per semester"},
        {"id": "H7", "type": "format", "allowed": ["in-person"]},
        {"id": "H8", "type": "location", "rule": "Reachable by walking or campus bus; no car required"},
        {"id": "H9", "type": "max_activities_per_week", "value": 3},
    ],
    "soft_preferences": [
        {"rank": 1, "id": "S1", "category": "interest", "preference": "Clinical or healthcare-related volunteering"},
        {"rank": 2, "id": "S2", "category": "social", "preference": "Small groups (roughly under 30 people)"},
        {"rank": 3, "id": "S3", "category": "time", "preference": "Weekday afternoons, 15:00-18:00"},
        {"rank": 4, "id": "S4", "category": "location", "preference": "Near South Quad"},
        {"rank": 5, "id": "S5", "category": "interest", "preference": "Contemporary dance (casual, non-competitive)"},
        {"rank": 6, "id": "S6", "category": "interest", "preference": "Environmental sustainability"},
    ],
    "schedule": sched(
        [rec("R1", "CHEM 232", "class", ["Mon", "Wed", "Fri"], "10:00", "10:50"),
         rec("R2", "MCB 250", "class", ["Tue", "Thu"], "11:00", "12:15"),
         rec("R3", "SPAN 228", "class", ["Mon", "Wed", "Fri"], "13:00", "13:50"),
         rec("R4", "CHEM 233 lab", "lab", ["Thu"], "13:30", "16:20"),
         rec("R5", "Undergraduate research lab hours", "work", ["Mon"], "15:00", "17:00")],
        [one("O1", "Doctor's appointment", "personal", "2026-11-04", "16:00", "17:00"),
         one("O2", "MCAT practice diagnostic exam", "academic", "2026-11-08", "09:00", "14:00")]),
}
p3_history = [
    u("Hi, I'm Leah, a sophomore in MCB on the pre-med track. I want to find extracurriculars, "
      "but I'm pretty particular, so bear with me."),
    a("No problem, Leah. Specific preferences make it easier to find a good fit. What are you "
      "interested in, and is there anything you definitely don't want?"),
    u("I'm interested in healthcare volunteering, contemporary dance (just for fun, not "
      "competitive), and sustainability stuff. I don't want competitive sports, Greek life "
      "recruitment, career fairs or networking-type events, or big lecture-style talks."),
    a("Noted. Are there any hard rules I need to follow when suggesting things?"),
    u("Yes. I keep Shabbat, so nothing from Friday 16:30 until Saturday 18:00. Nothing before "
      "9:00 and nothing that ends after 21:00, because I don't want to walk home alone late. No "
      "events centered on alcohol. I can't pay more than $25 a semester in dues. It has to be in "
      "person and reachable by walking or bus since I don't have a car. And no more than 3 "
      "activities a week."),
    a("Got it, and I'll treat all of those as non-negotiable. What does your class and work "
      "schedule look like?"),
    u("CHEM 232 MWF 10:00-10:50, SPAN 228 MWF 13:00-13:50, MCB 250 Tue/Thu 11:00-12:15, CHEM 233 "
      "lab Thursday 13:30-16:20, and research lab hours Monday 15:00-17:00. This week I also have a "
      "doctor's appointment Wednesday Nov 4 16:00-17:00 and an MCAT practice test Sunday Nov 8 "
      "9:00-14:00."),
    a("Thanks. If several options fit, how should I decide between them?"),
    u("Here's my ranking: 1) clinical or healthcare volunteering matters most, 2) small groups, "
      "under about 30 people, 3) weekday afternoons around 15:00-18:00, 4) close to South Quad, "
      "5) dance, 6) sustainability. If something is high on the list I'll take it even if it "
      "misses something lower."),
    a("Clear. I'll apply your hard rules first, then rank options by that order: healthcare "
      "volunteering, small groups, weekday afternoons, near South Quad, dance, then "
      "sustainability."),
]

# ================================================================== P4
p4_intake = {
    "student": {"name": "Sam", "year": "sophomore", "major": "Statistics",
                "college": "College of Liberal Arts and Sciences", "residence": "Off-campus apartment; goes home to the suburbs some weekends"},
    "interests": ["competitive chess", "investing and personal finance"],
    "goals": ["Join 1-2 activities that stick for the rest of the year"],
    "dislikes": [],
    "hard_constraints": [
        {"id": "H1", "type": "earliest_start", "time": "09:00"},
        {"id": "H2", "type": "latest_end", "time": "22:00"},
        {"id": "H3", "type": "max_activities_per_week", "value": 2},
    ],
    "soft_preferences": [
        {"rank": 1, "id": "S1", "category": "interest", "preference": "Investing / personal finance"},
        {"rank": 2, "id": "S2", "category": "interest", "preference": "Competitive chess"},
        {"rank": 3, "id": "S3", "category": "time", "preference": "Evenings or weekends"},
    ],
    "schedule": sched(
        [rec("R1", "STAT 400", "class", ["Mon", "Wed", "Fri"], "10:00", "10:50"),
         rec("R2", "MATH 241", "class", ["Tue", "Thu"], "09:30", "10:45"),
         rec("R3", "CS 225", "class", ["Tue", "Thu"], "12:30", "13:45"),
         rec("R4", "ECON 102", "class", ["Mon", "Wed", "Fri"], "14:00", "14:50"),
         rec("R5", "Campus Rec front desk shift", "work", ["Tue", "Thu"], "17:00", "21:00")],
        []),
}
p4_truth = copy.deepcopy(p4_intake)
p4_truth["interests"] = ["casual board games", "investing and personal finance"]
p4_truth["dislikes"] = ["competitive chess / tournaments"]
p4_truth["hard_constraints"].append(
    {"id": "H4", "type": "unavailable", "days": ["Sat", "Sun"], "start": "00:00", "end": "23:59",
     "valid_until": "2026-11-30", "note": "Going home every weekend in November"})
p4_truth["soft_preferences"] = [
    {"rank": 1, "id": "S1", "category": "interest", "preference": "Investing / personal finance"},
    {"rank": 2, "id": "S2b", "category": "interest", "preference": "Casual, low-pressure board games"},
    {"rank": 3, "id": "S3b", "category": "time", "preference": "Weekday evenings"},
]
p4_truth["schedule"]["recurring"] = [
    rec("R1", "STAT 400", "class", ["Mon", "Wed", "Fri"], "10:00", "10:50"),
    rec("R2", "MATH 241", "class", ["Tue", "Thu"], "09:30", "10:45"),
    rec("R3", "CS 225", "class", ["Tue", "Thu"], "12:30", "13:45"),
    rec("R5b", "Campus Rec front desk shift", "work", ["Mon", "Wed"], "17:00", "21:00"),
]
p4_history = [
    u("Hi, I'm Sam, a sophomore in statistics. I'm looking for 1-2 activities. I play "
      "competitive chess and I'm really into investing and personal finance."),
    a("Nice to meet you, Sam. What does your week look like?"),
    u("STAT 400 MWF 10:00-10:50, ECON 102 MWF 14:00-14:50, MATH 241 Tue/Thu 9:30-10:45, and CS 225 "
      "Tue/Thu 12:30-13:45. I work the Campus Rec front desk Tuesday and Thursday 17:00-21:00."),
    a("Thanks. Any hard limits on timing or how many activities you want?"),
    u("Nothing before 9:00 and nothing that ends after 22:00. Max 2 activities a week. Evenings "
      "or weekends are best. Investing is my top priority, then chess."),
    a("Got it: nothing before 09:00 or ending after 22:00, at most 2 per week, investing first, "
      "then chess, and evenings or weekends are preferred."),
    u("Update since we last talked: I dropped ECON 102, so my MWF 14:00-14:50 is free now. Also "
      "my front desk shifts moved to Monday and Wednesday 17:00-21:00, starting this week. I don't "
      "work Tuesdays or Thursdays anymore."),
    a("Updated. ECON 102 is removed, and your shifts are now Monday and Wednesday 17:00-21:00 "
      "instead of Tuesday and Thursday."),
    u("Also, honestly I'm over competitive chess. The tournaments stressed me out. I'd rather do "
      "casual board games, nothing competitive. Investing is still number one."),
    a("Understood. I'll drop competitive chess, look for casual board games instead, and keep "
      "investing as your top priority."),
    u("One more thing. I said weekends were fine, but I'm going home every weekend in November, "
      "so no Saturdays or Sundays this month. Weekday evenings work best now."),
    a("Got it. No weekend activities through the end of November, and I'll favor weekday "
      "evenings."),
]
p4_updates = [
    {"id": "U1", "for_tasks": ["adapt-schedule"],
     "message": u("Something came up: there's a STAT 400 midterm review session Thursday Nov 5 "
                  "from 18:00 to 20:00 that I need to go to. Can you redo my plan for this week?")},
    {"id": "U2", "for_tasks": ["resolve-conflicting"],
     "message": u("Change of plans for this weekend only: I'm staying on campus Saturday Nov 7 "
                  "for a friend's birthday dinner at 18:00, so I'm free that Saturday from 12:00 "
                  "to 17:00. I'm still going home Sunday. Can you find me something that "
                  "Saturday afternoon?")},
]

# ================================================================== P5
p5_intake = {
    "student": {"name": "Jordan", "year": "senior", "major": "Information Sciences (minor in Music)",
                "college": "School of Information Sciences", "residence": "Off-campus apartment; no car, relies on the bus"},
    "interests": ["improv comedy", "UX / design"],
    "goals": ["Try something new before graduating in May"],
    "dislikes": [],
    "hard_constraints": [
        {"id": "H1", "type": "earliest_start", "time": "09:00"},
    ],
    "soft_preferences": [
        {"rank": 1, "id": "S1", "category": "interest", "preference": "Improv comedy"},
        {"rank": 2, "id": "S2", "category": "interest", "preference": "UX / design"},
    ],
    "schedule": sched(
        [rec("R1", "IS 445", "class", ["Mon", "Wed"], "09:30", "10:45"),
         rec("R2", "IS 467", "class", ["Tue", "Thu"], "11:00", "12:15"),
         rec("R3", "MUS 313", "class", ["Tue", "Thu"], "13:00", "14:15"),
         rec("R4", "Orchestra rehearsal (viola)", "extracurricular", ["Tue", "Thu"], "19:00", "21:30")],
        []),
}
p5_truth = copy.deepcopy(p5_intake)
p5_truth["interests"] = ["music ensemble / casual group playing (not solo performance)", "UX / design"]
p5_truth["dislikes"] = ["improv comedy", "solo performance"]
p5_truth["hard_constraints"] = [
    {"id": "H1", "type": "earliest_start", "time": "09:00"},
    {"id": "H2", "type": "latest_end", "time": "22:30", "note": "Last bus home around 23:00"},
    {"id": "H3", "type": "location", "rule": "Reachable by bus or walking; no car"},
    {"id": "H4", "type": "max_cost_usd", "value": 15, "per": "event"},
    {"id": "H5", "type": "max_cost_usd", "value": 40, "per": "semester", "note": "Dues"},
    {"id": "H6", "type": "max_activities_per_week", "value": 2, "note": "New activities only"},
    {"id": "H7", "type": "max_hours_per_week", "value": 4},
    {"id": "H8", "type": "max_event_duration_hours", "value": 3, "note": "No overnight events or hackathons"},
]
p5_truth["soft_preferences"] = [
    {"rank": 1, "id": "S1b", "category": "interest", "preference": "Music-related group playing (chamber group, casual jam), not solo"},
    {"rank": 2, "id": "S2", "category": "interest", "preference": "UX / design"},
    {"rank": 3, "id": "S3", "category": "time", "preference": "Weekday evenings over weekends (Saturdays are for capstone work)"},
    {"rank": 4, "id": "S4", "category": "social", "preference": "Small groups"},
    {"rank": 5, "id": "S5", "category": "location", "preference": "Near Main Quad / Illini Union"},
]
p5_truth["schedule"]["recurring"] = p5_intake["schedule"]["recurring"] + [
    rec("R5b", "UX internship (remote, at desk)", "work", ["Wed"], "13:00", "18:00"),
    rec("R6", "UX internship (remote, at desk)", "work", ["Fri"], "09:00", "13:00"),
]
p5_truth["schedule"]["one_off"] = [
    one("O1", "Capstone presentation", "academic", "2026-11-05", "16:00", "17:30"),
    one("O2", "Job interview in Chicago (travel all day)", "personal", "2026-11-06", "06:00", "21:00"),
    one("O3", "Orchestra viola sectional", "extracurricular", "2026-11-08", "14:00", "16:00"),
]
p5_history = [
    u("hey! I'm Jordan, senior in info sciences with a music minor. I graduate in May and I "
      "want to try something new before I leave. I've been thinking about improv comedy, and "
      "maybe something UX/design-related since that's what I want to do after college."),
    a("Hi Jordan! Improv and UX/design are both good directions. To find things that fit, can "
      "you tell me about your weekly schedule?"),
    u("Sure. IS 445 (data viz) Mon/Wed 9:30-10:45, IS 467 Tue/Thu 11:00-12:15, MUS 313 Tue/Thu "
      "13:00-14:15. I also play viola in orchestra, rehearsals are Tue/Thu 19:00-21:30. I don't do "
      "mornings, so nothing before 9."),
    a("Got it: IS 445 MW 09:30-10:45, IS 467 TR 11:00-12:15, MUS 313 TR 13:00-14:15, orchestra "
      "TR 19:00-21:30, and nothing before 09:00. Anything else?"),
    u("Not yet, still figuring it out. Side question: I want to get better at Figma before "
      "applying to UX jobs. Where should I start? I've only used it for basic wireframes."),
    a("If you're already making wireframes, the next steps are the features hiring managers "
      "expect to see in a portfolio. First, learn auto layout well. It lets frames resize like "
      "real interfaces and it's the biggest difference between beginner and intermediate files. "
      "Second, build reusable components with variants, for example a button with default, hover, "
      "and disabled states, and use them across screens. Third, set up text and color styles so "
      "the design is consistent. Fourth, prototype a full flow, like signing up or checking out, "
      "with interactions between screens. Figma's own learning resources and community files are "
      "free and good for this. A practical exercise is to redesign one flow from an app you use "
      "every day. Document the problem, your sketches, the iterations, and the final prototype, "
      "because portfolios are judged on process as much as polish. Once that feels comfortable, "
      "a small design system with tokens, a component library, and one documented pattern is a "
      "strong portfolio piece."),
    u("ok that's a good plan. Anyway, nothing else schedule-wise right now. Random question though: for IS 445, what's the actual difference between a "
      "bar chart and a histogram? My TA marked me down for using the wrong one."),
    a("Good question. They look similar but encode different things. A bar chart compares values "
      "across separate categories, like the number of students per major. The bars are usually "
      "separated by gaps and the order of categories is arbitrary or chosen for readability. A "
      "histogram shows the distribution of one continuous numeric variable, like commute times, "
      "by splitting it into adjacent bins and counting how many observations fall into each. The "
      "bars touch because the bins are contiguous, and the x-axis is a numeric scale with a fixed "
      "order. Rule of thumb: if your x-axis is labels, use a bar chart. If it's ranges of a "
      "number, use a histogram and pick the bin width carefully, because different widths can "
      "change the visible shape of the distribution."),
    u("ok that makes sense, thanks. oh btw, I don't have a car and the bus near my apartment "
      "stops running around 23:00, so I need anything to end by 22:30 at the latest."),
    a("Noted: no car, and anything I suggest has to end by 22:30 so you can catch a bus home."),
    u("Also off topic, but my roommate keeps playing music at like 2am and I have no idea how to "
      "bring it up without it being awkward. Any advice?"),
    a("That's a common roommate problem. A few suggestions: pick a calm moment, not 2am when "
      "you're annoyed. Lead with the effect on you rather than blame, for example 'I've been "
      "having trouble sleeping on weeknights when there's music late.' Propose something concrete, "
      "like headphones after midnight or a quiet-hours window you both agree on, and ask what "
      "would work for them too. If you have a lease or roommate agreement, it can help to write "
      "down what you agree on. If it keeps happening after a real conversation, a landlord or "
      "housing staff member can mediate."),
    u("Thanks, I'll try that. Schedule update: I started a part-time UX internship. It's remote, "
      "but I need to be at my desk Wednesdays 12:00-17:00 and Fridays 9:00-13:00."),
    a("Congrats on the internship! I've added it: Wednesdays 12:00-17:00 and Fridays "
      "09:00-13:00, both unavailable."),
    u("Could you help me tighten a resume bullet? Right now it says 'Worked on a team that made "
      "a dashboard for the library to see how many people use study rooms.'"),
    a("Sure. A stronger version leads with your action, names the tools, and states the impact. "
      "For example: 'Co-designed and built an interactive dashboard (Tableau, SQL) visualizing "
      "study-room usage across library branches, helping staff identify peak hours and adjust "
      "room availability.' If you know numbers, like how many rooms, how many weeks of data, or "
      "a change in wait times, add one. A quantified result is usually the most persuasive part "
      "of a bullet. Keep it to one or two lines and start with a strong verb such as designed, "
      "built, analyzed, or led."),
    u("perfect. ok another constraint: I'm trying to save money, so nothing that costs more than "
      "$15 per event, and I won't pay more than $40 in dues for a semester."),
    a("Got it: at most $15 per event and at most $40 per semester in dues."),
    u("My friend keeps telling me to do a hackathon, but those go all night and I just can't do "
      "that anymore. Honestly anything longer than about 3 hours at once is too much for me "
      "right now, so keep individual activities to 3 hours max."),
    a("Makes sense. I won't suggest overnight events, and I'll keep any single activity to "
      "3 hours or less."),
    u("Can you help me think about how to structure my capstone presentation? It's 15 minutes "
      "plus Q&A, and our project is a redesign of a local nonprofit's volunteer sign-up site."),
    a("For a 15-minute capstone talk about a redesign, a problem-to-impact structure works well. "
      "Spend about 2 minutes on context: who the nonprofit is, who signs up to volunteer, and why "
      "it matters. Spend 3 minutes on the problem, showing evidence such as drop-off points in the "
      "old flow, quotes from user interviews, or usability test results. Spend 2 minutes on your "
      "process: research methods, personas or journey maps, and how many rounds of iteration you "
      "did. The redesign should be the centerpiece at about 5 minutes. Walk through the new flow "
      "as a story about one volunteer, and show a before and after for the most important "
      "screens. Use about 2 minutes for results and impact, such as task completion time, error "
      "rates, or stakeholder feedback, and be honest about limitations. Close in 1 minute with "
      "what you'd do next. For Q&A, prepare backup slides on accessibility, technical "
      "constraints, and why you rejected alternative designs, because those questions come up "
      "often. Rehearse with a timer at least twice."),
    u("Update on improv: I went to a drop-in improv thing last week and honestly hated it. Please "
      "cross improv off the list completely. I'd rather do something music-related where I'm "
      "playing with other people, not performing solo. A chamber group or a casual jam would be "
      "great. UX/design stuff is still on the table."),
    a("Understood. Improv is off the list. I'll prioritize group music activities like chamber "
      "groups or casual jams, with no solo performing, and keep UX/design as your second "
      "interest."),
    u("Unrelated, but is it worth buying a really heavy winter coat or can I get away with "
      "layers? This is my last winter here lol"),
    a("Champaign winters often have stretches well below freezing, and wind across the open "
      "quads makes it feel colder, especially at bus stops. Since you rely on the bus, a warm, "
      "windproof coat is probably worth it, ideally one that covers your hips. Pair it with a hat, "
      "gloves, and warm socks. If you'd rather not buy something expensive for one winter, a "
      "mid-weight insulated jacket with a fleece layer underneath and a windproof shell works "
      "well too. Thrift stores and end-of-season sales are good places to look."),
    u("Do you have tips for practicing viola more efficiently? I only get maybe 30-40 minutes a "
      "day between everything."),
    a("With 30-40 minutes, structure matters more than total time. Try this split. Spend 5 "
      "minutes warming up with slow scales and long tones, focusing on tone and bow distribution "
      "instead of speed. Spend 10 minutes on technique targeted at whatever your current "
      "repertoire needs, like shifting, string crossings, or a specific bowing pattern. Spend "
      "15-20 minutes on repertoire, but don't play pieces top to bottom. Pick the 2-3 hardest "
      "passages and loop them slowly with a metronome, raising the tempo only when you can play "
      "a passage cleanly three times in a row. Use the last few minutes to play through a longer "
      "section for continuity. Record yourself once or twice a week, because it's the fastest "
      "way to hear intonation and rhythm problems you miss while playing. On rehearsal days, "
      "focus your short practice on the orchestra passages the conductor flagged. If you can "
      "split practice into two short sessions, that often helps retention more than one longer "
      "block."),
    u("ok cool. some preferences for activities: I like smaller groups, and I'd much rather do "
      "weekday evenings than weekends because I work on my capstone on Saturdays. Being close to "
      "the Main Quad or the Union is nice but not required."),
    a("Noted: smaller groups, weekday evenings over weekends (Saturdays are for capstone), and "
      "a preference for locations near the Main Quad or Illini Union."),
    u("A few things this week: my capstone presentation is Thursday Nov 5 from 16:00 to 17:30. "
      "And I'll be in Chicago all day Friday Nov 6 for a job interview, gone from about 6:00 until "
      "21:00."),
    a("Good luck with both! I've added the capstone presentation Thursday, November 5, "
      "16:00-17:30, and marked Friday, November 6, 06:00-21:00 as unavailable."),
    u("Oh, my internship changed my Wednesday hours. It's 13:00-18:00 now instead of "
      "12:00-17:00, starting this week. Fridays are the same."),
    a("Updated. Wednesday internship hours are now 13:00-18:00. Friday stays 09:00-13:00."),
    u("Random stats question for my other class: what is a p-value actually? I keep seeing "
      "'probability the null is true' and my professor says that's wrong."),
    a("Your professor is right. A p-value is the probability of seeing data at least as extreme "
      "as what you observed, assuming the null hypothesis is true. It's computed under the null, "
      "so it can't be the probability that the null is true. A small p-value means your data "
      "would be surprising if the null were true, which counts as evidence against it. It doesn't "
      "tell you how big or important an effect is, and 0.05 is a convention, not a law of nature. "
      "Pair p-values with effect sizes and confidence intervals."),
    u("One more for IS 467: what's the difference between a LEFT JOIN and an INNER JOIN? I "
      "always mix them up."),
    a("An INNER JOIN returns only rows that have a match in both tables. If you join students "
      "to enrollments, students with no enrollments disappear from the result. A LEFT JOIN "
      "returns every row from the left table, the one written first, plus matching rows from "
      "the right table. Where there's no match, the right-side columns are NULL, so students "
      "with no enrollments still appear with empty enrollment fields. A quick way to remember: "
      "a LEFT JOIN never loses rows from the left table. A common use is finding missing data. "
      "LEFT JOIN, then filter WHERE right_table.id IS NULL to get students with no enrollments. "
      "Be careful putting conditions on the right table in the WHERE clause after a LEFT JOIN, "
      "because that can quietly turn it back into an inner join. Put those conditions in the ON "
      "clause instead."),
    u("that's helpful. last things: orchestra added a viola sectional this Sunday Nov 8 from "
      "14:00 to 16:00. And I don't want to overcommit. Max 2 new activities a week and no more "
      "than 4 hours total for new stuff."),
    a("Got it. I've added the sectional Sunday, November 8, 14:00-16:00, and I'll keep it to at "
      "most 2 new activities and 4 hours per week."),
    u("thanks!! I'm going home for Thanksgiving later this month so I'm trying to fit stuff in "
      "before then. anyway, that's everything for now."),
    a("Sounds good, Jordan. Enjoy Thanksgiving when it comes. Whenever you're ready, I can "
      "suggest activities for this week that fit everything you've told me."),
]
def idx(history, text):
    """Index of the unique message containing text (keeps eval references stable on edits)."""
    hits = [i for i, msg in enumerate(history) if text in msg["content"]]
    assert len(hits) == 1, (text, hits)
    return hits[0]


i5 = lambda text: idx(p5_history, text)
p5_irrelevant = sorted(i for t in ["bar chart and a histogram", "roommate keeps playing", "Figma before",
                             "tighten a resume bullet", "capstone presentation? It's 15",
                             "heavy winter coat", "practicing viola", "p-value actually",
                             "LEFT JOIN and an INNER", "going home for Thanksgiving"]
                 for i in (i5(t), i5(t) + 1))

p5_updates = [
    {"id": "U1", "for_tasks": ["adapt-schedule"],
     "message": u("Orchestra just cancelled Thursday's rehearsal (Nov 5), so I'm free that evening "
                  "after my capstone presentation. Can you update my plan?")},
    {"id": "U2", "for_tasks": ["resolve-conflicting"],
     "message": u("Is there anything I could do Wednesday from 12:00 to 13:00 this week?")},
]


# ================================================================== assemble
def checks_common(truth):
    return [
        {"id": "C-busy", "kind": "hard", "check": "No suggested activity overlaps any busy_block (after applying buffer_minutes, if any)."},
        {"id": "C-window", "kind": "hard", "check": "Every suggested activity starts at or after earliest_start and ends at or before latest_end."},
        {"id": "C-exist", "kind": "hard", "check": "Every suggested activity exists in the frozen campus.db snapshot or a cited, fetched URL. No fabricated events or clubs."},
    ] + [
        {"id": f"C-{c['id']}", "kind": "hard", "check": f"Respects hard constraint {c['id']} ({c['type']})."}
        for c in truth["hard_constraints"]
        if c["type"] not in ("earliest_start", "latest_end", "unavailable", "buffer_minutes")
    ]


def build(pid, label, description, intake, history, truth, tests, tasks, extra_eval,
          updates=(), msg_dates=None):
    busy, windows = derive(truth)
    ev = {
        "tests": tests,
        "applicable_tasks": tasks,
        "current_truth": truth,
        "busy_blocks": busy,
        "available_windows": windows,
        "grading_checks": checks_common(truth) + extra_eval.pop("checks", []),
        "superseded_facts": extra_eval.pop("superseded_facts", []),
        "known_good_activities": [],
        "known_good_activities_status": "pending: populate from a frozen campus.db snapshot; intentionally empty, no ECAs were invented",
    }
    if msg_dates:
        ev["message_dates"] = msg_dates
    ev.update(extra_eval)
    return pid, {
        "id": pid,
        "schema_version": "1.0",
        "label": label,
        "description": description,
        "agent_visible": {"intake_profile": intake, "conversation_history": history},
        "runtime_updates": list(updates),
        "eval_only": ev,
    }


ALL = list(TASKS)
profiles = dict([
    build("P1-simple", "Simple Student",
          "First-year journalism student interested in photography with a light class schedule.",
          p1_intake, p1_history, copy.deepcopy(p1_intake),
          ["basic ECA retrieval", "interest matching", "personalization with minimal constraints"],
          ["match-interest", "one-schedule", "schedule-eca", "multiple-schedule", "multi-step"],
          {"interest_keywords": ["photo", "photography", "camera", "film"],
           "checks": [
               {"id": "C-interest", "kind": "soft", "check": "At least one suggestion is photography-related (S1)."},
               {"id": "C-beginner", "kind": "soft", "check": "Suggestions do not require prior experience or a portfolio (S2)."},
               {"id": "C-o1", "kind": "hard", "check": "Nothing on 2026-11-07 between 10:00 and 16:00."}]}),
    build("P2-busy", "Busy Student",
          "Junior computer engineering student with a dense class, lab, and work schedule and a bus commute.",
          p2_intake, p2_history, copy.deepcopy(p2_intake),
          ["schedule reasoning over a dense week", "conflict avoidance including buffers and one-off events",
           "respecting count/hour caps", "multi-interest prioritization"],
          ["match-interest", "one-schedule", "schedule-eca", "multiple-schedule", "multi-step"],
          {"interest_keywords": ["robot", "robotics", "embedded", "microcontroller", "hardware", "startup", "entrepreneur", "basketball"],
           "checks": [
               {"id": "C-buffer", "kind": "hard", "check": "15-minute gap between any suggestion and adjacent commitments (H3)."},
               {"id": "C-cap", "kind": "hard", "check": "At most 2 activities and 3 total hours in the reference week (H4, H5)."},
               {"id": "C-exam", "kind": "hard", "check": "Nothing on 2026-11-03 18:45-21:15 (midterm plus buffer)."},
               {"id": "C-rank", "kind": "soft", "check": "If any robotics/embedded option fits, it is suggested before entrepreneurship or basketball (S1 > S2 > S5)."},
               {"id": "C-sunday", "kind": "soft", "check": "Prefers non-Sunday slots when an equivalent option exists (S4)."}]}),
    build("P3-picky", "Picky Student",
          "Sophomore pre-med MCB student with strong dislikes, firm restrictions, and an explicit preference ranking.",
          p3_intake, p3_history, copy.deepcopy(p3_intake),
          ["hard vs. soft constraint separation", "preference ranking / tie-breaking", "exclusion of disliked categories",
           "religious-observance time block"],
          ["match-interest", "one-schedule", "schedule-eca", "multiple-schedule", "multi-step"],
          {"interest_keywords": ["volunteer", "hospital", "clinic", "health", "EMT", "dance", "sustainab", "environment"],
           "avoid_keywords": ["tournament", "intramural", "varsity", "rush", "recruitment", "fraternity", "sorority", "career fair", "networking", "bar", "tailgate"],
           "checks": [
               {"id": "C-shabbat", "kind": "hard", "check": "Nothing Fri 16:30 to Sat 18:00 (H3, H4)."},
               {"id": "C-dislikes", "kind": "hard", "check": "No suggestion falls in a disliked category or matches avoid_keywords."},
               {"id": "C-cost", "kind": "hard", "check": "Dues/fees at most $25/semester, or the agent says cost is unverified (H6). campus.db has no cost field."},
               {"id": "C-rank", "kind": "soft", "check": "A fitting healthcare-volunteering option outranks a dance option even if the dance option better matches S3/S4."},
               {"id": "C-explain", "kind": "behavior", "check": "Agent explains tradeoffs in terms of the stated ranking when it picks a lower-ranked option."}]}),
    build("P4-changing", "Changing Student",
          "Sophomore statistics student whose classes, work shifts, interests, and weekend availability change over the conversation.",
          p4_intake, p4_history, p4_truth,
          ["using the latest information over stale information", "replanning after schedule changes",
           "handling a one-time exception to a standing rule", "preference drift"],
          ALL,
          {"interest_keywords": ["invest", "finance", "stock", "trading", "board game", "tabletop"],
           "avoid_keywords": ["chess tournament", "competitive chess"],
           "superseded_facts": [
               {"field": "schedule.recurring.R4", "old": "ECON 102 MWF 14:00-14:50", "new": "dropped (time is free)", "stated_in_msg": 2, "superseded_in_msg": 6},
               {"field": "schedule.recurring.R5", "old": "Work Tue/Thu 17:00-21:00", "new": "Work Mon/Wed 17:00-21:00", "stated_in_msg": 2, "superseded_in_msg": 6},
               {"field": "interests", "old": "competitive chess", "new": "casual board games; competitive chess now disliked", "stated_in_msg": 0, "superseded_in_msg": 8},
               {"field": "soft_preferences.S3", "old": "evenings or weekends", "new": "weekday evenings; weekends unavailable through 2026-11-30 (hard)", "stated_in_msg": 4, "superseded_in_msg": 10}],
           "checks": [
               {"id": "C-stale-work", "kind": "hard", "check": "Nothing Mon/Wed 17:00-21:00. Tue/Thu 17:00-21:00 may be used."},
               {"id": "C-stale-chess", "kind": "hard", "check": "Does not suggest competitive chess."},
               {"id": "C-weekend", "kind": "hard", "check": "No weekend suggestions in the reference week, except the U2 window when U2 is injected."}],
           "runtime_update_effects": {
               "U1": {"adds_busy": [{"date": "2026-11-05", "start": "18:00", "end": "20:00", "source": "U1"}],
                      "expected": "Re-plan. Any previously suggested Thu 2026-11-05 activity overlapping 18:00-20:00 is moved or dropped. Other suggestions are kept."},
               "U2": {"adds_available": [{"date": "2026-11-07", "start": "12:00", "end": "17:00"}],
                      "expected": "Treat 2026-11-07 12:00-17:00 as a one-time exception to H4. Suggestions end by 17:00. Sunday 2026-11-08 stays unavailable, and H4 still applies on other November weekends."}}},
          updates=p4_updates,
          msg_dates={"0-5": "2026-10-19", "6-11": "2026-10-30"}),
    build("P5-long-context", "Long-Context Student",
          "Senior information sciences student with a long, meandering conversation history.",
          p5_intake, p5_history, p5_truth,
          ["context compaction (history well over the 1024-token memory limit)",
           "recall of facts buried among irrelevant turns", "ignoring superseded preferences and times after compaction"],
          ["match-interest", "one-schedule", "schedule-eca", "multiple-schedule", "adapt-schedule", "resolve-conflicting", "multi-step"],
          {"interest_keywords": ["chamber", "ensemble", "jam", "orchestra", "music", "UX", "design", "HCI"],
           "avoid_keywords": ["improv", "open mic", "solo", "comedy"],
           "superseded_facts": [
               {"field": "interests", "old": "improv comedy", "new": "disliked; replaced by group music",
                "stated_in_msg": 0, "superseded_in_msg": i5("cross improv off")},
               {"field": "schedule.recurring.R5", "old": "Internship Wed 12:00-17:00", "new": "Internship Wed 13:00-18:00",
                "stated_in_msg": i5("I started a part-time UX internship"), "superseded_in_msg": i5("changed my Wednesday hours")}],
           "buried_facts": [
               {"fact": "latest_end 22:30 (bus)", "msg": i5("need anything to end by 22:30")},
               {"fact": "Internship Fri 09:00-13:00", "msg": i5("I started a part-time UX internship")},
               {"fact": "Cost caps $15/event, $40/semester", "msg": i5("more than $15 per event")},
               {"fact": "Single activity at most 3 hours; no overnight events", "msg": i5("3 hours max")},
               {"fact": "Saturdays reserved for capstone (soft)", "msg": i5("capstone on Saturdays")},
               {"fact": "Capstone presentation 2026-11-05 16:00-17:30", "msg": i5("capstone presentation is Thursday")},
               {"fact": "Away 2026-11-06 06:00-21:00", "msg": i5("capstone presentation is Thursday")},
               {"fact": "Sectional 2026-11-08 14:00-16:00", "msg": i5("viola sectional")},
               {"fact": "Max 2 new activities, 4 hours/week", "msg": i5("viola sectional")}],
           "irrelevant_msgs": p5_irrelevant,
           "checks": [
               {"id": "C-improv", "kind": "hard", "check": "Never suggests improv or solo performance."},
               {"id": "C-wed", "kind": "hard", "check": "Nothing Wed 13:00-18:00. Wed 12:00-13:00 is free under current truth."},
               {"id": "C-compact", "kind": "behavior", "check": "After compaction, every buried_fact is still respected and no superseded value is used."}],
           "runtime_update_effects": {
               "U1": {"removes_busy": [{"date": "2026-11-05", "start": "19:00", "end": "21:30", "source": "R4"}],
                      "expected": "May now use Thu 2026-11-05 17:30-22:30. Must still respect the 4-hour/2-activity caps."},
               "U2": {"expected": "Answers using the updated internship hours (Wed 13:00-18:00): 12:00-13:00 is free, after IS 445 ends at 10:45. Must not say the old 12:00-17:00 hours block it. Suggestions must end by 13:00."}}},
          updates=p5_updates,
          msg_dates={f"0-{i5('I started a part-time UX internship') - 1}": "2026-10-05",
                     f"{i5('I started a part-time UX internship')}-{i5('cross improv off') - 1}": "2026-10-14",
                     f"{i5('cross improv off')}-{i5('capstone presentation is Thursday') - 1}": "2026-10-22",
                     f"{i5('capstone presentation is Thursday')}-{len(p5_history) - 1}": "2026-11-01"}),
])

def render():
    return json.dumps(profiles, indent=2, ensure_ascii=False) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("-o", "--output", type=Path, default=DEFAULT_OUT,
                    help=f"output path (default: {DEFAULT_OUT.name} next to this script)")
    ap.add_argument("--force", action="store_true", help="overwrite the output file if it exists")
    ap.add_argument("--check", action="store_true",
                    help="write nothing; exit 1 if the output file differs from what would be generated")
    args = ap.parse_args()
    text = render()

    if args.check:
        if not args.output.exists():
            sys.exit(f"{args.output} does not exist")
        if args.output.read_text() != text:
            sys.exit(f"{args.output} is out of date with generate_profiles.py (regenerate with --force)")
        print(f"{args.output} matches generate_profiles.py")
        return
    if args.output.exists() and not args.force:
        sys.exit(f"{args.output} already exists; pass --force to overwrite (or --check to compare)")
    args.output.write_text(text)
    print(f"wrote {len(profiles)} profiles to {args.output}")


if __name__ == "__main__":
    main()
