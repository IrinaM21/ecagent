# Manual grading for the last batch in eval/results/results.json: yes/no labels for the checks that read the
# reply (MANUAL_CHECKS / manual_checks in eval/eval.py) + quality (1-5), for all three agents. Everything about
# the picks (relevance, dislikes, constraints) is graded automatically from the fixture answer keys.
#
# Run from the repo root (no flags, it grades whatever is left in the last batch):
#   python -m eval.manual_grading
#
# Shuffled (blind): the agent name, trial numbers and automatic scores are never shown, and trials of all
# three agents are shuffled together with a fixed seed. Labels are per trial (they read that trial's reply).
#
# Expected results.json layout (written by eval/eval.py):
#   [ {"batch_id": str, "description": str, "trials": [record, ...], ...}, ... ]   # oldest -> newest
# where each record looks like:
#   {"agent", "profile", "task", "trial", "query", "turns", "reply", "picked", "calendar", "auto_grade",
#    "manual", "quality", "notes", ...}
#
# Scores are written back into results.json after every trial, so quitting midway keeps your work.

import json
import os
import random
from pathlib import Path

from eval.eval import PROFILES, TASKS, Evaluator, manual_checks

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "eval" / "results" / "results.json"
SEED = 0  # fixed shuffle, so the order is the same every session

RUBRIC = """Rubric (answer y/n; judge against the student's CURRENT situation shown above each trial):
  reply_matches_calendar      the reply's claims about what was added/removed match the final calendar shown
  no_superseded_facts         the reply doesn't use any of the outdated facts listed (old value)
  nothing_fits_justified      (no activities picked) the reply says nothing fits this week AND gives a sound
                              reason; alternatives such as clubs are fine
Quality 1-5: how useful the reply is to this student overall."""


def load_batches(path):
    with open(path) as f:
        batches = json.load(f)
    if not batches:
        raise SystemExit(f"No batches in {path}, run the eval first")
    return batches


def save_batches(batches, path):
    # write to a temp file first so an interrupt mid-write can't corrupt results.json
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w") as f:
        json.dump(batches, f, indent=1)
    os.replace(tmp, path)


def trials_to_grade(batch):
    """All agents' trials in the batch that still need a label or a score, shuffled (blind)."""
    trials = [t for t in batch["trials"]
              if t.get("quality") is None or None in (t.get("manual") or {None: None}).values()]
    random.Random(SEED).shuffle(trials)
    return trials


def show_activities(picked):
    for a in picked:
        print("  " + json.dumps({k: a.get(k) for k in ("kind", "title", "date", "start", "end", "location",
                                                      "categories", "description", "url", "source")}))


def show(trial, profile, task, n, total):
    """What the grader needs: the student's current situation, the scripted turns, the reply, the picks.
    Never the agent name, trial number or automatic scores (blind)."""
    truth = profile["eval_only"]["current_truth"]
    print("\n" + "=" * 80)
    print(f"[{n}/{total}]  {trial['profile']} / {trial['task']}")
    print("=" * 80)
    print("interests:", "; ".join(truth["interests"]))
    print("dislikes: ", "; ".join(truth["dislikes"]) or "(none)")
    print("ranked preferences:", "; ".join(f"{s['rank']}. {s['preference']}" for s in truth["soft_preferences"]))
    if task.get("request_restrictions"):
        print("request restrictions:", task["request_restrictions"])
    for turn in trial.get("turns") or []:
        print(f"\nstudent: {turn['user']}\nreply:   {turn['reply'] or '(empty)'}")
    if trial.get("error"):
        print(f"\n(trial ended with an error: {trial['error']})")
    print("\npicked:" if trial.get("picked") else "\npicked: (no activities)")
    show_activities(trial.get("picked") or [])
    if trial.get("quality") is not None:
        print(f"\ncurrent score: {trial['quality']}  notes: {trial.get('notes') or ''}")


def check_context(check, trial, profile):
    """Extra info shown only for the check being asked."""
    if check == "reply_matches_calendar":
        return {"calendar": trial.get("calendar") or []}
    if check == "no_superseded_facts":
        return {"superseded_facts": [{"old": f["old"], "new": f["new"]} for f in profile["eval_only"]["superseded_facts"]]}
    return {}


def show_check_context(context):
    for name, value in context.items():
        print(f"  {name}:")
        for item in value or ["(none)"]:
            print("    " + (item if isinstance(item, str) else json.dumps(item)))


def ask_yes_no(prompt):
    """Returns True/False, 's' to skip, or 'q' to quit."""
    while True:
        answer = input(f"{prompt} (y/n, s = skip, q = save and quit): ").strip().lower()
        if answer in {"y", "n"}:
            return answer == "y"
        if answer in {"s", "q"}:
            return answer
        print("Enter y, n, s, or q.")


def ask_score():
    """Returns an int 1-5, 's' to skip, or 'q' to quit."""
    while True:
        answer = input("\nquality 1-5 (s = skip, q = save and quit): ").strip().lower()
        if answer in {"s", "q"}:
            return answer
        if answer in {"1", "2", "3", "4", "5"}:
            return int(answer)
        print("Enter a whole number from 1 to 5, s, or q.")


def grade_trial(trial, profile, task):
    """Fill trial['manual'] (one yes/no per check, None if skipped). Returns 'q' to stop, else None."""
    manual = trial.setdefault("manual", {})
    for check in manual_checks(trial, task, profile["eval_only"]["current_truth"]):
        if manual.get(check) is not None:
            continue
        show_check_context(check_context(check, trial, profile))
        answer = ask_yes_no(check)
        if answer == "q":
            return "q"
        manual[check] = None if answer == "s" else answer
    return None


def main():
    batches = load_batches(RESULTS)
    batch = batches[-1]
    print(f"Batch {batch.get('batch_id')}: {batch.get('description', '')}")
    trials = trials_to_grade(batch)
    if not trials:
        print("Nothing left to grade in this batch.")
        return
    with open(PROFILES) as f:
        profiles = json.load(f)
    with open(TASKS) as f:
        tasks = json.load(f)
    print(f"{len(trials)} trials to grade (all agents, shuffled).\n\n{RUBRIC}")

    graded = 0
    for n, trial in enumerate(trials, 1):
        profile, task = profiles[trial["profile"]], tasks[trial["task"]]
        show(trial, profile, task, n, len(trials))
        if grade_trial(trial, profile, task) == "q":
            save_batches(batches, RESULTS)
            break
        score = ask_score()
        if score == "q":
            save_batches(batches, RESULTS)
            break
        if score != "s":
            trial["quality"] = score
            trial["notes"] = input("notes (optional): ").strip()
        save_batches(batches, RESULTS)  # trial is a reference into batches, so this saves the labels/score
        graded += 1

    # labels/scores change pass^3 and step 7, so the batch's report is recomputed and saved (no API calls)
    batch["report"] = Evaluator(batch.get("description", ""), api_key="not-needed").report(batch["trials"])
    save_batches(batches, RESULTS)
    remaining = sum(1 for t in batch["trials"] if t.get("quality") is None)
    print(f"\nGraded {graded} trials this session, {remaining} still without a quality score in this batch.")


if __name__ == "__main__":
    main()
