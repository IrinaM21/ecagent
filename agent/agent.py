import json
import re
from datetime import date, timedelta

from agent.calendars import CalendarError, SimulatedCalendar
from agent.compactor import Compactor
from agent.memory import Memory
from agent.recommender import Recommender
from agent.registration import ManualRegistration, RegistrationError
from agent.retriever import Retriever
from agent.scheduler import Scheduler
from scraper import db
from utils import Utils

MAX_ITEMS = 5  # cap on activities per plan; the scheduler's LLM picks the number from the request
MAX_STEPS = 8  # router decisions per turn, so a confused router can't loop forever


class ECAgent(Utils):
    def __init__(self, api_key=None, no_compact=False, db_path=db.DB_PATH, today=None, calendar=None,
                 memory_path=None, inbox=None, registration=None, use_web=True):
        """
        Main agent: interprets the query, routes to the modules, validates the result.
        Create one per eval run so memory and the calendar start fresh (no leaks between runs).

        Args:
            no_compact (bool): ablation, memory gets no compactor (no contradiction step, naive truncation)
            db_path: events database (eval passes the frozen snapshot)
            today (str): YYYY-MM-DD, pinned to 2026-11-02 in eval
            calendar: SimulatedCalendar in eval, GoogleCalendar in real use (agent/calendars.py)
            memory_path: JSON file memory is saved to after each turn (None in eval)
            inbox (EmailSource): gmail activities + commitments (agent/gmail.py), None in eval
            registration: SimulatedRegistration in eval, ManualRegistration (just gives the link) by default
            use_web (bool): allow the web search fallback (can turn off for eval)
        """
        super().__init__(api_key)
        self.today = today
        self.memory_path = memory_path
        self.m = Memory(compactor=None if no_compact else Compactor(api_key), api_key=api_key)
        self.inbox = inbox
        self.r = Retriever(api_key, db_path, today, inbox=inbox, use_web=use_web)
        self.rec = Recommender(api_key)
        self.s = Scheduler(api_key, today)
        self.calendar = calendar if calendar is not None else SimulatedCalendar()
        self.reg = registration if registration is not None else ManualRegistration()

    def load(self, conversation_history, intake_profile, run_id=None, usage=None):
        """
        Start a run: load the profile's agent_visible fields into memory (never the whole profile)
        and tag every module's log entries with run_id.

        Args:
            conversation_history (list): agent_visible.conversation_history
            intake_profile (dict): agent_visible.intake_profile
            run_id (str): e.g. "full_agent/P5-long-context/adapt-schedule/trial1"
            usage (dict): optional token counter, see Utils.call
        """
        inbox_parts = (self.inbox, getattr(self.inbox, "inbox", None)) if self.inbox is not None else ()
        for module in (self, self.m, self.m.c, self.r, self.rec, self.s, self.calendar, self.reg, *inbox_parts):
            if module is not None:  # no compactor in no_compact
                module.run_id = run_id
        self.m.load(conversation_history, intake_profile, usage)

    def system_prompt(self):
        """
        Who the agent is, the date, and (after compaction) the summary that replaces the chat history.
        Student info is read by the modules from memory's 'state' note, and rules live in the
        router and tools, not in the prompt.
        """
        today = self.today or date.today().isoformat()
        summary = self.m.read_note("summary")
        return f"""You are a friendly extracurricular planner, helping students enrich their experience at UIUC.
Today is {today}. "This week" means the 7 days starting today (America/Chicago).
{f"<earlier_conversation_summary>{summary}</earlier_conversation_summary>" if summary else ""}"""

    # this week's first and last day (the 7 days starting today)
    def week(self):
        first = date.fromisoformat(self.today) if self.today else date.today()
        return first.isoformat(), (first + timedelta(days=6)).isoformat()

    def get_subagents(self):
        """
        Next-action options for the router (supervisor). The first is the fallback (ask rather than guess).
        'register' and 'calendar' are removed by available() unless the student approved a pending plan.
        """
        return [
            {"name": "user", "description": "ask the student a question, or show results / a plan and ask for approval"},
            {"name": "retriever", "description": "find activities (campus events and student clubs) for the request"},
            {"name": "recommender", "description": "rank the activities found so far (or the candidate plans) by the student's preferences"},
            {"name": "scheduler", "description": "build candidate plans from the found activities that fit the student's week"},
            {"name": "register", "description": "register the student for the approved plan's activities (only if they asked to be registered)"},
            {"name": "calendar", "description": "add the approved plan to the student's calendar (only if they said yes to it)"},
            {"name": "FINISH", "description": "the request is answered"},
        ]

    # which steps the router may pick right now: register/calendar only after the student approved
    def available(self, data, approved):
        pending = self.m.read_note("plan")
        subagents = self.get_subagents()
        if not (approved and pending):
            subagents = [s for s in subagents if s["name"] != "calendar"]
        if not (approved and (pending or data.get("added"))) or "registration" in data:
            subagents = [s for s in subagents if s["name"] != "register"]
        return subagents

    # separate yes/no check on the student's reply ("maybe" or a question counts as no)
    def check_approval(self, user_input, pending, usage=None):
        plan = "; ".join(f"{a['title']} ({a['date']} {a['start']})" for a in pending)
        prompt = (f"The assistant proposed this plan to a student and asked whether to add it to their calendar: {plan}\n"
                  f"<student_reply>{user_input}</student_reply>\n"
                  f"Did the student clearly approve adding this plan? Asking a question, asking for changes, "
                  f"hesitating or saying no all count as not approving. Reply with only YES or NO.")
        answer = self.text_of(self.call([{"role": "user", "content": prompt}], max_tokens=10, usage=usage)).strip().upper()
        approved = answer.startswith("YES")
        self.log("check_approval", answer=answer, approved=approved)
        return approved

    def route(self, task, results, usage=None, subagents=None):
        """
        Router (supervisor): choose the next action from the task, the results so far, and the
        student's latest input. Falls back to the first option ("user") if no valid name is found.

        Args:
            task (str): the student's latest message
            results (list[str]): summaries returned by the subagents so far this turn (see run)
            usage (dict): optional token counter, see Utils.call
            subagents (list[dict]): allowed options (available()); default all except register/calendar

        Returns:
            dict: the chosen subagent spec
        """
        if subagents is None:
            subagents = [s for s in self.get_subagents() if s["name"] not in ("register", "calendar")]

        msg = f"""
You are an LLM router. Choose the ONE next action from the given list of agent names and descriptions, based on the student's message and the results so far.

<subagents> {["<name>" + sub['name'] + "</name> <description>" + sub['description'] + "</description>" for sub in subagents]} </subagents>

Respond with ONLY the NAME of the selected agent.
"""
        content = f"<message>{task}</message>\n<results_so_far>{chr(10).join(results) or 'nothing yet'}</results_so_far>"
        a = self.text_of(self.call([{"role": "user", "content": content}], system=msg, max_tokens=512, usage=usage))
        s = [sub for sub in subagents if sub["name"].lower() in a.lower()]
        s = subagents[0] if not len(s) else s[0]
        self.log("route", subagent=s["name"])
        return s

    def step_retriever(self, task, data, usage=None):
        """Retriever subagent: find + score activities. Returns its summary for the router."""
        data["activities"] = self.r.retrieve(task, self.m.read_note("state"), usage=usage)
        return self.r.summarize(data["activities"])

    def step_recommender(self, task, data, usage=None):
        """Recommender: rank the candidate plans if there are any, otherwise the activities. Returns its summary."""
        state = self.m.read_note("state")
        if data.get("plans"):
            data["plans"] = self.rec.rank_plans(task, state, data["plans"], usage)
            return self.rec.summarize(data["plans"], "candidate plans")
        data["activities"] = self.rec.rank_candidates(task, state, data.get("activities", []), usage)
        return self.rec.summarize(data["activities"], "activities")

    # calendar events + email commitments, plus any problems reading them
    def busy_context(self, usage=None):
        first, last = self.week()
        events, problems = [], []
        try:
            events = self.calendar.get_events(first, last)
        except CalendarError as e:
            problems.append(f"calendar could not be read: {e}")
        if self.inbox is not None:
            try:
                events += self.inbox.scan(first, last, usage)["commitments"]
            except Exception as e:
                problems.append(f"email could not be read: {e}")
        return events, problems

    def step_scheduler(self, task, data, usage=None):
        """
        Scheduler subagent: valid candidate plans from the found activities. Planned activities that
        don't fit anymore go in data["replaces"] (replaced once approved). Returns its summary.
        """
        state = self.m.read_note("state")
        existing, problems = self.busy_context(usage)
        conflicts = self.s.find_conflicts(state, existing)
        data["replaces"] = conflicts
        data["plans"] = self.s.schedule(task, state, data.get("activities", []), n_items=MAX_ITEMS,
                                        existing=[e for e in existing if e not in conflicts], usage=usage)
        summary = self.s.summarize(data["plans"])
        if conflicts:
            summary += ("; already on the calendar but now conflicting (the new plan replaces them): "
                        + "; ".join(f"{e['title']} ({e['date']} {e['start']})" for e in conflicts))
        return summary + "".join(f"; WARNING {p}" for p in problems)

    def verify_response(self, response, activities, schedule_checked, usage=None, tool_results=()):
        """
        Use the LLM to verify that the reply to the student is supported by the activity data
        (no made-up times, costs, or details, and no "fits your schedule" claims that weren't checked).

        Args:
            response (str): the reply about to be sent
            activities (list[dict]): the activities the reply is about
            schedule_checked (bool): True if these activities are a scheduler plan (already passed
                                     check_plan), so schedule claims are allowed
            usage (dict): optional token counter, see Utils.call
            tool_results (list[str]): this turn's subagent summaries, to check "I added it" claims

        Returns:
            dict: {"is_supported": bool, "confidence": float, "explanation": str}
        """
        keys = ("title", "kind", "date", "start", "end", "location", "description", "url")
        sources = {"activities": [{k: a.get(k) for k in keys} for a in activities],
                   "schedule_check": "passed: these activities fit the student's schedule" if schedule_checked
                                     else "not checked: do not claim these fit the student's schedule",
                   "actions_this_turn": list(tool_results)}
        prompt = (f"Verify if the response is supported by the source documents. Here is the response: {response} "
                  f"and here are the source documents: {json.dumps(sources)}. Every claim about an activity "
                  f"(time, place, cost, what it involves, whether it fits the schedule) must be supported by the sources, "
                  f"and every claim that something was added, removed or registered must be supported by actions_this_turn. "
                  f"Return only JSON with these fields: is_supported (boolean), confidence (float from 0-1), explanation (string)")
        text = self.text_of(self.call([{"role": "user", "content": prompt}], max_tokens=1024, usage=usage))
        try:
            result = json.loads(re.search(r"\{.*\}", text, re.S).group())
        except (AttributeError, json.JSONDecodeError):
            result = {"is_supported": False, "confidence": 0.0, "explanation": "something went wrong"}
        self.log("verify_response", **result)
        return result

    def step_user(self, task, data, results, usage=None):
        """
        Talk to the student (ends the turn): propose the best plan and ask for approval, recommend the
        top activities, or ask a clarifying question. The reply is checked with verify_response and
        rewritten once if unsupported. Shown activities go in data["selection"] (graded in eval).

        Args:
            task (str): the student's latest message
            data (dict): this turn's data (activities, plans)
            results (list[str]): subagent summaries so far this turn
            usage (dict): optional token counter, see Utils.call

        Returns:
            str: the reply to the student
        """
        if "calendar_results" in data or "registration" in data:
            # already checked by the scheduler before it was proposed
            shown = data.get("added") or [r["activity"] for r in data.get("registration", [])]
            checked = True
            task_note = ("Tell the student what happened, using only <results_so_far>: which activities were added "
                         "to their calendar (verified), which were removed, which failed, and any registration "
                         "results (confirmation numbers, or links they must use to register themselves). "
                         "Never say something was added or registered unless the results say so.")
        elif data.get("plans"):
            shown, checked = data["plans"][0], True
            self.m.write_note("plan", shown)  # pending until the student answers
            self.m.write_note("replaces", data.get("replaces") or None)
            task_note = "This plan fits the student's schedule. Show it and ask whether to add it to their calendar."
            if data.get("replaces"):
                task_note += (" It replaces these activities already on their calendar, which no longer fit: "
                              + "; ".join(f"{e['title']} ({e['date']} {e['start']})" for e in data["replaces"]) + ".")
        elif data.get("activities"):
            shown, checked = data["activities"][:3], False
            task_note = "Recommend these activities and say why each fits the student."
        else:
            shown, checked = [], False
            task_note = "Ask one clarifying question, or briefly explain that nothing matched."
        keys = ("title", "kind", "date", "start", "end", "location", "description", "url")
        system = (self.system_prompt() + f"\n\n{task_note}\n<results_so_far>{chr(10).join(results)}</results_so_far>"
                  f"\n<activities>{json.dumps([{k: a.get(k) for k in keys} for a in shown])}</activities>")

        reply = self.text_of(self.call(self.m.get_msgs(), system=system, usage=usage))
        check = self.verify_response(reply, shown, checked, usage, results) if shown else {"is_supported": True}
        if not check["is_supported"]:
            system += f"\n\nYour previous draft had unsupported claims: {check['explanation']}. Rewrite it using only the activity data."
            reply = self.text_of(self.call(self.m.get_msgs(), system=system, usage=usage))
        data["selection"], data["reply"] = shown, reply
        return reply

    def step_calendar(self, task, data, usage=None):
        """
        Calendar (only offered after the student approves, see available()): removes what the plan
        replaces, adds the plan, and checks both by reading the calendar back. Returns the scheduler's summary.
        """
        plan = self.m.read_note("plan")
        removed = self.s.remove_from_calendar(self.m.read_note("replaces") or [], self.calendar)
        results = self.s.add_to_calendar(plan, self.calendar)
        self.m.write_note("plan", None)  # nothing pending anymore -> calendar no longer offered
        self.m.write_note("replaces", None)
        data["calendar_results"] = results
        data["added"] = data["selection"] = [r["activity"] for r in results if r["verified"]]
        return self.s.summarize_added(results, removed)

    # register for the approved plan, retrying a failed try once (never claims a registration that didn't happen)
    def step_register(self, task, data, usage=None, retries=1):
        items = self.m.read_note("plan") or data.get("added") or []
        data["registration"] = []
        data.setdefault("selection", items)
        lines = []
        for a in items:
            result, error = None, None
            for attempt in range(retries + 1):
                try:
                    result = self.reg.register(a)
                    break
                except RegistrationError as e:
                    error = str(e)
            data["registration"].append({"activity": a, "result": result, "error": None if result else error})
            if result and result["status"] == "registered":
                lines.append(f"{a['title']}: registered, confirmation {result['confirmation']}"
                             + (" (after a failed first attempt)" if error else ""))
            elif result:
                lines.append(f"{a['title']}: NOT registered, the student must register at {result.get('url') or 'the event page'}")
            else:
                lines.append(f"{a['title']}: registration FAILED after {retries + 1} attempts ({error})")
        self.log("step_register", results=lines)
        return "registration: " + "; ".join(lines)

    def run(self, user_input, usage=None):
        """
        One turn of the supervisor loop (following ScheduleMe): memory takes the message, then the
        router picks the next action until the agent talks to the student or finishes.

        Args:
            user_input (str): the student's message (task query, runtime update, or approval reply)
            usage (dict): optional token counter, see Utils.call

        Returns:
            (str, list[dict]): the reply, and the activities shown/added this turn (graded in eval)
        """
        self.m.add_message({"role": "user", "content": user_input}, usage)  # resolves contradictions + manages context
        pending = self.m.read_note("plan")
        approved = bool(pending) and self.check_approval(user_input, pending, usage)
        data, results = {}, []
        if pending:
            results.append(("plan APPROVED by the student: " if approved else "proposed plan, NOT approved by this message: ")
                           + "; ".join(f"{a['title']} ({a['date']} {a['start']})" for a in pending))
        subagents = {"retriever": self.step_retriever, "recommender": self.step_recommender,
                     "scheduler": self.step_scheduler, "calendar": self.step_calendar,
                     "register": self.step_register}

        for _ in range(MAX_STEPS):
            allowed = self.available(data, approved)
            name = self.route(user_input, results, usage, allowed)["name"]
            if name not in {s["name"] for s in allowed}:
                self.log("route_rejected", subagent=name)  # e.g. calendar without approval: never run it
                name = "user"
            if name in ("user", "FINISH"):
                break
            try:
                results.append(subagents[name](user_input, data, usage))
            except Exception as e:
                results.append(f"{name}: error: {e}")  # the router sees it and can choose something else
        reply = self.step_user(user_input, data, results, usage)  # every turn ends by talking to the student

        if pending is not None and self.m.read_note("plan") is pending:
            self.m.write_note("plan", None)  # not added and nothing new proposed: approval must come right after a proposal
            self.m.write_note("replaces", None)
        self.m.add_message({"role": "assistant", "content": reply}, usage)
        if self.memory_path:
            self.m.save(self.memory_path)
        self.log("run", results=results, selection=[a["title"] for a in data.get("selection", [])])
        return reply, data.get("selection", [])
