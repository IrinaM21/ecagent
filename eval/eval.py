# TODO documentation (in ./doc, docstrings, comments)

from agent import Agent
from memory import Memory

from rule_based_eval import RuleBasedEvaluator
from single_pass_eval import SinglePassEvaluator
from compact_eval import CompactEvaluator

from utils import Utils

class Evaluator(Utils):
    def __init__(self, trial_description, token_limit, trials=3, key):
        """
        Initialize the evaluator with
            desc: description (Args)
            tasks: dict of benchmark tasks [task_id -> task_description]
            profiles: list of simulated user profiles
            past_results: dict with results from previous evaluation runs
        Args:
            trial_description (str): description of the changes to the model (clearly know relationship 
                                    between results + changes)
        """
        self.desc = trial_description
        self.tasks = json.load(open('./tasks.json', 'r'))
        self.profiles = json.load(open('./simulated-profiles.json', 'r'))
        # self.past_results = json.load(open('./results.json', 'r')) uncomment when regression report implemented
        self.limit = token_limit # placeholder token limit for compacted info
        self.trials = trials
        # separate agents so we can run all in parallel, no leaks
        self.a = Agent(key=key)
        self.s = Agent(key=key, single_pass=True)
        self.r = Agent(key=key, rule_based=True)
        self.c = Agent(key=key, no_compact=True)
        # self.m = Memory()

    def save_results(self, results, path):
        # save the results to json when eval complete
        with open(path, 'w') as f:
            json.dump(results, f)

    # compare ptk percentages: must be at least 10 % pts higher
    # across tasks == (profile, task)
    def trial_res(self, a_ptk, b_ptk):
        return 10 <= ( a_ptk - b_ptk )

    def get_gpt_feedback(self, out):
        # placeholder, will load GPT-4o Mini on init later
        return 5
    
    # expect baseline to perform worse, so compare against evaluation plan thresholds
    # across all trials (3*n_tasks)
    def subj_res(self, m, s):
        return m >= 4 and s <= 0.5

    def passed(self, out):
        # placeholder

        # should check that suggested activities on time constraints,
        # (2) match to student preferences (TODO isn't this subjective - human feedback?)

        return True

    # run agent on task on the given query
    # returns per_trial results
    def run_task(self, query, a):
        ptk = 1
        sub = []
        for _ in range(len(self.trials)):
            out = a.run(query)
            ptk *= self.passed(out)
            sub += [self.get_gpt_feedback(out)]
        return ptk, sub
    
    def run(self):
        # TODO clean up
        a_ptk = []
        r_ptk = []
        s_ptk = []
        c_ptk = []
        # TODO consider sub for baselines, esp self.c
        a_sub = []
        for p in self.profiles:
            # new memory for each profile, just load msg history for now
            # TODO update
            if self.profiles[p]: # skip TBD profiles (long-context -> "")
                self.m.set_msgs(p) # TODO update
                for t in self.tasks:
                    query = self.task[t]
                    if self.task[t]: # skip TBD tasks (i.e. multi-step -> "")
                        p, s = run_task(query, self.a)
                        a_ptk += p
                        a_sub += s
                        p, _ = run_task(query, self.r)
                        r_ptk += p
                        p, _ = run_task(query, self.s)
                        s_ptk += p
                        p, _ = run_task(query, self.c)
                        c_ptk += p
        results = {
            "run":self.trial_desc, 
            "subjective":self.subj_res( sum(a_sub)/len(a_sub) , s) 
            "rule-based":(self.trial_res(a_ptk, r_ptk)),
            "single-pass":(self.trial_res(a_ptk, s_ptk)),
            "compaction":(self.trial_res(a_ptk, c_ptk))
            }
        return results