# live test of Google Calendar + Gmail on a real account (run scripts.google_login first)
# python -m scripts.live_test [--only calendar|gmail] [--agent --interests photography] [--no-web]
# the calendar check adds a test event tomorrow at 23:00, reads it back, then deletes it

import argparse
import os
import sys
from datetime import date, timedelta

from agent.calendars import GoogleCalendar
from agent.gmail import EmailSource, GmailInbox
from agent.scheduler import Scheduler

results = []


# prints PASS/FAIL and keeps count
def check(name, ok, detail=""):
    results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))


def show(label, items, fields=("date", "start", "end", "title")):
    print(f"  {label}: {len(items)}")
    for it in items[:15]:
        print("    - " + "  ".join(str(it.get(f) or "-") for f in fields))


# add / read back / remove a test event on the real calendar
def test_calendar():
    print("\nCalendar")
    cal = GoogleCalendar()
    today = date.today()
    events = cal.get_events(today.isoformat(), (today + timedelta(days=6)).isoformat())
    check("read the next 7 days", True)
    show("events (busy time for planning)", events, ("date", "start", "end", "title", "agent_added"))
    check("found/created the ECAgent calendar", bool(cal.write_id()), cal.write_id())
    day = (today + timedelta(days=1)).isoformat()
    activity = {"kind": "event", "id": "live-test", "title": "ECAgent live test (safe to delete)", "date": day,
                "start": "23:00", "end": "23:30", "location": "", "description": "Created by scripts/live_test.py",
                "url": None, "source": "test"}
    event = cal.add_event(activity)
    check("add_event", bool(event.get("id")), f"{event['date']} {event['start']}-{event['end']}")
    found = Scheduler.find_added(cal, activity, event["id"])
    check("read back: the event is on the calendar", found is not None)
    check("read back: same time", found is not None and (found["start"], found["end"]) == ("23:00", "23:30"))
    check("read back: marked as agent-added", found is not None and found["agent_added"])
    check("remove_event", cal.remove_event(event["id"]))
    check("read back: the event is gone", Scheduler.find_added(cal, activity, event["id"]) is None)


# search the inbox, then pull out events (needs ANTHROPIC_API_KEY)
def test_gmail():
    print("\nGmail")
    inbox = GmailInbox()
    messages = inbox.fetch()
    check("search the inbox", True, f"{len(messages)} matching messages")
    show("messages", messages, ("sent", "subject"))
    check("calendar invites found", True, f"{sum(len(m['ics']) for m in messages)} .ics attachment(s)")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("  (skipping extraction: set ANTHROPIC_API_KEY)")
        return
    today = date.today()
    out = EmailSource(inbox).scan(today.isoformat(), (today + timedelta(days=6)).isoformat())
    show("activities (recommendable)", out["activities"], ("date", "start", "end", "title", "location"))
    show("commitments (busy time)", out["commitments"], ("date", "start", "end", "title"))
    print("  Compare with the 'Expected' lines in gmail-test-emails/README.md.")


# talk to the whole agent with your real calendar + gmail
def chat(interests, use_web):
    from agent.agent import ECAgent
    print("\nAgent chat (blank line to quit). Approved plans are added to your ECAgent calendar.")
    intake = {"student": {"name": "Test Student"}, "interests": interests, "goals": [], "dislikes": [],
              "hard_constraints": [{"id": "H1", "type": "earliest_start", "time": "08:00"},
                                   {"id": "H2", "type": "latest_end", "time": "22:00"}],
              "soft_preferences": [], "schedule": {"timezone": "America/Chicago", "recurring": [], "one_off": []}}
    agent = ECAgent(calendar=GoogleCalendar(), inbox=EmailSource(GmailInbox()), use_web=use_web)
    agent.load([], intake, run_id=f"live/{date.today()}")
    while True:
        text = input("\nyou> ").strip()
        if not text:
            break
        reply, selection = agent.run(text)
        print(f"\nagent> {reply}")
        if selection:
            print("  [activities this turn: " + "; ".join(f"{a['title']} {a.get('date')} {a.get('start')}" for a in selection) + "]")
    print("Details of every step are in agent/model_log.json.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["calendar", "gmail"])
    ap.add_argument("--agent", action="store_true", help="after the checks, chat with the whole agent")
    ap.add_argument("--interests", nargs="+", default=["photography", "volunteering"])
    ap.add_argument("--no-web", action="store_true", help="agent: don't use the web search fallback")
    args = ap.parse_args()
    try:
        if args.only != "gmail":
            test_calendar()
        if args.only != "calendar":
            test_gmail()
    except Exception as e:
        check("unexpected error", False, f"{type(e).__name__}: {e}")
    print(f"\n{sum(results)}/{len(results)} checks passed")
    if args.agent:
        chat(args.interests, not args.no_web)
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
