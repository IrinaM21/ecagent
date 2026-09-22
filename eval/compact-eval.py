# score compacted info based on % manually selected relevant info included
# (loosely) based on: https://arxiv.org/pdf/2608.01326. does not use LLM response correctness 
# to check for query (relevant info)
# also checks if under limit

import json
from agent import Compactor, Memory

class CompactEvaluator:
    def __init__(self, token_limit, profile):
        self.limit = token_limit
        self.manual = json.load(open('./manual_compact.json', 'r')) # manually selected relevant user info
        # m = Memory() use memory when implemented?
        # full_memory = m.get_full_memory() # get full chat history from memory
        self.all_info = profile # using simulated profiles for now
        self.compactor = Compactor()

    def check_info(self, compacted_info, manual_info):
        # Check if all manually selected relevant info is included in the compacted info
        # for now: check presence of relevant info by searching for keywords/phrases (manual dict)
        info_score = sum(info in compacted_info for info in manual_info)/len(manual_info)
        return info_score

    def evaluate(self):
        res = {}

        c_info = self.compactor.compact(all_info) # Get compacted user info

        in_limit = len(c_info) <= self.limit # for now, actual token counting later
        info_score = self.check_info(c_info, self.manual[profile])
        
        return in_limit and info_score > 0.8 # threshold for info score, can be adjusted

        

        
        