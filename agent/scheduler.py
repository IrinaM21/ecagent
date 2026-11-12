import json
import re
from datetime import date, timedelta

from agent.calendars import CalendarError, SimulatedCalendar, end_of  # SimulatedCalendar kept here so old imports work
from utils import Utils

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MIN_WINDOW = 30  # minutes; shorter free gaps don't count (same as eval/validate_profiles.py)
DEFAULT_MINUTES = 60  # assumed length for events that only list a start time


class Scheduler(Utils):
    # TODO re-check event details (DB / url) right before adding, since a real approval can come
    #   days after the proposal.

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
            calendar_events (list[dict]): events from the calendar tool + email commitments (date/start/end)

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
        # no end time -> assume DEFAULT_MINUTES; all-day events are skipped
        out += [{"date": e["date"], "start": e["start"], "end": end_of(e), "source": e.get("source") or "calendar"}
                for e in calendar_events if e.get("date") and e.get("start")]
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
            existing (list[dict]): calendar events + email commitments this week (all busy time;
                                   only agent-added ones count toward the weekly caps)
            usage (dict): optional token counter, see Utils.call

        Returns:
            list[list[dict]]: valid candidate plans
        """
        busy = self.busy_blocks(state, existing)
        planned = [e for e in existing if e.get("agent_added", True)]
        plans = self.generate_plans(query, state, activities, busy, n_items, existing=planned, usage=usage)
        self.log("schedule", n_activities=len(activities), n_existing=len(existing), n_plans=len(plans))
        return plans

    # agent-added activities on the calendar that don't fit anymore (e.g. after a schedule change)
    def find_conflicts(self, state, existing):
        planned = [e for e in existing if e.get("agent_added", True) and e.get("date") and e.get("start")]
        others = [e for e in existing if e not in planned]
        busy = self.busy_blocks(state, others)
        conflicts = [e for e in planned if not self.fits(e, state, busy)]
        self.log("find_conflicts", n_planned=len(planned), conflicts=[e["title"] for e in conflicts])
        return conflicts

    def summarize(self, plans):
        """Short summary of schedule() output for the agent's router."""
        if not plans:
            return "scheduler: no valid plan fits the student's week"
        sizes = "-".join(map(str, sorted({len(p) for p in plans})))
        return f"scheduler: valid candidate plans: {len(plans)}, activities per plan: {sizes}"

    # reads the calendar back to find this activity's event (None if it's not there)
    @staticmethod
    def find_added(calendar, activity, event_id=None):
        try:
            events = calendar.get_events(activity["date"], activity["date"])
        except CalendarError:
            return None
        for e in events:
            if event_id is not None and e["id"] == event_id:
                return e
            if event_id is None and e.get("agent_added", True) and e["title"] == activity["title"] \
                    and e.get("start") == activity.get("start"):
                return e
        return None

    def add_to_calendar(self, plan, calendar, retries=1):
        """
        Add an approved plan and check each add by reading the calendar back. A failed add is retried
        once, after checking it didn't go through anyway (no duplicates).

        Returns:
            list[dict]: per activity {activity, event, verified, error}
        """
        results = []
        for a in plan:
            event, error = None, None
            for _ in range(retries + 1):
                try:
                    event = calendar.add_event(a)
                    break
                except CalendarError as e:
                    error = str(e)
                    event = self.find_added(calendar, a)
                    if event:
                        break
            verified = event is not None and self.find_added(calendar, a, event["id"]) is not None
            if event is not None and not verified:
                error = "added, but not found on the calendar when read back"
            results.append({"activity": a, "event": event, "verified": verified, "error": None if verified else error})
        self.log("add_to_calendar", added=[r["activity"]["title"] for r in results if r["verified"]],
                 failed=[(r["activity"]["title"], r["error"]) for r in results if not r["verified"]])
        return results

    # remove events and check they're actually gone
    def remove_from_calendar(self, events, calendar):
        results = []
        for e in events:
            try:
                calendar.remove_event(e["id"])
                gone = self.find_added(calendar, e, e["id"]) is None
                results.append({"event": e, "removed": gone, "error": None if gone else "still on the calendar"})
            except CalendarError as err:
                results.append({"event": e, "removed": False, "error": str(err)})
        self.log("remove_from_calendar", removed=[r["event"]["title"] for r in results if r["removed"]],
                 failed=[(r["event"]["title"], r["error"]) for r in results if not r["removed"]])
        return results

    def summarize_added(self, results, removed=()):
        """Short summary of add_to_calendar() / remove_from_calendar() output for the agent's router."""
        label = lambda a: f"{a['title']} ({a.get('date')} {a.get('start')})"
        parts = []
        if removed:
            parts.append("removed: " + "; ".join(label(r["event"]) + ("" if r["removed"] else f" FAILED: {r['error']}")
                                                 for r in removed))
        ok = [label(r["activity"]) for r in results if r["verified"]]
        bad = [f"{label(r['activity'])}: {r['error']}" for r in results if not r["verified"]]
        parts.append(f"added and verified {len(ok)}" + (f": {'; '.join(ok)}" if ok else ""))
        if bad:
            parts.append(f"NOT added {len(bad)}: {'; '.join(bad)}")
        return "scheduler: " + ", ".join(parts)



