from utils import Utils, TOKEN_URL

class Memory(Utils):
    def __init__(self, memory=[], max_tokens=1024):
        self.msgs = memory
        self.count = 0 # TODO update later
        self.max = max_tokens
    
    def get_msgs(self):
        return self.msgs
    
    def set_msgs(self, new_memory):
        self.msgs = new_memory

    def is_memory_full(self):
        return self.count >= self.max