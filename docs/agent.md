# Assistant

## Introduction

### Problem

### Limitations

### Approach

### Results

### Overview

## Related Work

### Similar Agents
- CoS [1]
- ScheduleMe [2]
- Calendar.help [3]

### Technical Approaches

CoS: [1]

ScheduleMe: [2]

Calendar.help: [3]

### Comparison

## Agent Design

![agent-architecture](./images/arch.png)

### Components
- (`agent/agent.py`): Interfaces with the user and coordinates actions [2]. Interprets the query and validates ECAgent response [3].
- (`agent/retriever.py`): Discovers relevant events from event info cached in `scraper/db.py` (scraped weekly by `scraper/scrape.py`). If there are insufficient relevant results (k < 3 at approval score threshold = 7), appends info found with web_search / web_fetch. Converts event info to a common structured representation [4].
- (`agent/memory.py`): Persistent memory (structured note-taking). Calls context management (`agent/compactor.py`) module if near token limit, returns relevant user interest and preference info.
- (`agent/compactor.py`): Identifies salient details, contradictions in persistent memory --> LLM-generated summary (`Claude Sonnet 4.6`)
- (`agent/recommender.py`): Ranks retrieved event info (`agent/retriever.py`) and generated plans (`agent/scheduler.py`) [1] according to the query and retrieved user info (`agent/memory.py`).
- (`agent/scheduler.py`): Generates candidate schedules, checks them against hard constraints. The final selection (`agent/recommender.py`) is returned to the main agent (`agent/agent.py`) for validation and user approval.

### Pipeline
Supervisor loop [2]: for each student message, memory resolves contradictions and manages context, then the router (`agent/agent.py`) picks the next action from the task + results so far + user input (retriever, recommender, scheduler, calendar, or reply to the student). Subagents return their results to the agent. Each turn ends with a reply, checked against the activity data. Plans are only added to the calendar after the student approves them.

## Experimental Setup

1. `eval/profiles/simulated-profiles.json` --> load simulated student `profiles` with preferences, interests, + hard (scheduling) constraints
2. `eval/benchmark-tasks.json` --> load `tasks`; `eval/fixtures/<task>.json` --> load each task's fixture (a small synthetic events/clubs database + an answer key saying which activities are correct for each profile, written before any run and frozen)
3. Load: `full_agent` (our design), `no_compact` (`full_agent` with naive truncation in place of `agent/compactor.py`), `single_pass` (`full_agent` - `agent/compactor.py` - `agent/memory.py` - `agent/retriever.py` - `agent/scheduler.py` + manually input user preferences, constraints + event catalog in the prompt + web search tools, one model call per turn). Web search is off for every agent in the eval (see `docs/design-decisions.md`)
4. Pre-compute the cross product (`profiles` x `tasks`), limited to each task's profiles --> 36 evaluative instance pairs.
5. For each agent/ablation: (1) randomly select an instance (2) run it for 3 trials 
6. Score results: (1) pass^3 (auto in loop: picks checked against the fixture's answer key + hard constraints; the reply text checks are labeled manually, blind; required actions are a separate `full_agent` score, not compared to baselines), (2) manually grade subjective quality from 1 to 5
7. Report results: Overall pass/fail (`pass_overall=True` if average pass^3 per instance is at least 10 percentage points higher than both baselines', mean per-trial quality scores >= 4, with standard deviation <= 0.5; else, `False`) + pass^3 and quality score for each instance/trial

## Results

## Discussion

## Conclusion

## References
[1] @misc{zhao2025cosoptimaleventscheduling,
      title={CoS: Towards Optimal Event Scheduling via Chain-of-Scheduling}, 
      author={Yiming Zhao and Jiwei Tang and Shimin Di and Libin Zheng and Jianxing Yu and Jian Yin},
      year={2025},
      eprint={2511.12913},
      archivePrefix={arXiv},
      primaryClass={cs.AI},
      url={https://arxiv.org/abs/2511.12913}, 
      }
[2] @misc{wijerathne2026schedulememultiagentcalendarassistant,
      title={ScheduleMe: Multi-Agent Calendar Assistant}, 
      author={Oshadha Wijerathne and Amandi Nimasha and Dushan Fernando and Nisansa de Silva and Srinath Perera},
      year={2026},
      eprint={2509.25693},
      archivePrefix={arXiv},
      primaryClass={cs.AI},
      url={https://arxiv.org/abs/2509.25693}, 
      }
[3] @inproceedings{Cranshaw_2017, series={CHI ’17},
    title={Calendar.help: Designing a Workflow-Based Scheduling Agent with Humans in the Loop},
    url={http://dx.doi.org/10.1145/3025453.3025780},
    DOI={10.1145/3025453.3025780},
    booktitle={Proceedings of the 2017 CHI Conference on Human Factors in Computing Systems},
    publisher={ACM},
    author={Cranshaw, Justin and Elwany, Emad and Newman, Todd and Kocielnik, Rafal and Yu, Bowen and Soni, Sandeep and Teevan, Jaime and Monroy-Hernández, Andrés},
    year={2017},
    month=May, pages={2382–2393},
    collection={CHI ’17} }

[4] @article{10.1145/1869397.1869401,
    author = {Refanidis, Ioannis and Yorke-Smith, Neil},
    title = {A constraint-based approach to scheduling an individual's activities},
    year = {2010},
    issue_date = {November 2010},
    publisher = {Association for Computing Machinery},
    address = {New York, NY, USA},
    volume = {1},
    number = {2},
    issn = {2157-6904},
    url = {https://doi.org/10.1145/1869397.1869401},
    doi = {10.1145/1869397.1869401},
    abstract = {The goal of helping to automate the management of an individual's time is ambitious in terms both of knowledge engineering and of the quality of the plans produced by an AI system. Modeling an individual's activities is itself a challenge, due to the variety of activity, constraint, and preference types involved. Activities might be simple or interruptible; they might have fixed or variable durations, constraints over their temporal domains, and binary constraints between them. Activities might require the individual being at specific locations in order, whereas traveling time should be taken into account. Some activities might require exclusivity, whereas others can be overlapped with compatible concurrent activities. Finally, while scheduled activities generate utility for the individual, extra utility might result from the way activities are scheduled in time, individually and in conjunction.This article presents a rigorous, expressive model to represent an individual's activities, that is, activities whose scheduling is not contingent on any other person. Joint activities such as meetings are outside our remit; it is expected that these are arranged manually or through negotiation mechanisms and they are considered as fixed busy times in the individual's calendar. The model, formulated as a constraint optimization problem, is general enough to accommodate a variety of situations. We present a scheduler that operates on this rich model, based on the general squeaky wheel optimization framework and enhanced with domain-dependent heuristics and forward checking. Our empirical evaluation demonstrates both the efficiency and the effectiveness of the selected approach. Part of the work described has been implemented in the SelfPlanner system, a Web-based intelligent calendar application that utilizes Google Calendar.},
    journal = {ACM Trans. Intell. Syst. Technol.},
    month = dec,
    articleno = {12},
    numpages = {32},
    keywords = {Greedy algorithms, activity modeling, constraints, intelligent calendar applications, preferences}
    }