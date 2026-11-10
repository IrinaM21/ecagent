# Design Decisions

## Benchmark (`eval/benchmark-tasks.json`)
- **Task difficulty** Distribution: 20% easy tasks, 30% medium tasks, and 50% hard. We chose more difficult tasks because our goal is to evaluate the agent beyond basic recommendation generation. Easier tasks establish whether an agent can identify tasks and availability; the harder tasks evaluate the ability to manage constraints, update, and use info from previous interactions. Difficulty also varies with student profiles (complexity, context management).

## Evaluation (`eval/eval.py`)

### Setup
- Task order is **randomized** (for each profile) to control for effects.
- **Load agents/models separately** (`full_agent`, `no_compact`, `single_pass`) --> no information leaks confounding results
- Agent only sees **`agent_visible`** (intake profile + conversation history), no answer-key leaks from `eval_only`, labels, or runtime updates before injection
- **Frozen snapshot** of the events DB (week of 2026-11-02) + "today" pinned to 2026-11-02 (reproducible grading)
- **Web fallback** on during eval (per `docs/agent.md`), logged (some trial-to-trial variance)
- **Scripted turns**, same for every system: task query, runtime update (if any), fixed approval reply. Fixed in advance, not generated from the system's output (same information for all systems)
- Tasks with empty queries skipped

### Baselines
- **Baselines**: we compare `full_agent` to `no_compact` to isolate the effect of context management module on agent performance. We use `single_pass` as a proxy for the dominant alternative (asking an LLM chat application).
- **`no_compact`** = no compactor (no contradiction step), naive truncation of oldest turns
- **`single_pass`** = one model call per turn, no agent pipeline (memory, compaction, retrieval, scheduling). Gets intake profile + full conversation history + event catalog (reference week events + clubs) in the prompt + web search tools (same information sources as the agent)
- `single_pass` keeps the full uncompacted history (~3k tokens for P5), so `full_agent` vs. `single_pass` also shows whether compaction keeps up with having everything in context

### Metrics
- **Non-deterministic behavior** --> pass^3 in objective metrics, standard deviation in subjective metrics. 
- **pass^k** used (rather than pass@k) to reflect the importance of reliability, rather than creativity, for our agent’s functionality.
- **Graded on the final selection** (structured activities), not calendar writes, since `single_pass` has no calendar tool
- **Manual quality grading** (1-5), no LLM judge: `eval/manual_grading.py`, last batch of `full_agent` trials in `eval/results.json` (list of batches `{batch_id, description, trials}`)

## Agent (`agent/`)

### General
- **`claude-sonnet-4-6`** for every module (`MODEL` in `utils.py`), supports the web search/fetch tool versions in `scraper/tools.py` (Sonnet 4.5 doesn't)
- All modules extend **`Utils`** (`call`, `count_tokens`, `text_of`, `log`), retries on rate-limit/server errors, 300 s timeout
- **Action log** (`agent/model_log.json`): tracks actions at each step of the trajectory for debugging/analysis, entries grouped by `run_id` (system/profile/task/trial)

### Agent (`agent/agent.py`)
- **Supervisor loop** (following ScheduleMe [2]): router picks the next action from task + results so far + user input (a subagent, `user`, or `FINISH`), no fixed trajectories
  - Subagents (retriever, recommender, scheduler) return results to the agent, which decides what's next
  - `user` = ask a question or propose a plan, ends the turn
- **Code-enforced checks** (ScheduleMe enforces them by prompt only, and has no confirmation step): plans re-checked by the scheduler in code, calendar add only after the student approves
- **Response check** (`verify_response`): LLM verifies each reply against the activity data (no made-up details, schedule claims only for checked plans), one rewrite if unsupported
- Rules live in the subagents' prompts and tool descriptions, not the system prompt
- **Minimal system prompt**: role, date, compaction summary (replaces chat history). No student profile (modules read `state` from memory)
- **Results always passed through the agent** (retriever --> agent --> recommender --> agent --> scheduler --> agent --> recommender --> agent), by the agent's code, not its LLM. No module calls or holds another
- Proposed plan kept as a memory note across turns, cleared if the next message doesn't approve it (approval must come right after the proposal)
- Every turn ends with a reply to the student, max 8 router steps per turn, subagent errors passed back to the router as results
- Memory saved to a JSON file at the end of each turn (real use only, eval always loads from the simulated profiles)

### Memory (`agent/memory.py`)
- **Structured notes**: `state` (intake profile schema, updated from conversation) + `summary` (from compaction)
- Hard constraints checked in code against **`state`**, not `intake_profile` (stale for P4/P5)
- **Token limit 1024**: full agent --> compactor summary replaces history, `no_compact` --> drop oldest turns
- Memory runs context management itself, incl. at load (P5 is over the limit before the first query)
- **`save`/`restore`** as JSON, not pickle (loading a pickle can run code, no speed gain at this size, readable)

### Compactor (`agent/compactor.py`)
- **Contradictions** resolved whenever new info arrives (load + each user message), not only near the limit --> P4 (~400 tokens) never reaches 1024
- **Summarization** only near the limit: salient points --> compress --> contradiction pairs --> choose 1, 2, or both (one-time exceptions) --> keep rest in order

### Retriever (`agent/retriever.py`)
- **Common event format**: `kind, id, title, date, start, end, location, description, categories, url, source`
- **Keywords** (5-10, incl. synonyms) generated by the retriever from the request + `state` interests/goals, so the agent doesn't need the profile
- **Keyword search**, one keyword at a time (DB query needs all words to match)
- **Approval score** 0-10 ≈ chance out of 10 the student approves (5 = coin flip), content only (time = scheduler)
- **Web fallback** if < 3 candidates score >= 7 (likely approved), only URLs returned by `web_search`/`web_fetch` kept

### Recommender (`agent/recommender.py`)
- **Candidates**: approval score, then ranked soft preferences (lexicographic: S1 > S2 + S3), soft preferences never reject
- **Plans**: pairwise LLM judge, both A/B orders (position bias), disagreement = tie, sorted with this comparison

### Scheduler (`agent/scheduler.py`)
- **Busy blocks** from `state` + calendar events, same rules as `eval/validate_profiles.py` (matches grader)
- **Only events** scheduled (clubs have no meeting times, can still be recommended)
- LLM generates up to **5 candidate plans** from retrieved activities, checks with `free_time`/`check_plan` tools, final plans re-checked in code
- **Calendar tools**: `SimulatedCalendar` for eval (starts empty, records added events)
