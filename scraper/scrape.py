# Scheduled scraper: general campus events + RSO directory -> data/campus.db
#
# Run from the repo root:   python -m scraper.scrape
# Weekly (Mondays 6am) with cron:
#   0 6 * * 1 cd /path/to/ecagent && python -m scraper.scrape

import re
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from scraper import db

CALENDAR_BASE = "https://calendars.illinois.edu"
CALENDARS = {7: "General Events", 4063: "Illini Union"}
# one.illinois.edu/events needs a NetID login, so we only scrape the public group directory
CLUBS_URL = "https://one.illinois.edu/club_signup?view=all"
HEADERS = {"User-Agent": "ecagent-scraper/0.1 (UIUC CS 498 class project)"}


def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=120)
    r.raise_for_status()
    return r.text


def text_of(node):
    return " ".join(node.get_text(" ", strip=True).split()) if node else None


def parse_time(time_text):
    """'9:00 - 10:30 pm' -> ('21:00', '22:30'). 'All Day' -> (None, None)."""
    times = []
    for hour, minute, meridiem in re.findall(r"(\d{1,2}):(\d{2})\s*(am|pm)?", time_text or ""):
        times.append([int(hour) % 12, int(minute), meridiem])
    if not times:
        return None, None
    # the site drops am/pm on the start time when it matches the end time
    start_guessed = not times[0][2]
    if start_guessed:
        times[0][2] = times[-1][2] or "am"
    mins = [(h + 12 * (m == "pm")) * 60 + minute for h, minute, m in times]
    # guessed wrong if it lands after the end, e.g. "11:00 - 1:00 pm" starts in the morning
    if start_guessed and len(mins) > 1 and mins[0] > mins[1]:
        mins[0] = (mins[0] + 12 * 60) % (24 * 60)
    fmt = [f"{m // 60:02d}:{m % 60:02d}" for m in mins]
    return fmt[0], (fmt[1] if len(fmt) > 1 else None)


def parse_events(html, calendar_id, scraped_at):
    soup = BeautifulSoup(html, "html.parser")
    events = []
    # each day is an <h2> date header followed by a <ul class="event-entries">
    for day in soup.select("ul.event-entries"):
        date = datetime.strptime(text_of(day.find_previous("h2")), "%A, %B %d, %Y").date()
        for entry in day.find_all("li", class_="entry", recursive=False):
            link = entry.select_one(".entry-heading a")
            time_text = text_of(entry.select_one(".entry-time dd"))
            start, end = parse_time(time_text)
            events.append({
                "event_id": int(re.search(r"eventId=(\d+)", link["href"]).group(1)),
                "date": date.isoformat(),
                "title": text_of(link),
                "start_time": start,
                "end_time": end,
                "time_text": time_text,
                "location": text_of(entry.select_one(".entry-location dd")),
                "summary": text_of(entry.select_one(".summary")),
                "event_type": ", ".join(text_of(t) for t in entry.select(".type a")),
                "recurring": int("recurring-event" in entry["class"]),
                "calendar": CALENDARS[calendar_id],
                "url": CALENDAR_BASE + link["href"],
                "scraped_at": scraped_at,
            })
    return events


def parse_clubs(html, scraped_at):
    soup = BeautifulSoup(html, "html.parser")
    clubs = []
    # closed-membership groups have no checkbox value, so read the id off the input's id instead
    for box in soup.select("li.list-group-item input[name=clubs]"):
        club_id = box["id"].removeprefix("cb_club_")
        item = box.find_parent("li")
        # "Orange Student Organization - Advocacy & Activism, Education"
        type_line = text_of(item.select_one("p.grey-element")) or ""
        group_type, _, categories = type_line.partition(" - ")
        website = item.select_one('a[aria-label="Website"]')
        mission = text_of(item.find(id=f"club_{club_id}")) or ""
        benefits = text_of(item.find(id=f"club_whatwedo_{club_id}")) or ""
        clubs.append({
            "club_id": int(club_id),
            "name": text_of(item.select_one("h2.media-heading")),
            "group_type": group_type,
            "categories": categories,
            "website": website["href"].strip() if website else None,
            "mission": mission.removeprefix("Mission").strip(),
            "benefits": benefits.removeprefix("Membership Benefits").strip(),
            "scraped_at": scraped_at,
        })
    return clubs


def main():
    scraped_at = datetime.now().isoformat(timespec="seconds")

    # same event can be on both calendars, keep the first one we see
    events = {}
    for calendar_id in CALENDARS:
        for e in parse_events(fetch(f"{CALENDAR_BASE}/list/{calendar_id}"), calendar_id, scraped_at):
            events.setdefault((e["event_id"], e["date"]), e)
        time.sleep(1)
    clubs = parse_clubs(fetch(CLUBS_URL), scraped_at)

    # only touch the database once every page scraped fine, so a failed run keeps the old data
    if not events or not clubs:
        raise SystemExit(f"Scrape looks broken ({len(events)} events, {len(clubs)} clubs), "
                         "database not updated")
    db.save_events(list(events.values()))
    db.save_clubs(clubs)
    print(f"Saved {len(events)} events and {len(clubs)} clubs to {db.DB_PATH}")


if __name__ == "__main__":
    main()
