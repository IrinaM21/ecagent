import json
import re

from utils import Utils


class Compactor(Utils):
    def __init__(self, api_key=None):
        super().__init__(api_key)
        # prompt format: role (this system prompt), instructions, info in XML tags, response format
        # (based on CS 498 Using and Understanding AI (FA26), Intro to Prompting lecture)
        self.sys = """You are a context compactor tasked with compressing
        the information in memory to avoid exceeding the token limit."""

    def resolve_contradictions(self, state, new_msgs, usage=None):
        """
        Steps 3+4 for the structured 'state' note: find facts in new_msgs that contradict
        the state, keep the newer one, add new facts. Memory calls this whenever new info
        arrives (not only near the token limit), so changes are applied even in short histories.

        Args:
            state (dict): current 'state' note (same schema as intake_profile)
            new_msgs (list): new messages in Messages API format, oldest first
            usage (dict): optional token counter, see Utils.call

        Returns:
            (dict, list): updated state, and the contradictions found ("old fact -> new fact").
                          On a parse failure the old state is returned unchanged, so the
                          scheduler never sees broken JSON.
        """
        new_info = "\n".join(f"{m['role']}: {m['content']}" for m in new_msgs if isinstance(m["content"], str))

        prompt = f"""You maintain a student's profile for an extracurricular-activity assistant.
        Below is the current profile (JSON) and new information from the conversation, oldest first.

        1. List every contradiction: a fact in the new information that changes a fact in the profile
           or in an earlier message (schedule, constraint, interest, dislike). The newer statement wins.
        2. Return the updated profile: apply the newer side of each contradiction and add any new facts.
           Do not invent facts that are not stated. Keep exactly the same JSON keys and value formats
           (24-hour HH:MM times, YYYY-MM-DD dates, Mon..Sun days).

        <profile>{json.dumps(state)}</profile>
        <new_information>{new_info}</new_information>

        Reply in this format:
        <contradictions>one per line: old fact -> new fact (or "none")</contradictions>
        <state>{{...updated profile JSON...}}</state>"""

        text = self.text_of(self.call([{"role": "user", "content": prompt}], usage=usage))

        found = re.search(r"<contradictions>(.*?)</contradictions>", text, re.S)
        lines = found.group(1).splitlines() if found else []
        contradictions = [l.strip() for l in lines if l.strip() and l.strip().lower() != "none"]

        match = re.search(r"<state>\s*(\{.*\})\s*</state>", text, re.S)
        try:
            return json.loads(match.group(1)), contradictions
        except (AttributeError, json.JSONDecodeError):
            return state, contradictions


    @staticmethod
    def numbered(text):
        """'1. foo\n2. bar' -> ['foo', 'bar'] (ignores lines that aren't numbered)."""
        return [m.group(1).strip() for m in re.finditer(r"^\s*\d+[.)]\s*(.+)$", text, re.M)]

    def compact_user_info(self, previous_summary, msgs, usage=None):
        """
        Compact the conversation (+ the previous summary) into a numbered list of compressed
        salient points, with contradictions resolved. Memory calls this near the token limit.

        Args:
            previous_summary (str): notes['summary'] from an earlier compaction, or None
            msgs (list): conversation in Messages API format
            usage (dict): optional token counter, see Utils.call

        Returns:
            str: the new summary, a numbered list with one point per line
        """
        convo = "\n".join(f"{m['role']}: {m['content']}" for m in msgs if isinstance(m["content"], str))
        ask = lambda prompt: self.text_of(self.call([{"role": "user", "content": prompt}], system=self.sys, usage=usage))

        # 1: model - identify salient points, oldest first (the order tells step 4 which statement is newer)
        salient = ask(f"""Given the previous summary and the conversation below, identify the salient points
        about the student: schedule, constraints, interests, dislikes, goals, preferences.
        Skip small talk and anything unrelated to finding activities.
        <previous summary>{previous_summary or "none"}</previous summary>
        <conversation>{convo}</conversation>
        Your response should be a numbered list of salient points, oldest first, with each point on a new line.""")

        # 2: model - compress each point, keeping exact times/dates/numbers
        points = self.numbered(ask(f"""For each salient point in the given list, compress it so that it retains its meaning.
        Keep every time, date, day and number exactly. Keep the same order.
        <salient points>{salient}</salient points>
        Your response should be a numbered list of compressed salient points, with each point on a new line."""))
        listing = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(points))

        # 3: model - identify contradictions as (N, M) pairs
        text = ask(f"""Given the numbered list of compressed salient points, identify any contradictions
        (two points that cannot both be true now, e.g. a schedule or preference that changed).
        <points>{listing}</points>
        Your response should be one contradiction per line as a tuple (N, M), or "none".""")
        pairs = [(int(a) - 1, int(b) - 1) for a, b in re.findall(r"\((\d+),\s*(\d+)\)", text)]

        # 4: model - for each contradiction, decide which statement remains
        drop = set()
        for a, b in pairs:
            if a == b or not (0 <= a < len(points) and 0 <= b < len(points)):
                continue  # skip malformed pairs instead of crashing
            old, new = min(a, b), max(a, b)
            choice = ask(f"""For the given pair of contradicting statements, decide which should remain in the
            compressed memory. Statement 1 was said before statement 2. Keep both if they apply at
            different times (e.g. a one-time exception to a weekly rule).
            <statement 1>{points[old]}</statement 1>
            <statement 2>{points[new]}</statement 2>
            Return only 1, 2, or both. Your choice:""").strip().lower()
            if choice.startswith("1"):
                drop.add(new)
            elif choice.startswith("2"):
                drop.add(old)

        # 5: memory = the remaining points, original order kept
        kept = [p for i, p in enumerate(points) if i not in drop]
        return "\n".join(f"{i + 1}. {p}" for i, p in enumerate(kept))
