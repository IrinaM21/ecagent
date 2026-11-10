import json
import re
from datetime import date, timedelta

from utils import Utils

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MIN_WINDOW = 30  # minutes; shorter free gaps don't count (same as eval/validate_profiles.py)
DEFAULT_MINUTES = 60  # assumed length for events that only list a start time


class SimulatedCalendar(Utils):
    """
    Calendar tool for eval: starts empty (the student's schedule comes from memory) and records
    the events the agent adds, so the grader can check them. Same interface as the planned
    GoogleCalendar. Extends Utils only for log().
    """
    def __init__(self, api_key=None):
        super().__init__(api_key)
        self.events = []

    def get_events(self, start=None, end=None):
        """Events between start and end (YYYY-MM-DD, inclusive); all events if not given."""
        return [e for e in self.events
                if (start is None or e["date"] >= start) and (end is None or e["date"] <= end)]

    def add_event(self, event):
        """Add an activity the student approved. Returns a confirmation for the agent's LLM."""
        self.events.append(dict(event))
        self.log("add_event", title=event["title"], date=event["date"], start=event["start"])
        return f"Added {event['title']} on {event['date']} at {event['start']}"


class Scheduler(Utils):
    # TODO Google Calendar API tool: GoogleCalendar class with the same interface as SimulatedCalendar
    #   (get_events(start, end) -> GET /calendar/v3/calendars/primary/events?timeMin=..&timeMax=..,
    #    add_event(event) -> POST /calendar/v3/calendars/primary/events), using requests + an OAuth token.
    #   add_event only after the student approves the plan. Re-check event details (DB / url) right
    #   before adding, since a real approval can come days after the proposal.
    #   Add Google Cloud console setup (project, Calendar API, OAuth client, consent screen) to README.

    def __init__(self, api_key=None, today=None):
        """
        Args:
            today (str): YYYY-MM-DD start of the planning week if the state has no reference_week
                         (pinned to 2026-11-02 in eval); None = real today
        """
        super().__init__(api_key)
        self.today = today

    @staticmethod
    def applies(item, d):
        """Does a recurring/one-off item or 'unavailable' constraint apply on date d? (same rule as eval/validate_profiles.py)"""
        if "date" in item:
            return item["date"] == d.isoformat()
        if DAYS[d.weekday()] not in item.get("days", []):
            return False
        return item.get("valid_from", "0000") <= d.isoformat() <= item.get("valid_until", "9999")

    def busy_blocks(self, state, calendar_events=()):
        """
        Busy times for the week: the 'state' note's recurring + one-off items and 'unavailable'
        constraints, plus events already on the calendar. The state is LLM-updated, so items
        missing a start/end are skipped instead of crashing.

        Args:
            state (dict): memory's 'state' note
            calendar_events (list[dict]): events from the calendar tool (date/start/end)

        Returns:
            list[dict]: [{date, start, end, source}] sorted by date and start
        """
        sched = state.get("schedule", {})
        first = sched.get("reference_week", {}).get("start") or self.today or date.today().isoformat()
        week = [date.fromisoformat(first) + timedelta(days=i) for i in range(7)]
        sources = sched.get("recurring", []) + sched.get("one_off", []) + \
            [c for c in state.get("hard_constraints", []) if c.get("type") == "unavailable"]
        out = [{"date": d.isoformat(), "start": s["start"], "end": s["end"], "source": s.get("id")}
               for d in week for s in sources if s.get("start") and s.get("end") and self.applies(s, d)]
        out += [{"date": e["date"], "start": e["start"], "end": e["end"], "source": "calendar"}
                for e in calendar_events if e.get("date") and e.get("start") and e.get("end")]
        return sorted(out, key=lambda b: (b["date"], b["start"]))

    @staticmethod
    def mins(t):
        """'HH:MM' -> minutes after midnight."""
        return int(t[:2]) * 60 + int(t[3:])

    def free_windows(self, state, busy, day):
        """
        Free (start, end) minute ranges on day: inside earliest_start..latest_end, with busy blocks
        padded by buffer_minutes and merged, and gaps under 30 min dropped (same as the grader).

        Args:
            state (dict): memory's 'state' note
            busy (list[dict]): output of busy_blocks
            day (str): YYYY-MM-DD
        """
        hard = {c.get("type"): c for c in state.get("hard_constraints", [])}
        lo = self.mins(hard.get("earliest_start", {}).get("time", "00:00"))
        hi = self.mins(hard.get("latest_end", {}).get("time", "23:59"))
        buf = hard.get("buffer_minutes", {}).get("minutes", 0)
        merged = []
        for s, e in sorted((self.mins(b["start"]) - buf, self.mins(b["end"]) + buf) for b in busy if b["date"] == day):
            if merged and s <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])
        out, free = [], lo
        for s, e in merged + [[hi, hi]]:
            s, e = max(s, lo), max(e, lo)
            if min(s, hi) - free >= MIN_WINDOW:
                out.append((free, min(s, hi)))
            free = max(free, e)
        return out

    def fits(self, event, state, busy):
        """
        Can the student attend this event? It must lie inside one free window on its date and not
        exceed max_event_duration_hours. Events without a date/start time can't be checked -> False.

        Args:
            event (dict): to_common item
            state (dict): memory's 'state' note
            busy (list[dict]): output of busy_blocks
        """
        if not event.get("date") or not event.get("start"):
            return False
        start = self.mins(event["start"])
        end = self.mins(event["end"]) if event.get("end") else start + DEFAULT_MINUTES
        cap = next((c["value"] for c in state.get("hard_constraints", [])
                    if c.get("type") == "max_event_duration_hours"), None)
        if end <= start or (cap is not None and end - start > cap * 60):
            return False
        return any(ws <= start and end <= we for ws, we in self.free_windows(state, busy, event["date"]))

    @staticmethod
    def hhmm(x):
        """minutes after midnight -> 'HH:MM'."""
        return f"{x // 60:02d}:{x % 60:02d}"

    def length(self, e):
        """Event length in minutes (DEFAULT_MINUTES if no end time)."""
        start = self.mins(e["start"])
        return (self.mins(e["end"]) if e.get("end") else start + DEFAULT_MINUTES) - start

    def check_plan(self, plan, state, busy, existing=()):
        """
        Hard-constraint check for one candidate plan. Returns a list of conflicts (empty = valid):
        events outside the student's free time (busy blocks + buffers, earliest/latest, max event
        length), plan items that overlap each other (incl. buffers), repeated activities, clubs
        (no meeting time), and weekly caps (max activities / max hours, counting `existing`).

        Args:
            plan (list[dict]): to_common items proposed for the week
            state (dict): memory's 'state' note
            busy (list[dict]): output of busy_blocks
            existing (list[dict]): activities already on the calendar this week (count toward caps)
        """
        label = lambda e: f"{e['title']} ({' '.join(x for x in (e.get('date') or 'no date', e.get('start')) if x)})"
        same = lambda a, b: (a["source"], a["id"] or a["title"]) == (b["source"], b["id"] or b["title"])
        block = lambda e: {"date": e["date"], "start": e["start"],
                           "end": self.hhmm(self.mins(e["start"]) + self.length(e))}
        conflicts = []
        for i, e in enumerate(plan):
            if e["kind"] != "event" or not e.get("date") or not e.get("start"):
                conflicts.append(f"{label(e)}: no date/start time, can't be scheduled")
                continue
            others = [block(p) for j, p in enumerate(plan)
                      if j != i and p["kind"] == "event" and p.get("start") and p.get("date")]
            if not self.fits(e, state, busy):
                conflicts.append(f"{label(e)}: outside the student's free time")
            elif not self.fits(e, state, busy + others):
                conflicts.append(f"{label(e)}: overlaps another activity in the plan (incl. buffer)")
            if any(same(e, p) for p in plan[:i]):
                conflicts.append(f"{label(e)}: same activity appears twice")

        hard = {c.get("type"): c for c in state.get("hard_constraints", [])}
        max_n = hard.get("max_activities_per_week", {}).get("value")
        if max_n is not None and len(plan) + len(existing) > max_n:
            conflicts.append(f"{len(plan) + len(existing)} activities this week, max is {max_n}")
        max_hours = hard.get("max_hours_per_week", {}).get("value")
        if max_hours is not None:
            total = sum(self.length(e) for e in list(plan) + list(existing) if e.get("start"))
            if total > max_hours * 60:
                conflicts.append(f"{total / 60:.1f} hours this week, max is {max_hours}")
        return conflicts

    @staticmethod
    def pick(events, items):
        """Activity numbers from the prompt (1-based) -> events; out-of-range numbers are ignored."""
        return [events[i - 1] for i in items if isinstance(i, int) and 1 <= i <= len(events)]

    def get_tools(self):
        """Tool schemas for the scheduler's LLM (generate_plans)."""
        return [
            {"name": "free_time",
             "description": "The student's free time windows on a date (busy times, buffers and "
                            "earliest/latest times already applied).",
             "input_schema": {"type": "object",
                              "properties": {"date": {"type": "string", "description": "YYYY-MM-DD"}},
                              "required": ["date"], "additionalProperties": False}},
            {"name": "check_plan",
             "description": "Check a candidate plan against the student's hard constraints. "
                            "Returns the list of conflicts, or \"valid\".",
             "input_schema": {"type": "object",
                              "properties": {"items": {"type": "array", "items": {"type": "integer"},
                                                       "description": "activity numbers from the list"}},
                              "required": ["items"], "additionalProperties": False}},
        ]

    def get_tool_registry(self, events, state, busy, existing=()):
        """Tool name -> function, bound to this run's events, state, busy blocks and existing activities."""
        return {
            "free_time": lambda date: [f"{self.hhmm(a)}-{self.hhmm(b)}" for a, b in self.free_windows(state, busy, date)],
            "check_plan": lambda items: self.check_plan(self.pick(events, items), state, busy, existing) or "valid",
        }

    def generate_plans(self, query, state, activities, busy, n_items=1, n_plans=5, existing=(),
                       max_rounds=8, usage=None):
        """
        The LLM proposes candidate plans (combinations of the given real activities, chosen by
        number, so it can't invent events) and checks each one with the
        free_time / check_plan tools, revising until they pass. Final plans are re-checked in code
        and invalid ones are dropped. Soft preferences are not used here: the candidates are
        already ranked by the recommender, and plans are ranked afterwards (rank_plans).

        Args:
            query (str): the user's request
            state (dict): memory's 'state' note
            activities (list[dict]): retrieved activities ranked by Recommender.rank_candidates (best first)
            busy (list[dict]): output of busy_blocks
            n_items (int): max activities per plan (the LLM picks the number from the request)
            n_plans (int): max number of plans
            existing (list[dict]): activities already on the calendar this week (count toward caps)
            max_rounds (int): max model calls
            usage (dict): optional token counter, see Utils.call

        Returns:
            list[list[dict]]: valid plans
        """
        events = [a for a in activities if a["kind"] == "event" and a.get("date") and a.get("start")]
        if not events:
            self.log("generate_plans", n_events=0, kept=0)
            return []
        tools = self.get_tools()
        tool_registry = self.get_tool_registry(events, state, busy, existing)

        listing = "\n".join(f"{i + 1}. {e['date']} {e['start']}-{e['end'] or '?'} {e['title']} "
                            f"({e['location'] or 'location n/a'})" for i, e in enumerate(events))
        prompt = f"""You are a scheduler. Build up to {n_plans} different candidate plans for the student's
request, each with as many activities as the request asks for (1 if it asks for one activity),
at most {n_items}, from the numbered list below (best-ranked first).
Use free_time to see when the student is free, and check_plan to check every plan. If a plan has
conflicts, revise it and check it again. Only return plans that check_plan says are valid.

<request>{query}</request>
<activities>
{listing}
</activities>

When done, reply with the valid plans as a JSON list of lists of activity numbers inside
<plans></plans> tags, e.g. <plans>[[1, 4], [2]]</plans>."""

        messages = [{"role": "user", "content": prompt}]
        r, tools_used, stop_reason, text = 0, [], None, ""
        while r < max_rounds and stop_reason in ("tool_use", None):
            ans = self.call(messages, tools=tools, usage=usage)
            r += 1  # called model once
            stop_reason = ans["stop_reason"]
            messages.append({"role": "assistant", "content": ans["content"]})
            if stop_reason != "tool_use":
                text = self.text_of(ans)
                break
            # run every tool_use block, send all results back in one user message
            blocks = []
            for block in ans["content"]:
                if block["type"] == "tool_use":
                    try:
                        result = tool_registry[block["name"]](**block["input"])
                        blocks.append({"type": "tool_result", "tool_use_id": block["id"], "content": json.dumps(result)})
                        tools_used.append(block["name"])
                    except Exception as e:
                        blocks.append({"type": "tool_result", "tool_use_id": block["id"],
                                       "content": f"Error: {e}", "is_error": True})
            messages.append({"role": "user", "content": blocks})

        match = re.search(r"<plans>\s*(\[.*\])\s*</plans>", text, re.S)
        try:
            proposed = json.loads(match.group(1))
        except (AttributeError, json.JSONDecodeError):
            proposed = []
        # never trust the LLM's "valid": re-check every plan in code
        plans, dropped = [], 0
        for items in proposed[:n_plans]:
            plan = self.pick(events, items) if isinstance(items, list) else []
            if plan and not self.check_plan(plan, state, busy, existing) and plan not in plans:
                plans.append(plan)
            else:
                dropped += 1
        self.log("generate_plans", n_events=len(events), rounds=r, tools_used=tools_used,
                 proposed=proposed, kept=len(plans), dropped=dropped)
        return plans
    
    def schedule(self, query, state, activities, n_items=1, existing=(), usage=None):
        """
        Busy times (state + existing calendar events) -> valid candidate plans (generate_plans).
        The agent ranks them with Recommender.rank_plans and asks the student before anything
        is added to the calendar.

        Args:
            query (str): the user's request
            state (dict): memory's 'state' note
            activities (list[dict]): retrieved activities ranked by Recommender.rank_candidates
            n_items (int): max activities per plan (the scheduler's LLM picks the number from the request)
            existing (list[dict]): activities already on the calendar this week (from the agent)
            usage (dict): optional token counter, see Utils.call

        Returns:
            list[list[dict]]: valid candidate plans
        """
        busy = self.busy_blocks(state, existing)
        plans = self.generate_plans(query, state, activities, busy, n_items, existing=existing, usage=usage)
        self.log("schedule", n_activities=len(activities), n_existing=len(existing), n_plans=len(plans))
        return plans

    def summarize(self, plans):
        """Short summary of schedule() output for the agent's router."""
        if not plans:
            return "scheduler: no valid plan fits the student's week"
        sizes = "-".join(map(str, sorted({len(p) for p in plans})))
        return f"scheduler: valid candidate plans: {len(plans)}, activities per plan: {sizes}"

    def add_to_calendar(self, plan, calendar):
        """
        Add an approved plan to the calendar (SimulatedCalendar now, GoogleCalendar later).
        The calendar is passed in by the agent. Returns the calendar's confirmations.
        """
        return [calendar.add_event(a) for a in plan]

    def summarize_added(self, confirmations):
        """Short summary of add_to_calendar() output for the agent's router."""
        return f"scheduler: added {len(confirmations)} activities to the calendar ({'; '.join(confirmations)})"



