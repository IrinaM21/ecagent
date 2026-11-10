# Manual quality grading (1-5) for the last batch of full_agent trials in eval/results.json.
#
# Run from the repo root:
#   python -m eval.manual_grading                 # grade ungraded full_agent trials in the last batch
#   python -m eval.manual_grading --regrade       # also revisit trials that already have a score
#
# Expected results.json layout (written by eval/eval.py):
#   [ {"batch_id": str, "description": str, "trials": [record, ...], ...}, ... ]   # oldest -> newest
# where each record looks like:
#   {"system", "profile", "task", "trial", "query", "reply", "picked", "auto_grade", "quality", "notes"}
#
# Scores are written back into results.json after every trial, so quitting midway keeps your work.

import argparse
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "eval" / "results.json"
SYSTEM = "full_agent"
HIDDEN = {"reply", "quality", "notes"}  # shown separately / filled in by the grader


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


def trials_to_grade(batch, regrade=False):
    """full_agent trials in the batch, grouped by profile/task with trials in order."""
    trials = [t for t in batch["trials"] if t["system"] == SYSTEM]
    if not regrade:
        trials = [t for t in trials if t.get("quality") is None]
    return sorted(trials, key=lambda t: (t["profile"], t["task"], t["trial"]))


def show(trial, n, total, trials_per_task):
    print("\n" + "=" * 80)
    print(f"[{n}/{total}]  {trial['profile']} / {trial['task']}  --  trial {trial['trial']}/{trials_per_task}")
    print("=" * 80)
    print(json.dumps({k: v for k, v in trial.items() if k not in HIDDEN}, indent=1))
    print("\nreply:\n" + (trial.get("reply") or "(empty)"))
    if trial.get("quality") is not None:
        print(f"\ncurrent score: {trial['quality']}  notes: {trial.get('notes') or ''}")


def ask_score():
    """Returns an int 1-5, 's' to skip, or 'q' to quit."""
    while True:
        answer = input("\nquality 1-5 (s = skip, q = save and quit): ").strip().lower()
        if answer in {"s", "q"}:
            return answer
        if answer in {"1", "2", "3", "4", "5"}:
            return int(answer)
        print("Enter a whole number from 1 to 5, s, or q.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", default=str(RESULTS))
    parser.add_argument("--regrade", action="store_true", help="include trials that already have a score")
    args = parser.parse_args()

    batches = load_batches(args.results)
    batch = batches[-1]
    trials = trials_to_grade(batch, args.regrade)
    if not trials:
        print(f"Nothing to grade in batch {batch.get('batch_id')} (use --regrade to revisit scores).")
        return
    trials_per_task = max(t["trial"] for t in batch["trials"] if t["system"] == SYSTEM)
    print(f"Batch {batch.get('batch_id')}: {batch.get('description', '')}")
    print(f"{len(trials)} {SYSTEM} trials to grade.")

    graded = 0
    for n, trial in enumerate(trials, 1):
        show(trial, n, len(trials), trials_per_task)
        score = ask_score()
        if score == "q":
            break
        if score == "s":
            continue
        trial["quality"] = score
        trial["notes"] = input("notes (optional): ").strip()
        save_batches(batches, args.results)  # trial is a reference into batches, so this saves the score
        graded += 1

    remaining = sum(1 for t in batch["trials"] if t["system"] == SYSTEM and t.get("quality") is None)
    print(f"\nGraded {graded} trials this session, {remaining} still ungraded in this batch.")


if __name__ == "__main__":
    main()
