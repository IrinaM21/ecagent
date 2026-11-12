import json

from utils import Utils
"""
Based on Lecture 8 (CS 498 FA26, Using and Understanding AI Agents):

'For long-horizon tasks, you can also do structured note-taking, where an agent 
basically writes to a set of notes, which you can then read, and then basically 
restart the agent loop with a refreshed context.

This approach allows agents to persist important information and reload it when 
needed, effectively extending their memory beyond the context window.

e.g. context window <---> Notes.md Action: Training in Route 1... Write to notes: 
Pikachu gained 8 levels'
"""

class Memory(Utils):
    def __init__(self, compactor=None, memory=None, max_tokens=1024, api_key=None):
        """
        notes is a dict, see docs/benchmark.md

        Args:
            compactor (Compactor): context management module. None = no_compact ablation:
                                   no contradiction step, naive truncation near the limit.
        """
        super().__init__(api_key)
        # new dict per instance: a shared `memory={}` default would leak notes between eval runs
        self.notes = memory if memory is not None else {}
        self.msgs = []  # conversation so far, Messages API format

        self.count = 0  # tokens in self.msgs, updated by load() / add_message()
        self.max = max_tokens

        self.c = compactor

    def get_msgs(self):
        return self.msgs

    def get_notes(self):
        return self.notes
    
    def set_notes(self, new_memory):
        self.notes = new_memory

    def is_memory_full(self):
        return self.count >= self.max
    

    def write_note(self, key, value):
        """
        Write a note with the given key.

        Args:
            key (str): The key for the note
            value: The value to store (can be any type)
        """
        # Store the note
        self.notes[key] = value

    def read_note(self, key):
        """
        Read a note by key.

        Args:
            key (str): The key to look up

        Returns:
            The stored value, or None if not found
        """
        # Retrieve the note
        return self.notes.get(key, None)

    def load(self, conversation_history, intake_profile, usage=None):
        """
        Fresh state for one eval run: the chat so far + a structured 'state' note.
        Only pass agent_visible fields here, never the whole profile (see docs/benchmark.md).

        Args:
            conversation_history (list): agent_visible.conversation_history
            intake_profile (dict): agent_visible.intake_profile; starts the 'state' note,
                                   which update_state() rewrites from the chat
        """
        self.msgs = [dict(m) for m in conversation_history]  # copy so the profile object is never mutated
        self.write_note("state", json.loads(json.dumps(intake_profile)))  # deep copy
        # count_tokens rejects an empty message list, so skip the call when there is no history
        self.count = self.count_tokens(self.msgs) if self.msgs else 0
        # the whole history is new info relative to the intake profile
        self.resolve(self.msgs, usage)
        # context management belongs to memory: compact/truncate now if the history is already over the limit (P5)
        self.manage_context(usage)

    def save(self, path):
        """Write memory (notes + conversation) to a JSON file so a later session can restore it."""
        with open(path, "w") as f:
            json.dump({"notes": self.notes, "msgs": self.msgs, "count": self.count}, f, indent=1)

    def restore(self, path):
        """Load memory saved by save(), for returning users (eval loads from the simulated profiles instead)."""
        with open(path) as f:
            saved = json.load(f)
        self.notes, self.msgs, self.count = saved["notes"], saved["msgs"], saved["count"]

    def add_message(self, msg, usage=None):
        """
        Append one turn to the conversation. For user turns (new info from the student,
        incl. runtime updates), apply contradictions/new facts to the 'state' note right away.

        Args:
            msg (dict): {"role": ..., "content": ...}
            usage (dict): optional token counter, see Utils.call
        """
        self.msgs.append(msg)
        if msg["role"] == "user" and isinstance(msg["content"], str):
            self.resolve([msg], usage)
        self.count = self.count_tokens(self.msgs)
        self.manage_context(usage)  # context management belongs to memory

    def manage_context(self, usage=None):
        """
        Near the token limit: the compactor summarizes the conversation into notes['summary']
        and the message history restarts empty (full agent), or the oldest turns are dropped
        (no_compact). Either way memory ends up under self.max.

        Args:
            usage (dict): optional token counter, see Utils.call
        """
        if not self.is_memory_full():
            return
        before = self.count
        # always keep the newest user message (the agent is about to answer it, and the API needs one)
        keep = self.msgs[-1:] if self.msgs and self.msgs[-1]["role"] == "user" else []
        older = self.msgs[:len(self.msgs) - len(keep)]
        if self.c is not None:
            if older:
                # fold the previous summary in, so repeated compactions don't lose older facts
                summary = self.c.compact_user_info(self.read_note("summary"), older, usage)
                self.write_note("summary", summary)
            self.msgs = keep
        else:
            # estimate (chars / 4) while dropping the oldest turns, exact count at the end
            size = lambda msgs: sum(len(m["content"]) if isinstance(m["content"], str) else 0 for m in msgs) // 4
            while older and size(older + keep) > self.max:
                older = older[1:]
            while older and older[0]["role"] != "user":
                older = older[1:]  # the conversation must still start with a user turn
            self.msgs = older + keep
        self.count = self.count_tokens(self.msgs) if self.msgs else 0
        self.log("compact" if self.c else "truncate", tokens_before=before, tokens_after=self.count)

    def resolve(self, new_msgs, usage=None):
        """
        Update the 'state' note from new_msgs with the compactor's contradiction step and
        log what changed to agent/model_log.json. Does nothing in the no_compact ablation.
        """
        # only the student states facts about themselves, so skip assistant turns / tool results
        user_msgs = [m for m in new_msgs if m["role"] == "user" and isinstance(m["content"], str)]
        if self.c is None or not user_msgs:
            return
        old = self.read_note("state")
        state, found = self.c.resolve_contradictions(old, user_msgs, usage)
        self.write_note("state", state)
        self.log("resolve_contradictions", n_msgs=len(user_msgs), contradictions=found, state_changed=state != old)