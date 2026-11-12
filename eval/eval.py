# Evaluation pipeline: full_agent vs. no_compact vs. single_pass (docs/agent.md, Experimental Setup).
#
# eval_instance = one (profile, task) pair from the cross product profiles x tasks, limited to each
# task's "profiles" in eval/benchmark-tasks.json. Every agent/ablation runs every eval_instance.

import json
import os
import random
from datetime import datetime
from pathlib import Path

from agent.agent import ECAgent
from agent.calendars import SimulatedCalendar
from agent.registration import SimulatedRegistration
from agent.retriever import Retriever
from scraper import db
from utils import Utils

ROOT = Path(__file__).resolve().parent.parent
TASKS = ROOT / "eval" / "benchmark-tasks.json"
PROFILES = ROOT / "eval" / "profiles" / "simulated-profiles.json"
RESULTS = ROOT / "eval" / "results" / "results.json"
FIXTURES = ROOT / "eval" / "fixtures"         # <task>.json: activities + answer key (written before any run)
FIXTURE_DBS = ROOT / "data" / "fixtures"      # <task>.db: built by python -m eval.fixtures.build_fixtures
TODAY = "2026-11-16"  # the fixtures' reference week (2026-11-16 to 2026-11-22)
AGENTS = ["full_agent", "no_compact", "single_pass"]

# checks that read the reply text: graded blind in eval/manual_grading.py, per trial. Everything about the
# picks (relevance, dislikes, constraints, preferences) is automatic, from the fixture's answer key.
# (no_superseded_facts is both: picks vs. the key's superseded_traps here, the reply text by a person)
MANUAL_CHECKS = {"reply_matches_calendar", "no_superseded_facts"}
REPLY_CHECKS = MANUAL_CHECKS
NOTHING_FITS = "nothing_fits_justified"  # manual, per trial, only for empty picks
# required_actions: a separate full_agent-only score, not compared to the baselines (not in pass^3)
ACTION_CHECKS = {"events_added", "planned_event_removed", "registered", "no_overlap_between_added"}
DEFAULT_MINUTES = 60  # length assumed for events with only a start time (same as the profile validator)


def mins(t):
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def manual_checks(trial, task, truth):
    """
    The manual checks one trial needs, in task order (used by eval/manual_grading.py and pass_k).
    Empty picks also get nothing_fits_justified (the automatic pick checks don't apply to them).
    """
    checks = [c for c in task["checks"] if c in MANUAL_CHECKS]
    return ([NOTHING_FITS] + checks) if not trial.get("picked") else checks


class Evaluator(Utils):
    def __init__(self, description, trials=3, api_key=None, seed=0):
        """
        Initialize the evaluator with
            desc: description (Args)
            tasks: dict of benchmark tasks [task_id -> task spec: profiles, turns, checks, ...]
            profiles: dict of simulated user profiles [profile_id -> profile]
            trials: number of trials per (agent, eval_instance), for pass^k
            rng: random order of eval_instances (seeded, so a batch can be rerun in the same order)
        Args:
            description (str): description of the changes to the model (clearly know relationship
                               between results + changes)
            trials (int): trials per (agent, eval_instance), default 3 (pass^3)
            seed (int): seed for the eval_instance order
        """
        super().__init__(api_key)
        self.desc = description
        self.trials = trials
        self.rng = random.Random(seed)
        with open(TASKS) as f:
            self.tasks = json.load(f)
        with open(PROFILES) as f:
            self.profiles = json.load(f)
        self.fixtures = {}  # task_id -> fixture (activities + answer key), one small clean DB per task
        for task_id in self.tasks:
            with open(FIXTURES / f"{task_id}.json") as f:
                self.fixtures[task_id] = json.load(f)
            if not self.db_path(task_id).exists():
                raise SystemExit(f"Missing {self.db_path(task_id)}: run python -m eval.fixtures.build_fixtures")

    @staticmethod
    def db_path(task_id):
        return FIXTURE_DBS / f"{task_id}.db"

    def answer_key(self, task_id, profile_id):
        """The fixture's answer key for one eval_instance (grader only, never shown to the agent)."""
        return self.fixtures[task_id]["answer_key"][profile_id]

    def starting_events(self, task_id, profile_id):
        """Activities already on the student's calendar when the trial starts (adapt-schedule's planned activity)."""
        return [dict(e) for e in self.answer_key(task_id, profile_id).get("starting_calendar", [])]

    def make_eval_instance(self, agent_name, profile_id, task_id):
        """Fresh agent + calendar + registration for one trial of (profile, task): nothing leaks between runs."""
        calendar = SimulatedCalendar(events=self.starting_events(task_id, profile_id))
        registration = SimulatedRegistration(fail_on={1} if task_id == "multi-step" else ())
        agent = ECAgent(api_key=self.key, no_compact=(agent_name == "no_compact"),
                        single_pass=(agent_name == "single_pass"), db_path=self.db_path(task_id), today=TODAY,
                        calendar=calendar, registration=registration,
                        use_web=False)  # web search not tested in the eval (docs/design-decisions.md)
        return agent, calendar, registration

    # run agent on one eval_instance (profile, task) for one trial, with the task's scripted turns
    # (same for every agent/ablation). returns the per-trial record (results.json)
    def run_trial(self, agent_name, profile_id, task_id, trial):
        profile = self.profiles[profile_id]
        visible = profile["agent_visible"]  # never the whole profile: eval_only is the answer key
        agent, calendar, registration = self.make_eval_instance(agent_name, profile_id, task_id)
        usage = {}
        record = {"agent": agent_name, "profile": profile_id, "task": task_id, "trial": trial,
                  "turns": [], "picked": [], "error": None}
        try:
            agent.load(visible["conversation_history"], visible["intake_profile"],
                       run_id=f"{agent_name}/{profile_id}/{task_id}/trial{trial}", usage=usage)
            for turn in self.tasks[task_id]["turns"]:
                message = self.turn_message(turn, profile, task_id)
                reply, selection = agent.run(message, usage)
                record["turns"].append({"user": message, "reply": reply, "selection": selection})
                if selection:  # graded on the final selection: the last turn that showed/added activities
                    record["picked"] = selection
        except Exception as e:  # one failed trial shouldn't stop the batch, it's graded as a fail
            record["error"] = f"{type(e).__name__}: {e}"
        record.update(query=record["turns"][0]["user"] if record["turns"] else None,
                      reply=record["turns"][-1]["reply"] if record["turns"] else None,
                      calendar=calendar.events, registered=list(registration.registered.values()),
                      usage=usage, auto_grade=None, quality=None, notes="")
        return record

    def turn_message(self, turn, profile, task_id):
        """Scripted user message: a runtime update's message (by id), or the task text filled in for this profile."""
        if "runtime_update" in turn:
            update = next(u for u in profile["runtime_updates"] if u["id"] == turn["runtime_update"])
            return update["message"]["content"]  # only the message reaches the agent
        return turn["content"].format_map(profile["task_fills"].get(task_id, {}))

    # the fixture row for a picked activity (common format), or None if it isn't in the task's fixture.
    # picks are graded on DB data, never on the agent's own description (lookup from mirkp's early_eval.py)
    # (retriever only has keyword search, so this queries by id with scraper/db.py's query helper)
    def lookup(self, a, task_id):
        if a.get("kind") == "event":
            rows = db._query("SELECT * FROM events WHERE event_id = ? AND date = ?", (a.get("id"), a.get("date")),
                             self.db_path(task_id))
        else:
            rows = db._query("SELECT * FROM clubs WHERE club_id = ?", (a.get("id"),), self.db_path(task_id))
        return Retriever.to_common(rows[0]) if rows else None

    @staticmethod
    def key_id(a):
        """Answer-key id of an activity: event:<id> / club:<id>."""
        return f"{a['kind']}:{a['id']}"

    def span(self, e):
        """(start, end) in minutes, or None if the event has no start time (can't be checked -> doesn't fit)."""
        if not e.get("start"):
            return None
        start = mins(e["start"])
        return start, mins(e["end"]) if e.get("end") else start + DEFAULT_MINUTES

    def fits(self, e, windows, extra_busy=()):
        """
        Inside one available window on its date (busy blocks, buffers, earliest/latest already applied by the
        profile generator), and not overlapping extra busy time (runtime updates). Uses the profile's precomputed
        windows, not the agent's scheduler, so the agent isn't graded by its own code.
        """
        s = self.span(e)
        return s is not None and \
            any(w["date"] == e["date"] and mins(w["start"]) <= s[0] and s[1] <= mins(w["end"]) for w in windows) and \
            not any(b["date"] == e["date"] and s[0] < mins(b["end"]) and mins(b["start"]) < s[1] for b in extra_busy)

    # automatic checks: answers the fixture, its answer key and the profile prove. True/False, or None = not
    # applicable (an empty pick -> manual nothing_fits_justified). Same checks for every agent/ablation;
    # action checks (required_actions) are scored separately, for full_agent only (not compared to baselines)
    def auto_grade(self, record):
        profile = self.profiles[record["profile"]]
        ev = profile["eval_only"]
        key = self.answer_key(record["task"], record["profile"])
        picked = [self.lookup(a, record["task"]) for a in record["picked"]]  # fixture rows, never the agent's text
        ids = [self.key_id(p) for p in picked if p]
        events = [p for p in picked if p and p["kind"] == "event"]
        start_ids = {e["id"] for e in self.starting_events(record["task"], record["profile"])}
        added = [e for e in record["calendar"] if e["id"] not in start_ids]
        caps = {h["type"]: h["value"] for h in ev["current_truth"]["hard_constraints"] if "value" in h}
        updates = [u for u in profile["runtime_updates"] if record["task"] in u["for_tasks"]]
        update_busy = [b for u in updates for b in ev["runtime_update_effects"][u["id"]].get("adds_busy", [])]
        spans = [s for s in map(self.span, events) if s]
        # weekly caps count the planned activities still on the calendar too (adapt-schedule keeps one)
        kept = [e for e in record["calendar"] if e["id"] in start_ids and self.key_id(e) not in ids]
        kept_spans = [s for s in map(self.span, kept) if s]
        hours = sum((s[1] - s[0]) / 60 for s in spans + kept_spans)
        restrictions = key.get("restrictions", {})
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

        checks, actions = {}, {}
        for name in self.tasks[record["task"]]["checks"]:
            base, _, n = name.partition(":")
            if base in ACTION_CHECKS:
                if record["agent"] != "full_agent" or not record["picked"]:
                    actions[name] = None  # full_agent-only score; an empty pick has nothing to add
                elif base == "events_added":
                    actions[name] = len(added) == int(n)
                elif base == "no_overlap_between_added":
                    added_spans = sorted((e["date"], *self.span(e)) for e in added if self.span(e))
                    actions[name] = all(a[0] != b[0] or a[2] <= b[1] for a, b in zip(added_spans, added_spans[1:]))
                elif base == "registered":  # a confirmation for each picked event (the retry isn't checked yet)
                    actions[name] = len(record["registered"]) >= len(events) > 0
                elif base == "planned_event_removed":  # planned activities that clash with the update are off the
                    # calendar; the ones that still fit stay (removing those isn't asked for)
                    clashing = {e["id"] for e in self.starting_events(record["task"], record["profile"])
                                if not self.fits(e, ev["available_windows"], update_busy)}
                    on_calendar = {e["id"] for e in record["calendar"]}
                    actions[name] = bool(clashing) and not clashing & on_calendar and \
                        (start_ids - clashing) <= on_calendar
            elif base in MANUAL_CHECKS and base != "no_superseded_facts":
                continue  # reply text: eval/manual_grading.py
            elif not record["picked"]:
                checks[name] = None
            elif base == "exists":
                checks[name] = all(p is not None for p in picked)
            elif base == "is_event":
                checks[name] = all(p and p["kind"] == "event" for p in picked)
            elif base == "club_or_fitting_event":
                checks[name] = all(p and (p["kind"] == "club" or self.fits(p, ev["available_windows"])) for p in picked)
            elif base == "fits_schedule":
                checks[name] = bool(events) and all(self.fits(e, ev["available_windows"]) for e in events)
            elif base == "fits_schedule_after_update":
                checks[name] = bool(events) and all(self.fits(e, ev["available_windows"], update_busy) for e in events)
            elif base == "hard_constraints":  # caps + what the answer key says each activity breaks
                n = len(events) + len(kept)
                checks[name] = n <= caps.get("max_activities_per_week", n) and \
                    hours <= caps.get("max_hours_per_week", hours) and \
                    all((s[1] - s[0]) / 60 <= caps.get("max_event_duration_hours", 24) for s in spans) and \
                    not any(i in key["breaks"] for i in ids)
            elif base == "interest":  # multiple-schedule splits relevant by requested interest
                relevant = key["relevant"]
                relevant = [i for g in relevant.values() for i in g] if isinstance(relevant, dict) else relevant
                checks[name] = bool(ids) and all(i in relevant for i in ids)
            elif base == "no_avoid":
                checks[name] = not any(i in key["dislike"] for i in ids)
            elif base in ("best_ranked_preference", "one_per_interest"):
                # unique by construction (checked by build_fixtures): the pick must be the reference solution
                checks[name] = sorted(ids) == sorted(key["reference_solution"])
            elif base == "picked_from_overlap_set":
                checks[name] = bool(ids) and all(i in key["overlap_set"] for i in ids)
            elif base == "meets_request_restrictions":
                checks[name] = bool(events) and all(
                    e["categories"] == restrictions["event_type"] and
                    days[datetime.fromisoformat(e["date"]).weekday()] == restrictions["weekday"] for e in events)
            elif base == "no_superseded_facts":  # picks only; the reply text is graded manually
                checks[name] = not any(i in key.get("superseded_traps", {}) for i in ids)
        ok = record["error"] is None
        action_pass = None
        if record["agent"] == "full_agent" and any(v is not None for v in actions.values()):
            action_pass = ok and all(v is not False for v in actions.values())
        return {"checks": checks, "pass": ok and all(v is not False for v in checks.values()),
                "action_checks": actions, "action_pass": action_pass}

    def trial_pass(self, record):
        """
        One trial: automatic checks pass AND every manual label is yes.
        None = not decided yet (not auto-graded, or a manual label still missing).
        """
        if record.get("auto_grade") is None:
            return None
        truth = self.profiles[record["profile"]]["eval_only"]["current_truth"]
        labels = [(record.get("manual") or {}).get(c) for c in manual_checks(record, self.tasks[record["task"]], truth)]
        if not record["auto_grade"]["pass"] or False in labels:
            return False
        return None if None in labels else True

    # pass^k per eval_instance: every trial of (agent, profile, task) passes (reliability over creativity)
    # returns {agent: {"instances": {"profile/task": True/False/None}, "pass_k": mean over decided, "pending": n}}
    def pass_k(self, records):
        by_instance = {}
        for r in records:
            by_instance.setdefault((r["agent"], f"{r['profile']}/{r['task']}"), []).append(self.trial_pass(r))
        results = {}
        for (agent, instance), trials in by_instance.items():
            value = False if False in trials else (None if None in trials else True)
            results.setdefault(agent, {"instances": {}})["instances"][instance] = value
        for agent, res in results.items():
            decided = [v for v in res["instances"].values() if v is not None]
            res["pass_k"] = sum(decided) / len(decided) if decided else None
            res["pending"] = len(res["instances"]) - len(decided)
        return results

    # full_agent only, not compared to baselines: pass^k of the required_actions (calendar, registration)
    def action_pass_k(self, records):
        by_instance = {}
        for r in records:
            if r["agent"] == "full_agent" and r.get("auto_grade") and r["auto_grade"]["action_pass"] is not None:
                by_instance.setdefault(f"{r['profile']}/{r['task']}", []).append(r["auto_grade"]["action_pass"])
        instances = {i: all(trials) for i, trials in by_instance.items()}
        return {"instances": instances, "pass_k": sum(instances.values()) / len(instances) if instances else None}

    # overall pass/fail (docs/agent.md, step 7): full_agent's average pass^3 per instance is at least 10 percentage
    # points higher than both baselines', and its mean per-trial quality is >= 4 with standard deviation <= 0.5.
    # None while trials are still undecided (manual labels / quality missing), never a guess
    def report(self, records):
        pk = self.pass_k(records)
        scores = [r["quality"] for r in records if r["agent"] == "full_agent" and r.get("quality") is not None]
        n_full = sum(r["agent"] == "full_agent" for r in records)
        mean = sum(scores) / len(scores) if scores else None
        sd = (sum((q - mean) ** 2 for q in scores) / len(scores)) ** 0.5 if scores else None
        rates = {a: (pk.get(a) or {}).get("pass_k") for a in AGENTS}
        pending = sum((pk.get(a) or {}).get("pending", 0) for a in AGENTS) + (n_full - len(scores))
        overall = None
        if not pending and None not in rates.values():
            beats = all(rates["full_agent"] - rates[b] >= 0.10 for b in ("no_compact", "single_pass"))
            overall = beats and mean >= 4 and sd <= 0.5
        return {"pass_overall": overall, "pass^3": rates, "quality": {"mean": mean, "sd": sd, "n": len(scores)},
                "pending": pending, "per_instance": {a: (pk.get(a) or {}).get("instances") for a in AGENTS},
                "action_pass^3": self.action_pass_k(records)}

    # save the results to json: append this run's batch to RESULTS (oldest -> newest), as eval/manual_grading.py reads it
    def save_results(self, batch, path=RESULTS):
        batches = []
        if Path(path).exists() and Path(path).stat().st_size:  # results.json starts out empty
            with open(path) as f:
                batches = json.load(f)
        batches = [b for b in batches if b["batch_id"] != batch["batch_id"]] + [batch]  # replace if saved before
        # write to a temp file first so an interrupt mid-write can't corrupt results.json
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(str(path) + ".tmp")
        with open(tmp, "w") as f:
            json.dump(batches, f, indent=1)
        os.replace(tmp, path)

    def run(self, path=RESULTS):
        """
        docs/agent.md step 5: for each agent/ablation, eval_instances in random order, self.trials trials each.
        Every trial is auto-graded and saved right away to path (a crash keeps the finished trials).
        """
        instances = [(p, t) for t, spec in self.tasks.items() for p in spec["profiles"]]
        batch = {"batch_id": datetime.now().isoformat(timespec="seconds"), "description": self.desc, "trials": []}
        for agent_name in AGENTS:
            order = list(instances)
            self.rng.shuffle(order)
            for profile_id, task_id in order:
                for trial in range(1, self.trials + 1):
                    record = self.run_trial(agent_name, profile_id, task_id, trial)
                    record["auto_grade"] = self.auto_grade(record)
                    batch["trials"].append(record)
                    self.save_results(batch, path)
        return batch
