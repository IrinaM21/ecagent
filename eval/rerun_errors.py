# Rerun the trials of a batch that failed for reasons outside the agent (an API error such as the account's
# credit limit), and put the new records in their place in the SAME batch, so the batch is complete.
# Trials the agent itself got wrong are never rerun: only records whose "error" is an API error.
#
# Run from the repo root (real API calls, needs ANTHROPIC_API_KEY):
#   python -m eval.rerun_errors eval/results/pipeline-check.json     # last batch in that file
#
# Each replaced record keeps a "rerun" timestamp and the original error ("rerun_of"), so it's visible
# which trials ran later.

import json
import sys
from datetime import datetime
from pathlib import Path

from eval.eval import Evaluator


def api_error(record):
    return bool(record.get("error")) and "API error" in record["error"]


def main():
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python -m eval.rerun_errors <results file>")
    path = Path(sys.argv[1])
    batch = json.loads(path.read_text())[-1]
    todo = [i for i, r in enumerate(batch["trials"]) if api_error(r)]
    print(f"batch {batch['batch_id']}: {len(todo)} trials with API errors to rerun")
    ev = Evaluator(batch.get("description", ""))
    for i in todo:
        old = batch["trials"][i]
        record = ev.run_trial(old["agent"], old["profile"], old["task"], old["trial"])
        record["auto_grade"] = ev.auto_grade(record)
        record.update(rerun=datetime.now().isoformat(timespec="seconds"), rerun_of=old["error"])
        batch["trials"][i] = record
        ev.save_results(batch, path)  # same batch_id: replaces this batch in place, other batches untouched
        print(f"  {old['agent']} {old['profile']} {old['task']}: "
              f"{'ERROR ' + record['error'][:80] if record['error'] else 'pass' if record['auto_grade']['pass'] else 'fail'}")


if __name__ == "__main__":
    main()
