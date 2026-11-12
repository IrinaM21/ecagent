# Pipeline Check

Run on 2026-11-11 with `python -m eval.pipeline_check`. Raw per-trial records (scripted turns, replies, picks, final calendar, registrations, automatic grades, tokens) are in `eval/results/pipeline-check.json`.

This checks that the whole eval pipeline works (every fixture, every task's checks, every agent/ablation) before the real eval. It is **not eval results**: one trial per agent/ablation per eval_instance (so pass^1, not pass^3), on draft (not frozen) fixtures, with no manual grading.

## Setup

**Eval_instances:** all 36 (profile, task) pairs from `eval/benchmark-tasks.json` (10 tasks, 1-5 profiles each). One trial per agent/ablation: 108 trials.

**Environment:** each task's fixture database (`data/fixtures/<task>.db`, 12-25 synthetic events and 2-9 clubs, week of Nov 16-22, 2026), "today" pinned to Monday, Nov 16. Web search off for every agent. Fresh agent, simulated calendar and simulated registration per trial (`multi-step`: the first registration attempt fails on purpose).

**Agents/ablations:** `full_agent`, `no_compact`, `single_pass` (`docs/agent.md`, Experimental Setup), all `claude-sonnet-4-6`.

**Grading:** `auto_grade` against each fixture's answer key and the profile's free time (`docs/design-decisions.md`, Evaluation). Every instance's fixture has a correct answer, so a trial with **no picks** ("nothing fits" or no answer) can't pass: its automatic checks don't apply and it would fail the manual `nothing_fits_justified` label. Manual checks (reply text) and quality were not graded. Required actions are scored for `full_agent` only.

## Results

| | `full_agent` | `no_compact` | `single_pass` |
|---|---|---|---|
| Passed automatic checks | 19/36 | 17/36 | 18/36 |
| Failed automatic checks | 3 | 5 | 15 |
| No picks (fail once manually graded) | 14 | 14 | 3 |
| Passed, when it picked something | 19/22 | 17/22 | 18/33 |
| Tokens (in / out) | 414,422 / 132,025 | 297,841 / 41,685 | 299,304 / 18,691 |
| Cost | $3.22 | $1.52 | $1.18 |

No trial hit an error, and every pick was a real fixture row. Total cost $5.92 (at $3 / $15 per million input / output tokens).

**By task** (pass / fail / no picks):

| Task | `full_agent` | `no_compact` | `single_pass` |
|---|---|---|---|
| `match-interest` | 0 / 0 / 5 | 0 / 0 / 5 | 2 / 3 / 0 |
| `one-schedule` | 0 / 0 / 5 | 0 / 0 / 5 | 1 / 4 / 0 |
| `schedule-eca` | 4 / 1 / 0 | 4 / 1 / 0 | 4 / 1 / 0 |
| `choose-conflicting` | 3 / 2 / 0 | 4 / 1 / 0 | 3 / 2 / 0 |
| `multiple-schedule` | 5 / 0 / 0 | 3 / 2 / 0 | 2 / 2 / 1 |
| `multi-restriction` | 2 / 0 / 0 | 2 / 0 / 0 | 1 / 0 / 1 |
| `adapt-schedule` | 0 / 0 / 1 | 0 / 0 / 1 | 0 / 1 / 0 |
| `resolve-conflicting` | 0 / 0 / 2 | 0 / 0 / 2 | 0 / 2 / 0 |
| `multi-step` | 5 / 0 / 0 | 4 / 1 / 0 | 4 / 0 / 1 |
| `long-context` | 0 / 0 / 1 | 0 / 0 / 1 | 1 / 0 / 0 |

**Failed checks:** `full_agent`: `picked_from_overlap_set` 2, `fits_schedule` 1. `no_compact`: `fits_schedule` 4, `hard_constraints` 2, `one_per_interest` 2, `interest` 1, `no_avoid` 1. `single_pass`: `fits_schedule` 4, `club_or_fitting_event` 3, `is_event` 3, `interest` 2, `one_per_interest` 2, `hard_constraints` 2, `best_ranked_preference` 2, `picked_from_overlap_set` 1.

**`full_agent` required actions:** `events_added:1` 13/15, `events_added:2` 5/5, `registered` 5/5 (all recovered from the forced first failure), `no_overlap_between_added` 5/5; `planned_event_removed` not scored (no replacement picked in `adapt-schedule`). Action pass^1: 0.9.

## Findings

**1. `full_agent` and `no_compact` skip retrieval when the request doesn't name an interest, then say their activity list is empty.** All 14 no-pick trials of each are the five tasks whose query names no interest: `match-interest` ("Can you find me an activity?"), `one-schedule`, `resolve-conflicting`, `adapt-schedule` (the runtime update) and `long-context`. The router picks `user` (reply to the student) before any subagent runs; a direct check gave `user` in 4/4 router calls for "Can you find me an activity?". The reply step then has no results and tells the student its database is empty or has nothing matching (25 of the 31 no-pick replies across agents), which is false: every fixture has a correct answer. `verify_response` doesn't catch this, since it only runs when activities are shown.

**2. When they do retrieve, both are accurate.** `full_agent` passed 19 of the 22 trials where it picked something; its 3 failures are 2 picks outside `choose-conflicting`'s overlap set and 1 outside the schedule. `no_compact` passed 17/22. One trial per instance is too few to compare the two.

**3. `full_agent` sometimes takes back a correct pick on the approval turn.** In `choose-conflicting` (P3, P5) it proposed the correct event plus a related club (the club is why both fail `picked_from_overlap_set`), then, after "Yes, please add it to my calendar", said it had no activity data and that the details it gave were "fabricated", and added nothing (`events_added:1` fails). The approval turn ran without the earlier results, the same pattern as finding 1.

**4. `single_pass` always answers, but it is wrong more often.** It picked something in 33/36 trials and passed 18 of them. Its failures are spread across checks: activities outside the schedule, the wrong kind (a club where an event was needed, or the reverse), off-interest picks, constraint breaks, and ignoring the top-ranked preference. Its 3 no-pick trials asked the student to choose between options instead of naming picks (e.g. P1 `multiple-schedule` asked whether "one photography activity and one photography activity" meant an event and a club).

**5. Unrequested registration.** In 26 trials (`schedule-eca`, `choose-conflicting`, `multiple-schedule`), `full_agent` and `no_compact` registered the student for the event after approval, though only `multi-step` asks for registration. The checks for those tasks don't cover it, so it didn't affect grades.

**6. Harness bugs found and fixed during the check.**
- `single_pass` named its picks by database id instead of list number, so every pick was dropped before grading; the prompt now asks for the list number (found in a first single-instance run).
- `save_results` crashed on an empty `results.json` and on an older results file in a different format; both are handled now (the single-instance records moved to `eval/results/live-test.json`).
- The runner's summary line counted no-pick trials as passes (all their automatic checks are not applicable); it now reports them separately.
