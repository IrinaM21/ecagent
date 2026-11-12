import json
import re

from utils import Utils

# TODO: replaces ECAgent.verify_response. Not called yet because it must be motivated by eval first
# (README TODO). verify_response checks the reply against several sources of error; if any
# check fails it returns False and the agent replans (router) instead of only rewriting the reply.

# hard constraints the activity data has no fields for (cost/fees, alcohol, in-person/virtual, transport)
UNVERIFIABLE_TYPES = {"exclude", "max_cost_usd", "format", "location"}
KEYS = ("title", "kind", "date", "start", "end", "location", "description", "categories", "url")


class Verifier(Utils):
    def __init__(self, api_key=None):
        """
        Response check subagent with its own context: every check is a separate model call that only
        sees what it needs (the reply, the activities, the request, the student's state), never the
        agent's conversation. The agent's code passes the data in, so no module calls or holds another.
        """
        super().__init__(api_key)

    @staticmethod
    def listing(activities):
        return "\n".join(f"{i}. {json.dumps({k: a.get(k) for k in KEYS})}" for i, a in enumerate(activities, 1))

    def ask_json(self, prompt, step, fallback, max_tokens=1024, usage=None):
        """One model call that returns JSON; fallback if the answer can't be parsed (logged)."""
        text = self.text_of(self.call([{"role": "user", "content": prompt}], max_tokens=max_tokens, usage=usage))
        try:
            return json.loads(re.search(r"[\[{].*[\]}]", text, re.S).group())
        except (AttributeError, json.JSONDecodeError):
            self.log(step, error="could not parse answer", answer=text[:500])
            return fallback

    def verify_response(self, response, activities, request, state, schedule_conflicts=None, tool_results=(),
                        usage=None):
        """
        Check the reply before it is shown to the student. Each helper covers one source of error.

        Args:
            response (str): the reply about to be sent
            activities (list[dict]): the activities the reply is about (common format)
            request (str): the student's original request this turn
            state (dict): memory's 'state' note (current interests, dislikes, hard_constraints)
            schedule_conflicts (list[str] | None): Scheduler.check_plan output for these activities,
                                                   run by the agent's code; None if they aren't a plan
            tool_results (list[str]): this turn's subagent summaries, to check "I added it" claims
            usage (dict): optional token counter, see Utils.call

        Returns:
            dict: {"ok": bool (False -> replan), "failures": [{"check", "explanation"}]}
        """
        checks = {
            "grounded": self.check_grounded(response, activities, schedule_conflicts is not None, tool_results, usage),
            "interests": self.check_interests(activities, state, usage),
            "schedule": self.check_schedule(activities, schedule_conflicts),
            "request": self.check_request(response, activities, request, usage),
        }
        failures = [{"check": name, "explanation": c["explanation"]} for name, c in checks.items() if not c["ok"]]
        self.log("verify_response", ok=not failures, failures=failures)
        return {"ok": not failures, "failures": failures}

    def check_grounded(self, response, activities, schedule_checked, tool_results=(), usage=None):
        """Every claim in the reply is supported by the activity data and this turn's actions (no made-up details)."""
        sources = {"activities": [{k: a.get(k) for k in KEYS} for a in activities],
                   "schedule_check": "passed: these activities fit the student's schedule" if schedule_checked
                                     else "not checked: do not claim these fit the student's schedule",
                   "actions_this_turn": list(tool_results)}
        prompt = f"""You are a fact checker for a student extracurricular planner. You check a reply before it is shown to the student.

        Verify if the response is supported by the source documents. Every claim about an activity (time, place, cost, what it involves, whether it fits the schedule) must be supported by the sources, and every claim that something was added, removed or registered must be supported by actions_this_turn.

        <response>{response}</response>
        <sources>{json.dumps(sources)}</sources>

        Return only JSON with these fields: ok (boolean), explanation (string)"""
        return self.ask_json(prompt, "check_grounded", {"ok": False, "explanation": "something went wrong"}, usage=usage)

    # LLM check of what code can't check: the student's likes and dislikes, and the hard constraints the
    # activity data has no fields for (UNVERIFIABLE_TYPES).
    def check_interests(self, activities, state, usage=None):
        """Each activity fits the student's likes, isn't a dislike, and doesn't break a constraint code can't check."""
        if not activities:
            return {"ok": True, "explanation": ""}
        rules = [h for h in state.get("hard_constraints", []) if h["type"] in UNVERIFIABLE_TYPES]
        prompt = f"""You are a careful reviewer for a student extracurricular planner. You check activities before they are recommended to a student.

        For each activity, decide from its data only:
        - verdict: "breaks" if the data shows it breaks one of the hard constraints or is one of the student's dislikes (e.g. a $40 fee over a $25 cap, a virtual event for in-person only, a chess tournament for someone who dislikes competitive chess), "ok" if the data shows it doesn't, "unknown" if the data doesn't say
        - matches_likes: true if it fits at least one of the student's likes

        <likes>{json.dumps(state.get("interests", []))}</likes>
        <dislikes>{json.dumps(state.get("dislikes", []))}</dislikes>
        <hard_constraints>{json.dumps(rules)}</hard_constraints>
        <activities>
        {self.listing(activities)}
        </activities>

        Return only JSON: a list with one object per activity: {{"n": activity number, "verdict": "breaks"|"ok"|"unknown", "matches_likes": boolean, "reason": string}}"""
        verdicts = self.ask_json(prompt, "check_interests", [], max_tokens=2048, usage=usage)
        bad = [v for v in verdicts if isinstance(v, dict) and (v.get("verdict") == "breaks" or v.get("matches_likes") is False)]
        explanation = " ".join(f"{activities[v['n'] - 1]['title']}: {v.get('reason')}." for v in bad
                               if isinstance(v.get("n"), int) and 1 <= v["n"] <= len(activities))
        return {"ok": not bad, "explanation": explanation}

    @staticmethod
    def check_schedule(activities, schedule_conflicts):
        """
        Hard constraints related to the schedule (free time, buffers, earliest/latest, caps), checked in code:
        the scheduler's check_plan output, passed in by the agent. Not a plan (None) -> nothing to check.
        """
        if schedule_conflicts is None or not activities:
            return {"ok": True, "explanation": ""}
        return {"ok": not schedule_conflicts, "explanation": "; ".join(schedule_conflicts)}

    def check_request(self, response, activities, request, usage=None):
        """The reply and its activities do what the student asked (number, day, type, topic, actions)."""
        prompt = f"""You are a reviewer for a student extracurricular planner. You check that a reply answers what the student asked before it is shown to them.

        Compare the reply and its activities to the student's request. Check that they match what was asked: the number of activities, the day or time, the kind of activity or topic, and any action asked for (adding to the calendar, registering). Asking the student a needed question, or saying honestly that nothing fits and why, counts as answering the request.

        <request>{request}</request>
        <response>{response}</response>
        <activities>
        {self.listing(activities)}
        </activities>

        Return only JSON with these fields: ok (boolean), explanation (string)"""
        return self.ask_json(prompt, "check_request", {"ok": False, "explanation": "something went wrong"}, usage=usage)
