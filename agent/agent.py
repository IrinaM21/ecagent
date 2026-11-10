import json
import re
from datetime import date

from agent.compactor import Compactor
from agent.memory import Memory
from agent.recommender import Recommender
from agent.retriever import Retriever
from agent.scheduler import Scheduler, SimulatedCalendar
from scraper import db
from utils import Utils

MAX_ITEMS = 5  # cap on activities per plan; the scheduler's LLM picks the number from the request
MAX_STEPS = 8  # router decisions per turn, so a confused router can't loop forever


class ECAgent(Utils):
    def __init__(self, api_key=None, no_compact=False, db_path=db.DB_PATH, today=None, calendar=None,
                 memory_path=None):
        """
        Main agent: interprets the query, routes to the modules, validates the result.
        Create one per eval run so memory and the calendar start fresh (no leaks between runs).

        Args:
            no_compact (bool): ablation, memory gets no compactor (no contradiction step, naive truncation)
            db_path: events database (eval passes the frozen snapshot)
            today (str): YYYY-MM-DD, pinned to 2026-11-02 in eval
            calendar: object with get_events/add_event (SimulatedCalendar in eval, GoogleCalendar later)
            memory_path: JSON file memory is saved to after each turn (None in eval)
        """
        super().__init__(api_key)
        self.today = today
        self.memory_path = memory_path
        self.m = Memory(compactor=None if no_compact else Compactor(api_key), api_key=api_key)
        self.r = Retriever(api_key, db_path, today)
        self.rec = Recommender(api_key)
        self.s = Scheduler(api_key, today)
        self.calendar = calendar or SimulatedCalendar(api_key)

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
        for module in (self, self.m, self.m.c, self.r, self.rec, self.s, self.calendar):
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

    def get_subagents(self):
        """
        Next-action options for the router (supervisor). The first is the fallback (ask rather than guess).
        'calendar' is only offered when a proposed plan is pending, so nothing is added without approval.
        """
        return [
            {"name": "user", "description": "ask the student a question, or show results / a plan and ask for approval"},
            {"name": "retriever", "description": "find activities (campus events and student clubs) for the request"},
            {"name": "recommender", "description": "rank the activities found so far (or the candidate plans) by the student's preferences"},
            {"name": "scheduler", "description": "build candidate plans from the found activities that fit the student's week"},
            {"name": "calendar", "description": "add the proposed plan to the student's calendar (only if they said yes to it)"},
            {"name": "FINISH", "description": "the request is answered"},
        ]

    def route(self, task, results, usage=None):
        """
        Router (supervisor): choose the next action from the task, the results so far, and the
        student's latest input. Falls back to the first option ("user") if no valid name is found.

        Args:
            task (str): the student's latest message
            results (list[str]): summaries returned by the subagents so far this turn (see run)
            usage (dict): optional token counter, see Utils.call

        Returns:
            dict: the chosen subagent spec
        """
        subagents = self.get_subagents()
        if self.m.read_note("plan") is None:  # no proposed plan -> nothing to add to the calendar
            subagents = [sub for sub in subagents if sub["name"] != "calendar"]

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

    def step_scheduler(self, task, data, usage=None):
        """Scheduler subagent: valid candidate plans from the found activities. Returns its summary."""
        data["plans"] = self.s.schedule(task, self.m.read_note("state"), data.get("activities", []),
                                        n_items=MAX_ITEMS, existing=self.calendar.get_events(), usage=usage)
        return self.s.summarize(data["plans"])

    def verify_response(self, response, activities, schedule_checked, usage=None):
        """
        Use the LLM to verify that the reply to the student is supported by the activity data
        (no made-up times, costs, or details, and no "fits your schedule" claims that weren't checked).

        Args:
            response (str): the reply about to be sent
            activities (list[dict]): the activities the reply is about
            schedule_checked (bool): True if these activities are a scheduler plan (already passed
                                     check_plan), so schedule claims are allowed
            usage (dict): optional token counter, see Utils.call

        Returns:
            dict: {"is_supported": bool, "confidence": float, "explanation": str}
        """
        keys = ("title", "kind", "date", "start", "end", "location", "description", "url")
        sources = {"activities": [{k: a.get(k) for k in keys} for a in activities],
                   "schedule_check": "passed: these activities fit the student's schedule" if schedule_checked
                                     else "not checked: do not claim these fit the student's schedule"}
        prompt = (f"Verify if the response is supported by the source documents. Here is the response: {response} "
                  f"and here are the source documents: {json.dumps(sources)}. Every claim about an activity "
                  f"(time, place, cost, what it involves, whether it fits the schedule) must be supported by the sources. "
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
        if data.get("added"):
            shown, checked = data["added"], True  # already checked by the scheduler before it was proposed
            task_note = "These activities were just added to the student's calendar. Confirm briefly."
        elif data.get("plans"):
            shown, checked = data["plans"][0], True
            self.m.write_note("plan", shown)  # pending until the student answers
            task_note = "This plan fits the student's schedule. Show it and ask whether to add it to their calendar."
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
        check = self.verify_response(reply, shown, checked, usage) if shown else {"is_supported": True}
        if not check["is_supported"]:
            system += f"\n\nYour previous draft had unsupported claims: {check['explanation']}. Rewrite it using only the activity data."
            reply = self.text_of(self.call(self.m.get_msgs(), system=system, usage=usage))
        data["selection"], data["reply"] = shown, reply
        return reply

    def step_calendar(self, task, data, usage=None):
        """
        Calendar (only offered while a plan is pending, i.e. after the student was asked):
        the scheduler adds the approved plan and the pending note is cleared. Returns the scheduler's summary.
        """
        plan = self.m.read_note("plan")
        confirmations = self.s.add_to_calendar(plan, self.calendar)
        self.m.write_note("plan", None)  # nothing pending anymore -> calendar no longer offered
        data["added"] = data["selection"] = plan
        return self.s.summarize_added(confirmations)

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
        data, results = {}, []
        if pending:
            results.append("proposed plan waiting for the student's answer: "
                           + "; ".join(f"{a['title']} ({a['date']} {a['start']})" for a in pending))
        subagents = {"retriever": self.step_retriever, "recommender": self.step_recommender,
                     "scheduler": self.step_scheduler, "calendar": self.step_calendar}

        for _ in range(MAX_STEPS):
            name = self.route(user_input, results, usage)["name"]
            if name in ("user", "FINISH"):
                break
            try:
                results.append(subagents[name](user_input, data, usage))
            except Exception as e:
                results.append(f"{name}: error: {e}")  # the router sees it and can choose something else
        reply = self.step_user(user_input, data, results, usage)  # every turn ends by talking to the student

        if pending is not None and self.m.read_note("plan") is pending:
            self.m.write_note("plan", None)  # not approved and nothing new proposed: approval must come right after a proposal
        self.m.add_message({"role": "assistant", "content": reply}, usage)
        if self.memory_path:
            self.m.save(self.memory_path)
        self.log("run", results=results, selection=[a["title"] for a in data.get("selection", [])])
        return reply, data.get("selection", [])
