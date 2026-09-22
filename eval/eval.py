# TODO single-pass evaluation on benchmark tasks
# TODO rule-based scheduler evaluation
# TODO documentation (in ./doc, docstrings, comments)
# TODO add regression report, rule based + single pass + compact separate

import requests
import json

from rule_based_eval import RuleBasedEvaluator
from single_pass_eval import SinglePassEvaluator
from compact_eval import CompactEvaluator

class Evaluator:
    def __init__(self, trial_description, token_limit):
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
    
    def save_results(self, results, path):
        # save the results to json when eval complete
        with open(path, 'w') as f:
            json.dump(results, f)

    def run(self):
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
