# Benchmark: Simulated Student Profiles

The benchmark runs the agent (and the baselines) on every combination of a **simulated student
profile** and a **benchmark task** (`eval/benchmark-tasks.json`). This document covers the
profiles in `eval/simulated-profiles.json`: what each one tests, the JSON schema, and how to
maintain them.

| File | Purpose |
|---|---|
| `eval/simulated-profiles.json` | The five profiles. **Generated, do not hand-edit.** |
| `eval/generate_profiles.py` | Source of truth for profile data; writes the JSON. |
| `eval/validate_profiles.py` | Standalone schema + ground-truth consistency checks. |
| `eval/benchmark-tasks.json` | Task ids and queries (task ids are referenced by profiles). |

## The five profiles

| ID | Student | What it tests |
|---|---|---|
| `P1-simple` | Maya, first-year Journalism. One interest (photography), light schedule, minimal constraints. | Basic ECA retrieval, interest matching, personalization. |
| `P2-busy` | Daniel, junior Computer Engineering. Dense classes/labs/work, bus commute (15-min buffers), max 2 activities / 3 h per week, an exam, interview and group meeting this week. | Schedule reasoning, conflict avoidance (buffers, one-off events), weekly caps, ranking multiple interests. |
| `P3-picky` | Leah, sophomore MCB pre-med. Explicit dislikes, hard restrictions (Shabbat Fri 16:30–Sat 18:00, no alcohol-centered events, ≤ $25/semester, in person, no car, done by 21:00), and a student-stated ranking of six soft preferences. | Hard vs. soft separation, exclusion of disliked categories, rank-based tie-breaking. |
| `P4-changing` | Sam, sophomore Statistics. Over two sessions drops a class, work shifts move Tue/Thu → Mon/Wed, competitive chess becomes a dislike (replaced by casual board games), weekends become unavailable for November. Two runtime updates. | Using newest information over stale, replanning, one-time exceptions to a standing rule. |
| `P5-long-context` | Jordan, senior Information Sciences. 42-message history (~3k tokens, ~3× the 1024-token memory limit) across four sessions; half the turns are off-topic, nine scheduling facts are buried throughout, two preferences/times are superseded. Two runtime updates. | Context compaction, recall of buried facts, not reviving superseded facts after compaction. |

Each profile lists the tasks it is meant for in `eval_only.applicable_tasks` (e.g. only P4 and
P5 have material for `adapt-schedule` / `resolve-conflicting`).

## Schema

The top level is an object keyed by profile id (the eval loop iterates its keys, so **do not add
non-profile keys** such as metadata). Every profile has the same shape:

```
<profile-id>: {
  id, schema_version, label, description,
  agent_visible:   { intake_profile, conversation_history },
  runtime_updates: [ { id, for_tasks, message } ],
  eval_only:       { tests, applicable_tasks, current_truth, busy_blocks, available_windows,
                     grading_checks, superseded_facts, known_good_activities, ... }
}
```

### Visibility tiers

| Tier | Who sees it | When |
|---|---|---|
| `agent_visible` | The agent | Before the task query. `conversation_history` is loaded into memory; `intake_profile` may be given to the scheduler as structured input. |
| `runtime_updates` | The agent, **only the `message` field** | Injected by the eval harness mid-task, only for the tasks in `for_tasks`. `id`/`for_tasks` are harness metadata. |
| `eval_only` | Grader only | Never placed in memory, prompts, or tool results. The validator checks agent-visible text for leaked grader content. |

`id`, `label`, `description` and `schema_version` are bookkeeping and should not be sent to the
agent either (labels like "Picky Student" would hint at what is being tested).

### State object (`intake_profile` and `eval_only.current_truth`)

Both use the same keys:

| Field | Meaning |
|---|---|
| `student` | `name`, `year`, `major`, `college`, `residence`. |
| `interests`, `goals`, `dislikes` | Free-text lists. `interests` and `dislikes` never overlap. |
| `hard_constraints` | Non-negotiable typed rules; any violation fails the output. Each has an `id` (`H1`…) and a `type`, see below. Optional `note`, `valid_from`/`valid_until` (YYYY-MM-DD). |
| `soft_preferences` | Ranked preferences (`rank` 1 = most important, contiguous 1..n), each with `id` (`S1`…), `category` (`interest`/`time`/`location`/`format`/`social`/`goal`) and `preference` text. Used to rank valid options, never to reject them. |
| `schedule` | `timezone` (`America/Chicago`), `reference_week` `{start, end}`, `recurring` items (`id`, `title`, `type`, `days` `Mon`…`Sun`, `start`, `end`, optional `location`), `one_off` items (same but `date` instead of `days`). |

Hard constraint types:

| `type` | Parameters |
|---|---|
| `earliest_start` / `latest_end` | `time` (every profile has both) |
| `unavailable` | `days` + `start`/`end` (weekly) or `date` + `start`/`end` |
| `buffer_minutes` | `minutes` required before and after every commitment |
| `max_activities_per_week`, `max_hours_per_week`, `max_event_duration_hours` | `value` |
| `max_cost_usd` | `value`, `per` (`event` / `semester`) |
| `exclude`, `location` | `rule` (free text) |
| `format` | `allowed` (e.g. `["in-person"]`) |

All times are 24-hour `HH:MM`, all dates `YYYY-MM-DD`, all in `America/Chicago`, matching the
`events` table in `scraper/db.py`. Use `23:59` for end of day.

### `conversation_history`

Anthropic Messages API format: a list of `{"role": "user" | "assistant", "content": "<string>"}`,
starting with `user`, strictly alternating, and **ending with `assistant`** so the harness can
append the task query as the next `user` turn. Assistant turns never name specific campus clubs
or events.

### `eval_only` fields

| Field | Meaning |
|---|---|
| `tests` | Capabilities the profile targets. |
| `applicable_tasks` | Task ids (from `benchmark-tasks.json`) this profile should be run on. |
| `current_truth` | The resolved state after every change in the history. Grade against this, not `intake_profile`. |
| `busy_blocks` | `current_truth` expanded over the reference week: `{date, start, end, source}`, where `source` is the recurring/one-off/constraint id. |
| `available_windows` | Free time per day inside `earliest_start`–`latest_end`, after removing busy blocks padded by `buffer_minutes`. Gaps under 30 min are omitted. `{date, start, end, minutes}`. |
| `grading_checks` | `{id, kind, check}` with `kind` `hard` (must pass), `soft` (ranking quality) or `behavior` (how the agent responds). Every non-time hard constraint `Hn` has a `C-Hn` check. |
| `superseded_facts` | `{field, old, new, stated_in_msg, superseded_in_msg}`: indices into `conversation_history`. |
| `known_good_activities` | Verified example activities. **Intentionally empty**: there is no `campus.db` snapshot yet, and entries may only be added with a source (`event_id`, `club_id` or `url`); the validator rejects unsourced entries. |
| `interest_keywords`, `avoid_keywords` | Keyword hints for rule-based matching. |
| `runtime_update_effects` | Per update id: `adds_busy` / `removes_busy` / `adds_available` blocks and an `expected` behavior description. |
| `buried_facts`, `irrelevant_msgs`, `message_dates` | (P4/P5) Message indices of key facts and off-topic turns, and the simulated date of each range of messages. |

## Updates and superseded preferences

There are two kinds of change:

1. **Changes already in the history** (P4, P5). The original statement stays in
   `conversation_history` unchanged, and a later user message changes it (e.g. P5 says
   "Wednesdays 12:00–17:00" and later "13:00–18:00 now"). `intake_profile` shows the original
   state, `current_truth` the resolved state, and `superseded_facts` links the two messages. The
   agent must act on the newer statement; using a superseded value is a hard failure. The
   validator requires `intake_profile == current_truth` exactly when there are no superseded facts.
2. **Runtime updates** (`runtime_updates`). User messages the harness injects during
   `adapt-schedule` / `resolve-conflicting` / similar tasks, after the agent's first answer. Their
   effect on ground truth lives only in `eval_only.runtime_update_effects`; `current_truth`,
   `busy_blocks` and `available_windows` describe the state **before** any runtime update.

When adding a change: keep the old message, add the new message later in the history, update
`current_truth`, add a `superseded_facts` entry, and add a grading check that fails if the old
value is used.

## Reference week and simulated calendar

All profiles share one reference week: **Monday 2026-11-02 to Sunday 2026-11-08**. Daylight
saving time ended on 2026-11-01, so the whole week is CST (UTC-6). Recurring items repeat on their
`days` within that week; one-off items and dated constraints must fall inside it (validated).

"This week" in a task query means the reference week. Because `search_events` defaults to today
and the database is re-scraped weekly, reproducible results require running the eval against a
**frozen `campus.db` snapshot covering 2026-11-02 to 2026-11-08** with the agent's notion of
"today" pinned to 2026-11-02. `message_dates` give simulated dates for when earlier conversation
sessions happened; they are before the reference week.

## Regenerating and validating

Both scripts use only the Python standard library and can be run from any directory.

```bash
# validate the committed JSON (schema, ground-truth consistency, leaks); exit 1 on failure
python eval/validate_profiles.py
python eval/validate_profiles.py --profiles path/to/other.json --tasks eval/benchmark-tasks.json

# is the JSON in sync with the generator? (writes nothing)
python eval/generate_profiles.py --check

# after editing profile data in generate_profiles.py
python eval/generate_profiles.py --force   # overwrite eval/simulated-profiles.json
python eval/validate_profiles.py

# write to a new file instead (refuses to overwrite an existing file without --force)
python eval/generate_profiles.py -o /tmp/profiles.json
```

The generator is deterministic: unchanged data regenerates byte-identical JSON. It derives
`busy_blocks` and `available_windows` from `current_truth`, and looks up message indices
(`superseded_facts`, `buried_facts`, …) by message text, so inserting a message does not break
references. The validator does **not** import the generator; it recomputes busy blocks and
windows independently, so it also catches hand edits that drift from `current_truth`.

What the validator checks:

- Structure: identical keys across profiles, required student fields, valid task ids.
- Formats: 24-hour times, ISO dates inside the reference week, `America/Chicago`, valid weekdays,
  start before end, unique ids, soft ranks 1..n, known hard-constraint types.
- Consistency: no overlapping recurring commitments; `busy_blocks` and `available_windows` match
  `current_truth`; intake vs. current truth is explained by `superseded_facts`; every non-time
  hard constraint has a grading check; interests and dislikes are disjoint; runtime updates have
  effects and target applicable tasks; `known_good_activities` entries cite a source.
- Messages: Messages API format, alternation, ends with `assistant`, fact/date indices point at the
  right messages.
- Leakage: no grader vocabulary or grading-check text in agent-visible content.
- Long context: the compaction profile's history is at least ~2× the 1024-token memory limit
  (estimated as characters / 4).

## Integrating with `eval/eval.py`

Expected harness behavior (not yet implemented in `eval.py`):

1. For each profile, create a **fresh** `Memory` and load
   `profile["agent_visible"]["conversation_history"]`, never the whole profile object.
2. Run only the tasks in `eval_only.applicable_tasks`; append the task query as a `user` message.
3. For tasks listed in a runtime update's `for_tasks`, send the update's `message` after the
   agent's first answer and grade the final answer with `runtime_update_effects`.
4. Grade with `eval_only` (`busy_blocks`, `available_windows`, `grading_checks`) against the
   frozen database snapshot.
