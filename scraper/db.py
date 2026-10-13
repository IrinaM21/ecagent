# SQLite storage for scraped campus events + RSO directory, and the queries the agent uses

import sqlite3
from datetime import date
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "campus.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id    INTEGER NOT NULL,   -- calendars.illinois.edu eventId
    date        TEXT NOT NULL,      -- YYYY-MM-DD
    title       TEXT NOT NULL,
    start_time  TEXT,               -- HH:MM (24h), NULL if all day
    end_time    TEXT,               -- HH:MM (24h), NULL if all day / not listed
    time_text   TEXT,               -- time as shown on the site, e.g. "All Day"
    location    TEXT,
    summary     TEXT,
    event_type  TEXT,               -- e.g. "Lecture", "Exhibition"
    recurring   INTEGER,            -- 1 if the same event_id repeats on other dates
    calendar    TEXT,               -- which calendar it came from
    url         TEXT,
    scraped_at  TEXT,
    PRIMARY KEY (event_id, date)
);
CREATE TABLE IF NOT EXISTS clubs (
    club_id     INTEGER PRIMARY KEY, -- one.illinois.edu group id
    name        TEXT NOT NULL,
    group_type  TEXT,                -- e.g. "Orange Student Organization"
    categories  TEXT,                -- comma separated
    website     TEXT,
    mission     TEXT,
    benefits    TEXT,
    scraped_at  TEXT
);
"""

EVENT_COLS = ["event_id", "date", "title", "start_time", "end_time", "time_text", "location",
              "summary", "event_type", "recurring", "calendar", "url", "scraped_at"]
CLUB_COLS = ["club_id", "name", "group_type", "categories", "website", "mission", "benefits",
             "scraped_at"]


def connect(path=DB_PATH):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _insert(conn, table, cols, rows):
    sql = f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
    conn.executemany(sql, [[r[c] for c in cols] for r in rows])


def save_events(events, path=DB_PATH):
    # replace everything from the first scraped date onward, so cancelled events drop out.
    # past events are kept.
    conn = connect(path)
    with conn:
        conn.execute("DELETE FROM events WHERE date >= ?", (min(e["date"] for e in events),))
        _insert(conn, "events", EVENT_COLS, events)
    conn.close()


def save_clubs(clubs, path=DB_PATH):
    conn = connect(path)
    with conn:
        conn.execute("DELETE FROM clubs")
        _insert(conn, "clubs", CLUB_COLS, clubs)
    conn.close()


def _query(sql, params, path):
    if not Path(path).exists():
        raise FileNotFoundError(f"{path} not found, run `python -m scraper.scrape` first")
    conn = connect(path)
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def _keyword_filter(query, cols):
    # every word in the query has to show up in at least one of the columns
    clauses, params = [], []
    for word in query.split():
        clauses.append("(" + " OR ".join(f"{c} LIKE ?" for c in cols) + ")")
        params += [f"%{word}%"] * len(cols)
    return clauses, params


def search_events(query="", start_date=None, end_date=None, limit=20, path=DB_PATH):
    """Events matching all keywords in query, between start_date and end_date (YYYY-MM-DD,
    inclusive). start_date defaults to today."""
    clauses, params = _keyword_filter(query, ["title", "summary", "location", "event_type"])
    clauses.append("date >= ?")
    params.append(start_date or date.today().isoformat())
    if end_date:
        clauses.append("date <= ?")
        params.append(end_date)
    sql = f"SELECT * FROM events WHERE {' AND '.join(clauses)} ORDER BY date, start_time LIMIT ?"
    return _query(sql, params + [limit], path)


def search_clubs(query="", limit=20, path=DB_PATH):
    """RSOs matching all keywords in query."""
    clauses, params = _keyword_filter(query, ["name", "categories", "mission", "benefits"])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return _query(f"SELECT * FROM clubs {where} ORDER BY name LIMIT ?", params + [limit], path)
