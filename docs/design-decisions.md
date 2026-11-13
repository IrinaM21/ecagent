# Design Decisions

## Benchmark (`eval/benchmark-tasks.json`)
- **Task difficulty** Distribution: 20% easy tasks, 30% medium tasks, and 50% hard. We chose more difficult tasks because our goal is to evaluate the agent beyond basic recommendation generation. Easier tasks establish whether an agent can identify tasks and availability; the harder tasks evaluate the ability to manage constraints, update, and use info from previous interactions. Difficulty also varies with student profiles (complexity, context management).

## Evaluation (`eval/eval.py`)

### Setup
- Task order is **randomized** (for each profile) to control for effects.
- **Load agents/models separately** (`full_agent`, `no_compact`, `single_pass`) --> no information leaks confounding results
- Agent only sees **`agent_visible`** (intake profile + conversation history), no answer-key leaks from `eval_only`, labels, or runtime updates before injection
- **Per-task fixture DBs** (`data/fixtures/<task>.db`, built from `eval/fixtures/<task>.json`) + "today" pinned to 2026-11-16 (reproducible grading)
  - Small, clean, synthetic, no scraped rows: noisy data makes baseline comparisons unreliable (e.g. annotation errors in the BIRD text-to-SQL benchmark changed model rankings) and lowers test accuracy
  - Each fixture has an **answer key** (labels per activity and profile: relevant, dislike, breaks a constraint, outside the schedule, ...) and its setup (starting calendar, overlap set, restrictions, reference solution)
  - **Agent bias fix**: fixtures and answer keys are written from the task definitions and profiles only, before any eval run, then frozen (never changed after seeing results). Frozen 2026-11-12 after review; the pipeline check (`docs/pipeline-check.md`) ran on them before the freeze and led to no fixture changes
  - Every eval_instance has at least one correct answer, unless it is deliberately a "nothing fits" instance
  - The real snapshot (`data/snapshot-2026-11-11.db`) is not used by the eval (see Why relevance is not scored on real data)
- **Web search off during eval** (`use_web=False` for every agent): the eval focuses on what this project contributes, the agent architecture (multi-step planning, reasoning over constraints, context management), not on web search. Web results would also fall outside the fixture's answer key, so they can't be graded objectively yet. In real use both `full_agent` (retriever web fallback) and `single_pass` have it
- **Scripted turns**, same for every system: task query, runtime update (if any), fixed approval reply. Fixed in advance, not generated from the system's output (same information for all systems)
  - Query placeholders filled from the profile's `task_fills` (current interests only). One-interest profiles (P1) use the same interest for tasks that need two (`multiple-schedule`: two distinct activities for that interest)
- Tasks with empty queries skipped

### Baselines
- **Baselines**: we compare `full_agent` to `no_compact` to isolate the effect of context management module on agent performance. We use `single_pass` as a proxy for the dominant alternative (asking an LLM chat application).
- **`no_compact`** = no compactor (no contradiction step), naive truncation of oldest turns
- **`single_pass`** = one model call per turn, no agent pipeline (memory, compaction, retrieval, scheduling). Gets intake profile + full conversation history + event catalog (the task's fixture: events + clubs) in the prompt + web search tools (same tools and information as `full_agent`)
- **Deviation from the single-pass definition** (input --> model --> output, no external actions, no loop): `single_pass` gets web search, a server-side tool that searches and loops inside the one call. Kept because the baseline must have the same tools and information as `full_agent` (TA requirement), and because it approximates pasting the request into ChatGPT with search, which is what most students do. Off in the eval for now (see Setup)
- `single_pass` keeps the full uncompacted history (~3k tokens for P5), so `full_agent` vs. `single_pass` also shows whether compaction keeps up with having everything in context

### Metrics
- **Non-deterministic behavior** --> pass^3 in objective metrics, standard deviation in subjective metrics. 
- **pass^k** used (rather than pass@k) to reflect the importance of reliability, rather than creativity, for our agent’s functionality.
- **Graded on the final selection** (structured activities)
- **Same checks for every agent/ablation** (`docs/agent.md`, Experimental Setup, step 6), so pass^3 is compared on equal terms
- **Required actions are a separate `full_agent` score**, not compared to the baselines (not in pass^3): `events_added:N`, `planned_event_removed`, `registered`, `no_overlap_between_added` (calendar/registration). `single_pass` has no external actions by definition, so comparing on them would only measure "has a calendar tool"
- **Objective (code, pass^3) vs. manual (human) checks**: only checks with an answer the data proves are automatic. Relevance and constraints are decided in advance in each fixture's answer key, so they are automatic; only checks that read the reply are graded by a person (on real data no automatic relevance score is provably right; see Why relevance is not scored on real data)

  | Automatic, in pass^3 (`auto_grade`, from the fixture + answer key + profile) | Manual (`eval/manual_grading.py`) |
  |---|---|
  | `exists` (pick is in the task's fixture) | `reply_matches_calendar` (reply's add/remove claims vs. the final calendar) |
  | `fits_schedule`, `fits_schedule_after_update` (from `available_windows`: busy blocks, buffers, earliest/latest, unavailable) | `no_superseded_facts` (reply doesn't use outdated facts) |
  | `hard_constraints`: caps (`max_activities_per_week`, `max_hours_per_week`, `max_event_duration_hours`, counting planned activities still on the calendar) + answer-key labels for `exclude` (e.g. no alcohol), `max_cost_usd`, `format`, `location` (stated in the activity text) | `nothing_fits_justified`: empty picks, instead of the activity checks |
  | `interest`, `no_avoid` (answer key) | quality 1-5 |
  | `best_ranked_preference`, `one_per_interest` (answer key) | |
  | `picked_from_overlap_set`, `meets_request_restrictions` (fixture setup) | |
  | `is_event`, `club_or_fitting_event` (kind + schedule part) | |

  - **Empty picks** ("nothing fits this week"): the automatic pick checks are not applicable, and the trial gets `nothing_fits_justified` instead (otherwise an honest "nothing fits" would fail automatically)
- **Manual grading**, no LLM judge: `eval/manual_grading.py` on the last batch in `eval/results/results.json` (list of batches `{batch_id, description, trials}`)
  - **Labels** (yes/no) for the manual checks, next to **quality** (1-5). Binary labels are more consistent than a scale
  - **All three agents** are graded (the reply checks feed the comparison), not only `full_agent`. Reply checks and `nothing_fits_justified` are labeled per trial
  - **Shuffled (blind)**: trials of all three agents shuffled together (fixed seed), agent name and trial number hidden, no scores shown (no embedding/keyword hints that could bias the grader)
  - One grader, no flags: `python -m eval.manual_grading` grades whatever is left in the last batch
  - **Rubric** written in advance. "Nothing fits this week" + a reason (and e.g. clubs instead) is a correct answer only for instances whose fixture deliberately has no suitable activity
  - A trial passes only if its automatic checks pass and its manual labels are all yes; pass^3 = all 3 trials of an eval_instance pass (`pass_k` in `eval/eval.py`, undecided while labels are missing)

### Why relevance is not scored on real data (`docs/scripts/interest_test.py`)
- We tested replacing the keyword check (`interest_keywords`) with **cosine similarity of local embeddings** (`all-MiniLM-L6-v2`, sentence-transformers): activity text from the snapshot (title | categories | description) vs. the student's `current_truth` interests, best match. Neither method is right on every activity, and the only way to measure either is against human labels, so human judgment is the ground truth for relevance anyway. This, plus the noisy real data, is why the eval uses fixtures with an answer key written in advance
- **Test cutoff 0.45** on `data/snapshot-2026-11-11.db` (plot: [`docs/figures/interest-test.png`](figures/interest-test.png))
  - Clubs separate cleanly: real matches 0.49-0.64 (Point Four Photography 0.59, Personal Finance Club 0.64, Game Night 0.59), rest well below; one wrong match just above (Amateur Artists of Illustration 0.46 for photography)
  - Events are noisier: top scores are packed into ~0.33-0.48, real and wrong matches interleaved (EnterpriseWorks Tour 0.48, real: entrepreneurship; Artists Talk 0.44, wrong: music ensemble; Symphony Orchestra 0.43, real: music; Men's Basketball at Duke 0.39, wrong: "pickup basketball"), so any cutoff in that range misses some or passes some
- **Why events are harder** (snapshot, week of 2026-11-16):
  - Little text: 91 of 132 events have no summary, so the model sees a short title + event type; club missions average ~420 characters and say what the club is about
  - Many events aren't activities a student joins: the largest types are Athletics (26, mostly games to watch), Exhibition (21), and talks/informational sessions. "Illini Men's Basketball at Duke" shares words with "pickup basketball" but isn't something the student plays
  - Recurring events repeat once per day (deduplicated by title in the test script)
  - Only one event that week clears the cutoff for any profile (EnterpriseWorks Tour, P2). Best events for the others: P1 0.33, P3 0.41, P4 0.38, P5 0.44. Few relevant events exist for most profiles that week, which is why the rubric accepts "nothing fits"
- **Vs. the old keyword check** (same script, plot: [`docs/figures/interest-test-keywords.png`](figures/interest-test-keywords.png)): each activity at its embedding score, colored by whether `interest_keywords` matched it

  | Profile | Events: keyword / embedding / both | Clubs: keyword / embedding / both |
  |---|---|---|
  | P1-simple | 1 / 0 / 0 | 21 / 4 / 3 |
  | P2-busy | 5 / 1 / 1 | 38 / 10 / 9 |
  | P3-picky | 5 / 0 / 0 | 457 / 53 / 50 |
  | P4-changing | 0 / 0 / 0 | 32 / 10 / 6 |
  | P5-long-context | 5 / 0 / 0 | 125 / 8 / 8 |

  - Keywords pass far more, mostly false positives from broad stems: "health" for P3 (457 of 1249 clubs), "music"/"jam"/"ensemble" for P5 (125 clubs, incl. Hive Society Improv, a dislike), "film" for P1 (Weekend Films: Toy Story 5), "basketball" for P2 (Illini games to watch)
  - The embedding check at 0.45 is stricter, and also misses real matches just under the cutoff: FIRST Illini Robotics 0.44 (P2), Illini Film and Video 0.36 (P1), Investment Portfolio Organization 0.45 (P4, rounds up), Symphony Orchestra 0.43 (P5)
  - Few embedding-only passes, some wrong: Social Gaming Club 0.53 (P4, real), Dancing Illini 0.52 (P3, real), Competitive Super Smash Bros. Club 0.46 (P4, wrong: "casual board games"), Amateur Artists of Illustration 0.46 (P1, wrong)
  - So embeddings trade many false positives for some false negatives near the cutoff. We suspect tuning the cutoff or the model would move the errors, not remove them
  - Other automatic scores (e.g. a keyword + embedding hybrid) were not tested: any of them has the same validity/objectivity problem (it can only be checked against human labels), so relevance moves to manual grading instead
- Dislikes (`no_avoid`) have the same problem: `avoid_keywords` can't tell Hive Society Improv (P5 dislikes improv) from a music group, so dislikes go in the fixture answer key too
- The script and plots stay as the evidence for this decision; `sentence-transformers` is only needed to rerun it (not by the eval)

## Agent (`agent/`)

### General
- **`claude-sonnet-4-6`** for every module (`MODEL` in `utils.py`), supports the web search/fetch tool versions in `scraper/tools.py` (Sonnet 4.5 doesn't)
- All modules extend **`Utils`** (`call`, `count_tokens`, `text_of`, `log`), retries on rate-limit/server errors, 300 s timeout
- **Action log** (`agent/model_log.json`): tracks actions at each step of the trajectory for debugging/analysis, entries grouped by `run_id` (system/profile/task/trial)

### Agent (`agent/agent.py`)
- **Supervisor loop** (following ScheduleMe [2]): router picks the next action from task + results so far + user input (a subagent, `user`, or `FINISH`), no fixed trajectories
  - Subagents (retriever, recommender, scheduler) return results to the agent, which decides what's next
  - `user` = ask a question or propose a plan, ends the turn
  - **The retriever's description tells the router it uses the student's interests and schedule from memory** ("no need to ask the student for them"). The problem it fixes came from the pipeline check (`docs/pipeline-check.md`): on requests that name no interest ("Can you find me an activity?"), the router picked `user` before any search, so `full_agent` and `no_compact` picked nothing in 14/36 instances each and told the student their activity list was empty. From the router's view the request had no topic, and nothing said the retriever already knows the student
  - An earlier version fixed this by passing the whole `state` note to the router (second pipeline-check batch: no empty instances). The description change replaces it for **token efficiency**: the router only picks the next step, and the interests are used by the retriever (keywords, scores) and recommender, which read `state` themselves, so the extra ~300-780 tokens on every router call (up to 8 per turn) bought nothing. In the pipeline check (one trial per agent per instance, `docs/pipeline-check.md`), it cut `full_agent`'s input tokens by 32% (783k --> 531k) and cost by 18% ($4.72 --> $3.86) against the earlier version (`no_compact`: cost -19%), with no empty instances in either. `full_agent` passed 29/36 with this version and 32/36 with the earlier one (one trial per instance). It also keeps the minimal-prompt design (no student profile in prompts, modules read `state`) and doesn't give `no_compact`'s router the stale intake profile (P4's chess, P5's improv)
- **Code-enforced checks** (ScheduleMe enforces them by prompt only, and has no confirmation step): plans re-checked by the scheduler in code, calendar add only after the student approves
- **Response check** (`verify_response`): LLM verifies each reply against the activity data (no made-up details, schedule claims only for checked plans), one rewrite if unsupported
- **Prompt format**: every prompt is one f-string block: role description, then instructions, then the relevant info in XML tags, then the request for the response (output format)
- Rules live in the subagents' prompts and tool descriptions, not the system prompt
- **Minimal system prompt**: role, date, compaction summary (replaces chat history). No student profile (modules read `state` from memory)
- **Results always passed through the agent** (retriever --> agent --> recommender --> agent --> scheduler --> agent --> recommender --> agent), by the agent's code, not its LLM. No module calls or holds another
- Proposed plan kept as a memory note across turns, cleared if the next message doesn't approve it (approval must come right after the proposal)
- Every turn ends with a reply to the student, max 8 router steps per turn, subagent errors passed back to the router as results
- Memory saved to a JSON file at the end of each turn (real use only, eval always loads from the simulated profiles)

### Memory (`agent/memory.py`)
- **Structured notes**: `state` (intake profile schema, updated from conversation) + `summary` (from compaction)
- Hard constraints checked in code against **`state`**, not `intake_profile` (stale for P4/P5)
- **Token limit** from the Models API (`max_input_tokens` for `MODEL`, minus `MAX_TOKENS` for the reply), same for every system incl. eval: full agent --> compactor summary replaces history, `no_compact` --> drop oldest user/assistant pairs. The newest exchange (last user/assistant pair + new user message) is always kept word for word (assumes it is under the limit)
- Memory runs context management itself, incl. at load (P5 is over the limit before the first query)
- **`save`/`restore`** as JSON, not pickle (loading a pickle can run code, no speed gain at this size, readable)

### Compactor (`agent/compactor.py`)
- **Contradictions** resolved whenever new info arrives (load + each user message), not only near the limit --> P4 (~400 tokens) never reaches it
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
- **Busy blocks** from `state` + calendar events, same rules as `eval/profiles/validate_profiles.py` (matches grader)
- **Only events** scheduled (clubs have no meeting times, can still be recommended)
- LLM generates up to **5 candidate plans** from retrieved activities, checks with `free_time`/`check_plan` tools, final plans re-checked in code
- **Calendar tools** (`agent/calendars.py`): one interface (`get_events`, `add_event`, `remove_event`) for `SimulatedCalendar` (eval: optional starting events, `fail_on` failure injection) and `GoogleCalendar` (REST API with requests, no Google client library)
  - Reads the primary calendar as busy time, **writes to a separate ECAgent calendar** (easy to find/clear, main calendar untouched)
  - `agent_added` flag: only activities the agent added count toward weekly caps (classes/shifts on a real calendar don't)
  - **Verified adds**: every add/remove is read back; a failed add is retried once, after checking it didn't go through (no duplicates). Only verified adds are reported as added
- **Replanning** (task 7): agent-added activities that no longer fit (`find_conflicts`, deterministic) are left out of busy time; the new plan replaces them on approval (removed, then added)

### Tools (`agent/gmail.py`, `agent/registration.py`)
- **Code-called tools**, not LLM tool calls: the router picks a step, code runs it with the stored plan, so the model never writes to a real calendar by itself
- **Approval check**: separate yes/no LLM call on the student's reply; `register`/`calendar` are offered to the router and executed only after a yes (enforced in `run`, not by prompt)
- **Gmail, read only** (`gmail.readonly`): Gmail search narrows to likely messages, the LLM extracts events (no standard format), code validates: real dates/times, the quoted date/time evidence must appear in the email, past events dropped, every item keeps its message id. Commitments without a verified time are dropped. Calendar invites (`.ics`) parsed by code
- Email activities -> retriever candidates (scored like DB results); email commitments -> busy time. Off in eval (profiles have no inbox)
- **Registration**: `SimulatedRegistration` (eval, `fail_on` for task 9, retried once by the agent); `ManualRegistration` for real use (no campus registration API: gives the link, never claims the student is registered)
- Secrets outside the repo; one-time browser login (`scripts/google_login.py`, the only use of `google-auth-oauthlib`)
