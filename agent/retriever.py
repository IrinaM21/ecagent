import json
import re
from datetime import date, timedelta

from scraper import db
from scraper.tools import WEB_TOOLS
from utils import Utils


class Retriever(Utils):
    # email activities come from agent/gmail.py (inbox) and get scored with the DB results
    # TODO Outlook (Microsoft Graph /me/messages) as a second inbox with the same scan()
    # TODO Club meeting times: the clubs table is the directory only (no meetings; one.illinois.edu
    #   events need a NetID login). Fetch meeting times from each club's website (web_fetch) so
    #   clubs become schedulable events.

    def __init__(self, api_key=None, db_path=db.DB_PATH, today=None, inbox=None, use_web=True):
        """
        Args:
            db_path: events/clubs database (eval passes the frozen snapshot)
            today (str): YYYY-MM-DD, pinned to the reference week start in eval; None = real today
            inbox (EmailSource): email activities as extra candidates, None in eval
            use_web (bool): allow the web search fallback (can turn off for eval)
        """
        super().__init__(api_key)
        self.db_path = db_path
        self.today = today
        self.inbox = inbox
        self.use_web = use_web
        # prompt format: role (this system prompt), instructions, info in XML tags, response format
        # (based on CS 498 Using and Understanding AI (FA26), Intro to Prompting lecture)
        self.sys = """You are the retriever for a UIUC extracurricular planner. You find campus events and
        student clubs for a student's request and judge how well they fit the student."""

    @staticmethod
    def to_common(row, source="db"):
        """
        DB row (event or club) -> the common format every module uses [4].
        Events have a date/time; clubs don't (the directory has no meeting times).
        Web results use the same keys with id=None, source="web" and a required url.
        """
        if "event_id" in row:
            return {"kind": "event", "id": row["event_id"], "title": row["title"],
                    "date": row["date"], "start": row["start_time"], "end": row["end_time"],
                    "location": row["location"], "description": row["summary"],
                    "categories": row["event_type"], "url": row["url"], "source": source}
        return {"kind": "club", "id": row["club_id"], "title": row["name"],
                "date": None, "start": None, "end": None,
                "location": None, "description": row["mission"],
                "categories": row["categories"], "url": row["website"], "source": source}

    def search_db(self, keywords, start_date=None, end_date=None, limit=20):
        """
        Events + clubs matching ANY keyword, deduplicated, in common format.
        db.search_events/search_clubs require ALL words of a query to match, so search one keyword at a time.

        Args:
            keywords (list[str]): search terms, e.g. ["photography", "camera"]
            start_date, end_date (str): YYYY-MM-DD, inclusive; start defaults to self.today
            limit (int): max events and max clubs per keyword

        Returns:
            list[dict]: candidates in the to_common format
        """
        # TODO add semantic search (e.g. embeddings over title/summary/mission) to replace or
        # supplement keyword search, which misses synonyms ("photo walk" vs. "photography")
        found = {}
        for word in keywords:
            # db rows are plain dicts (sqlite3), keyed like the primary key so repeats collapse
            for row in db.search_events(word, start_date=start_date or self.today, end_date=end_date,
                                        limit=limit, path=self.db_path):
                found[("event", row["event_id"], row["date"])] = self.to_common(row)
            for row in db.search_clubs(word, limit=limit, path=self.db_path):
                found[("club", row["club_id"])] = self.to_common(row)
        return list(found.values())

    def score(self, query, state, candidates, usage=None):
        """
        Rate each candidate 0-10 = roughly the chance out of 10 that the student approves it,
        judged on content only (the scheduler checks time). Adds 'score' to each candidate and
        returns them sorted high to low. Candidates the model skips get 0.

        Args:
            query (str): what the agent is searching for (from the user's request)
            state (dict): memory's 'state' note
            candidates (list[dict]): to_common items
            usage (dict): optional token counter, see Utils.call
        """
        if not candidates:
            return []
        student = {k: state.get(k) for k in ("interests", "goals", "dislikes", "soft_preferences")}
        # non-time rules the content can break (time rules are the scheduler's job)
        rules = [h for h in state.get("hard_constraints", [])
                 if h["type"] in ("exclude", "max_cost_usd", "format", "location")]
        listing = "\n".join(f"{i + 1}. [{c['kind']}] {c['title']} | {c['categories'] or ''} | {(c['description'] or '')[:300]}"
                            for i, c in enumerate(candidates))

        prompt = f"""Rate each activity by how likely this student would be to approve it if we recommended it.
        Judge the content only (topic, type, format, cost, social setting). Ignore the date and time:
        the schedule is checked separately.

        Score = roughly the chance out of 10 that the student approves:
        0    definitely rejects: matches a dislike or breaks one of the rules below
        1-2  very unlikely to approve (~10-20%): unrelated to their interests and goals
        3-4  unlikely (~30-40%): only loosely related
        5    coin flip (~50%): somewhat relevant, no clear reason to approve or reject
        6-7  likely (~60-70%): clearly matches one of their interests or goals
        8-9  very likely (~80-90%): matches a main interest and fits their stated preferences
        10   certain to approve

        <request>{query}</request>
        <student>{json.dumps(student)}</student>
        <rules>{json.dumps(rules)}</rules>
        <activities>
        {listing}
        </activities>

        Reply with one line per activity in the format N: score (e.g. "3: 7"). Score every activity."""

        text = self.text_of(self.call([{"role": "user", "content": prompt}], system=self.sys, usage=usage))
        scores = {int(n) - 1: int(s) for n, s in re.findall(r"^\s*(\d+)\s*:\s*(\d+)", text, re.M)}
        for i, c in enumerate(candidates):
            c["score"] = min(10, scores.get(i, 0))
        return sorted(candidates, key=lambda c: c["score"], reverse=True)

    def web_fallback(self, query, state, start_date=None, end_date=None, max_continuations=3, usage=None):
        """
        Search the web for UIUC activities when the database has too few good matches.
        Returns to_common items (source="web"). Only keeps items whose url actually came back
        from web_search/web_fetch, so a made-up page can't get through.

        Args:
            query (str): what the agent is searching for
            state (dict): memory's 'state' note (interests are passed to the search)
            start_date, end_date (str): YYYY-MM-DD; start defaults to self.today
            max_continuations (int): max resends after pause_turn
            usage (dict): optional token counter, see Utils.call
        """
        start = start_date or self.today or date.today().isoformat()
        prompt = f"""Find extracurricular activities at the University of Illinois Urbana-Champaign for this request,
        in the dates given. Use web search, and fetch pages to confirm details.
        Only include activities you saw on a page.

        <request>{query}</request>
        <interests>{json.dumps(state.get("interests", []))}</interests>
        <dates>{start} to {end_date or "any"}</dates>

        Reply with a JSON list inside <activities></activities> tags,
        each item: {{"kind": "event" or "club", "title": ..., "date": "YYYY-MM-DD" or null,
        "start": "HH:MM" or null, "end": "HH:MM" or null, "location": ..., "description": ...,
        "url": the page you saw it on}}"""

        messages = [{"role": "user", "content": prompt}]
        urls, text = set(), ""
        for _ in range(max_continuations):
            ans = self.call(messages, system=self.sys, tools=WEB_TOOLS, usage=usage)
            for b in ans["content"]:
                # successful search results are a list; errors come back as a single object
                if b["type"] == "web_search_tool_result" and isinstance(b["content"], list):
                    urls |= {r["url"] for r in b["content"]}
                elif b["type"] == "web_fetch_tool_result" and b["content"].get("type") == "web_fetch_result":
                    urls.add(b["content"].get("url"))
            text += self.text_of(ans)
            if ans["stop_reason"] != "pause_turn":
                break
            # server tools hit their per-call step limit: resend so the server resumes (no extra user message)
            messages.append({"role": "assistant", "content": ans["content"]})

        match = re.search(r"<activities>\s*(\[.*\])\s*</activities>", text, re.S)
        try:
            items = json.loads(match.group(1))
        except (AttributeError, json.JSONDecodeError):
            return []
        keys = ["kind", "title", "date", "start", "end", "location", "description"]
        return [dict({k: it.get(k) for k in keys}, id=None, categories=None, url=it["url"], source="web")
                for it in items if isinstance(it, dict) and it.get("url") in urls]

    def keywords(self, query, state, usage=None):
        """
        5-10 database search terms for the request, from the student's interests/goals (incl. synonyms),
        so the agent doesn't need the student's profile to search.

        Args:
            query (str): the user's request
            state (dict): memory's 'state' note
            usage (dict): optional token counter, see Utils.call
        """
        prompt = f"""Give 5-10 short search keywords (single words) for finding campus events and student clubs
        that match this request and student. Include common synonyms (e.g. photography -> photo, camera).

        <request>{query}</request>
        <interests>{json.dumps(state.get("interests", []))}</interests>
        <goals>{json.dumps(state.get("goals", []))}</goals>

        Reply with the keywords on one line, separated by commas."""
        text = self.text_of(self.call([{"role": "user", "content": prompt}], system=self.sys, max_tokens=100, usage=usage))
        return [w.strip().lower() for w in text.split(",") if w.strip()][:10]

    def retrieve(self, query, state, start_date=None, end_date=None, k=3, threshold=7, usage=None):
        """
        Agent tool: keywords -> database search -> approval scores -> web fallback if fewer than k
        candidates score >= threshold (7 = likely approved, ~70%, see score()).

        Args:
            query (str): what the agent is searching for (from the user's request)
            state (dict): memory's 'state' note
            start_date, end_date (str): YYYY-MM-DD, inclusive
            k (int): min number of good candidates before falling back to the web
            threshold (int): min approval score for a candidate to count as good
            usage (dict): optional token counter, see Utils.call

        Returns:
            list: good candidates sorted by score (counts and web use are logged to agent/model_log.json)
        """
        keywords = self.keywords(query, state, usage)
        info = {"db_found": 0, "email_found": 0, "web_used": False, "web_found": 0}
        try:
            found = self.search_db(keywords, start_date, end_date)
        except FileNotFoundError as e:  # no database yet (e.g. a live test): email/web can still answer
            found = []
            info["db_error"] = str(e)
        info["db_found"] = len(found)
        if self.inbox is not None:
            first = start_date or self.today or date.today().isoformat()
            last = end_date or (date.fromisoformat(first) + timedelta(days=6)).isoformat()
            try:
                emails = self.inbox.scan(first, last, usage)["activities"]
            except Exception as e:  # an inbox problem must not stop retrieval
                emails = []
                info["email_error"] = str(e)
            info["email_found"] = len(emails)
            found += emails
        candidates = self.score(query, state, found, usage)
        good = [c for c in candidates if c["score"] >= threshold]
        info["good"] = len(good)
        if len(good) < k and self.use_web:
            web = self.score(query, state, self.web_fallback(query, state, start_date, end_date, usage=usage), usage)
            info.update(web_used=True, web_found=len(web))
            good += [c for c in web if c["score"] >= threshold]
        self.log("retrieve", query=query, keywords=keywords, **info)
        return sorted(good, key=lambda c: c["score"], reverse=True)

    def summarize(self, activities):
        """Short summary of retrieve() output for the agent's router."""
        n_timed = sum(bool(a.get("date") and a.get("start")) for a in activities)
        n_email = sum(a.get("source") == "email" for a in activities)
        top = ", ".join(a["title"] for a in activities[:3])
        return (f"retriever: {len(activities)} activities found ({n_timed} with a date/time, "
                f"{len(activities) - n_timed} without, e.g. clubs" + (f"; {n_email} from email" if n_email else "")
                + ")" + (f", top: {top}" if activities else ""))
