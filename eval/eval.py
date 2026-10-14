# TODO single-pass evaluation on benchmark tasks
# TODO rule-based scheduler evaluation
# TODO documentation (in ./doc, docstrings, comments)
# TODO add regression report, rule based + single pass + compact separate

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
        self.m = Memory()

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
    
    def get_query(self, p, t):
        return "This is a query" # TODO update
    
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
            self.m.set_msgs(p) # TODO update
            for t in self.tasks:
                q = self.get_query(p, t)
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



    
    #### V1 #### 
    """ def run(self):
        # adapted LEC9.10 for our design:
        # rule-based + single-pass = 1 trial

        results = {
            "trial_desc":self.desc
            "trial_results":{},
            "pass_at_k":{},
            "pass_to_k":{},
            "overall_pass_at_k":0.0,
            "overall_pass_to_k":0.0
        }

        for profile in self.profiles:
            results[profile] = {}

            for task_id in self.tasks:

                # since we consider each profile + task combo 1 eval instance
                total_id = f"{profile}_{task_id}"

                # no compaction, proposal says it's separate and it doesn't use tasks for now
                # Run rule-based evaluation
                r_eval = RuleBasedEvaluator(task, profile)
                r_r = r_eval.evaluate()

                # Run single-pass evaluation
                s_eval = SinglePassEvaluator(task, profile)
                s_r = s_eval.evaluate()

                # for now, treat each eval type as 1 trial
                results[total_id] = {
                    "rule_based": r_r,
                    "single_pass": s_r
                }
            # Run compact evaluation
            c_eval = CompactEvaluator(self.limit, profile)
            results[total_id]["compaction"] = c_eval.evaluate()
        
        save_results(results, './results.json')

        return results
 """