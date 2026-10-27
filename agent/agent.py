from compactor import Compactor
from scheduler import scheduler
from memory import Memory
from utils import Utils, API_URL

class ECAgent(Utils):
    def __init__(self, api_key, memory, no_compact=False, single_pass=False, rule_based=False):
        self.key = api_key

        self.s = Scheduler(rule_based)

        self.m = Memory()

        # self.manage_context = manage_context # default: manage context
        self.single_pass = single_pass
        self.c = None if no_compact else Compactor() 

    # for eval pipeline tests #

    def generate_response(self, user_input):

        if self.single_pass:
            # TODO implement
            pass
        
        if self.rule_based:
            # TODO implement
            pass

        if self.manage_context and self.m.is_memory_full():
            
            self.m.set_msgs(self.c.compact_user_info(self.m.get_msgs(), user_input))
            self.manage_context = False

        msgs = self.m.get_msgs()
        
        ans = self.post_to_url(API_URL, self.get_headers(self.key), self.get_json("claude-sonnet-4-5",1024,msgs))
    
        return ans.json()["content"][0]["text"].strip()
    
    def run(self, query):
        return self.generate_response(query)