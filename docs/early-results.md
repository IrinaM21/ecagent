# Early Testing Results

Run on 2026-10-27 with `python -m eval.early_eval`. Raw per-run output (picks, grades, model replies, tool calls, cost) is in `eval/early-results.json`.

## Setup

**Tasks:** Task 1 (`match-interest`, "Can you find me an activity?") and Task 2 (`one-schedule`, "Can you find me an activity that works with my schedule?"), on all 5 simulated profiles. One trial per system, profile and task (30 runs). These are the only two tasks that don't need a calendar tool.

**Environment:** a frozen snapshot of the scraped database, `data/snapshot-2026-10-27.db`, covering the reference week of Nov 2–8, 2026 (165 events, 1,228 clubs). "Today" is pinned to Monday, Nov 2.

**Systems:**

| System | What it gets | How it picks |
|---|---|---|
| Rule-based | The structured sign-up profile only | Keyword-matches interests against this week's events, keeps those that fit the schedule, takes the earliest; falls back to a matching club (Task 1) or any fitting event (Task 2) |
| Single-pass | Sign-up profile + conversation history + every event this week and every club, all in the prompt (~35k tokens) | One Claude Sonnet 5 call, no tools |
| Agent | Sign-up profile + conversation history | Claude Sonnet 5 with the `search_events` / `search_clubs` database tools, up to 10 turns |

**Grading (code-based, one recommendation per run),** using each profile's grader-only data:

* **Exists:** the event (id and date) or club is in the snapshot. No invented activities.
* **Fits:** the event lies inside one of the profile's available windows (classes, one-off commitments, buffers, earliest/latest times, blocked days). Events with no listed time, and clubs, can't be verified.
* **Interest:** the activity's text contains one of the profile's interest keywords.
* **Avoid:** the text contains a dislike or a superseded interest. This is a hard fail.
* **Task 1 passes** if the activity exists, matches an interest, avoids dislikes, and is a club or an event that fits.
* **Task 2 passes** if it is an event that exists, fits, and avoids dislikes, matching the task's success criteria, which don't require an interest match.

## Results

| System | Task 1 pass | Task 2 pass | Interest match (both tasks) | Hit a dislike | Cost |
|---|---|---|---|---|---|
| Rule-based | 3/5 | 4/5 | 5/10 | 2/10 | $0.00 |
| Single-pass | 5/5 | 0/5 | 10/10 | 1/10 | $1.48 (~$0.15/run) |
| Agent | 5/5 | 0/5 | 10/10 | 0/10 | $0.77 (~$0.08/run) |

Every recommendation from every system existed in the database: no hallucinated activities. The agent made 6 tool calls per run on average (2–11).

## Findings

**1. Both LLM systems personalize well; the rule-based baseline does not.** Both LLM systems matched an interest in 10/10 runs, against 5/10 for rule-based. The rule-based failures are the expected ones:
* P4 (changing preferences): it searched on the student's original interests, so the word "personal" (from "personal finance") matched a polymer engineering club.
* P5 (long context): it used "improv comedy", an interest the student later dropped. That matched a lecture about a "Divine Comedy" artwork, which also fell during the student's busy Wednesday afternoon.

Both LLM systems used the newer information in the conversation history: chamber music for P5, and finance in place of competitive chess for P4.

**2. Task 2's 0/5 for the LLMs is mostly a data gap, not a reasoning failure.** In every Task 2 run the LLMs recommended a club, and clubs have no meeting times in our data, so the grader can't confirm they fit. Their replies show they did check the schedule first. The single-pass reply for P2 went through each day against classes, shifts and the 15-minute bus buffer, found the only entrepreneurship event "misses your buffer by 5 minutes", and fell back to a club. Very few relevant events fit at all: across the five profiles, between 0 and 3 events per profile fit the schedule and match an interest keyword, and most of those are keyword false positives, such as a movie screening matching "film" for the photography student. Given that, recommending a club is a reasonable choice.

**3. Rule-based "wins" Task 2 by ignoring relevance.** Its 4/5 comes from falling back to any event that fits, for example office hours at an entrepreneurship center for the photography student. Only 2 of its 5 Task 2 picks matched an interest. As written, Task 2's criteria reward this.

**4. The tool-using agent was half the cost of single-pass and more careful.** The agent read only what it searched for; single-pass carried the full catalog in every call. On P3 (picky), single-pass recommended Hearts in Practice, a pre-health club whose mission mentions "professional networking", which the student lists as a dislike. Single-pass also asserted details that aren't in our data, such as dues being under the $25 cap and volunteer shifts being flexible. The agent saw the same club, explicitly rejected it for its networking emphasis, and chose a clinic-volunteering group instead.

**5. Both LLM systems make unverifiable schedule claims about clubs.** For example, a club "meets regularly, so it fits well around your class schedule". The grader can't catch these yet, and they would mislead a real student.

## Limitations

* One trial per run, so these are early indications, not stable rates.
* Keyword grading is lenient (a varsity basketball game counts as "pickup basketball") and misses synonyms.
* Hard constraints the database doesn't record (cost, alcohol, in-person format, weekly caps) are not checked.
* Only Tasks 1 and 2 are covered. The others need a simulated calendar tool.

## Next steps

1. **Get club meeting times** (from club websites via web fetch, or hand-curated for the benchmark week) so club recommendations can be checked against the schedule. This is the main blocker for Task 2.
2. **Decide whether Task 2 should also require an interest match.** Without it, irrelevant picks pass.
3. **Fill `known_good_activities` in each profile** from this snapshot, so grading can use hand-checked answers in place of keyword matching.
4. **Run 3+ trials per run** once the above is fixed, for pass^k.

## Reproducing

```bash
python -m eval.early_eval --systems rule          # free
python -m eval.early_eval                          # all three, needs ANTHROPIC_API_KEY (~$2.25)
python -m eval.early_eval --resume                 # continue an interrupted run
```
