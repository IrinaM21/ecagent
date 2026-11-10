import re
from functools import cmp_to_key

from utils import Utils


class Recommender(Utils):
    def __init__(self, api_key=None):
        super().__init__(api_key)

    def rank_candidates(self, query, state, candidates, usage=None):
        """
        Order candidates for the scheduler: approval score first (from the retriever), then the
        student's ranked soft preferences, where satisfying S1 beats satisfying S2 and S3 together.
        Soft preferences only rank valid options, never reject them (docs/benchmark.md).
        Adds 'prefs' (list of satisfied soft-preference ranks) to each candidate.

        Args:
            query (str): the user's request
            state (dict): memory's 'state' note
            candidates (list[dict]): retriever output (to_common items with 'score')
            usage (dict): optional token counter, see Utils.call
        """
        prefs = sorted(state.get("soft_preferences", []), key=lambda p: p["rank"])
        if not candidates or not prefs:
            return candidates
        pref_list = "\n".join(f"S{p['rank']}. {p['preference']}" for p in prefs)
        listing = "\n".join(f"{i + 1}. [{c['kind']}] {c['title']} | {c['date'] or ''} {c['start'] or ''} | "
                            f"{c['location'] or ''} | {(c['description'] or '')[:300]}" for i, c in enumerate(candidates))

        prompt = f"""For each activity, list which of the student's preferences it clearly satisfies.
Only count a preference if the activity's details show it; when unsure, leave it out.

<request>{query}</request>
<preferences>
{pref_list}
</preferences>
<activities>
{listing}
</activities>

Reply with one line per activity in the format N: S1, S3 (or "N: none")."""

        text = self.text_of(self.call([{"role": "user", "content": prompt}], usage=usage))
        matched = {int(n) - 1: sorted(int(s) for s in re.findall(r"S(\d+)", rest))
                   for n, rest in re.findall(r"^\s*(\d+)\s*:(.*)$", text, re.M)}
        for i, c in enumerate(candidates):
            c["prefs"] = matched.get(i, [])
        # lexicographic: a candidate satisfying rank 1 sorts above one satisfying only ranks 2+3
        n = len(prefs)
        return sorted(candidates, key=lambda c: (c["score"], [r in c["prefs"] for r in range(1, n + 1)]), reverse=True)

    def compare_plans(self, query, state, plan_a, plan_b, usage=None):
        """
        Pairwise LLM judge: which of two valid weekly plans would this student
        prefer? Asks twice with A/B swapped, because LLM judges tend to favor one position;
        if the two answers disagree it's a tie.

        Args:
            query (str): the user's request
            state (dict): memory's 'state' note (soft preferences + goals are the criteria)
            plan_a, plan_b (list[dict]): candidates chosen by the scheduler
            usage (dict): optional token counter, see Utils.call

        Returns:
            int: 1 if plan_a is better, -1 if plan_b is better, 0 for a tie (works with cmp_to_key)
        """
        fmt = lambda plan: "\n".join(f"- {c['date'] or 'no date'} {c['start'] or ''}-{c['end'] or ''} "
                                     f"{c['title']} ({c['location'] or 'location n/a'})" for c in plan) or "- (empty)"
        criteria = [f"S{p['rank']} (rank {p['rank']}): {p['preference']}"
                    for p in sorted(state.get("soft_preferences", []), key=lambda p: p["rank"])]
        criteria += [f"goal: {g}" for g in state.get("goals", [])]

        def judge(first, second):
            msg = f"""You are an LLM judge. Choose which of plan A and plan B this student would prefer for the
given request, based on the criteria (lower rank = more important), and explain your choice.
Both plans already fit the student's schedule and rules.

<request>{query}</request>
<A>
{fmt(first)}
</A>
<B>
{fmt(second)}
</B>
<criteria>
{chr(10).join(criteria)}
</criteria>

Respond with ONLY "A" or "B" on the first line, and the next line MUST have your explanation."""
            r = self.text_of(self.call([{"role": "user", "content": msg}], max_tokens=512, usage=usage)).split("\n")
            p = r[0].strip().upper()
            return p if p in ("A", "B") else None  # unclear answer -> no vote (tie), not a default "A"

        forward, swapped = judge(plan_a, plan_b), judge(plan_b, plan_a)
        if forward == "A" and swapped == "B":
            return 1
        if forward == "B" and swapped == "A":
            return -1
        return 0

    def rank_plans(self, query, state, plans, usage=None):
        """
        Sort the scheduler's candidate plans best-first, using compare_plans as the comparison.
        sorted() is stable, so tied plans keep the scheduler's order (which follows rank_candidates).
        The comparisons made and the final order are written to agent/model_log.json.

        Args:
            query (str): the user's request
            state (dict): memory's 'state' note
            plans (list[list[dict]]): candidate plans from the scheduler (all valid)
            usage (dict): optional token counter, see Utils.call

        Returns:
            list: ranked plans, best first
        """
        if len(plans) < 2:
            return list(plans)
        index = {id(p): i for i, p in enumerate(plans)}  # plan position in the scheduler's list, for the log
        comparisons = []

        def cmp(a, b):
            r = self.compare_plans(query, state, a, b, usage)
            comparisons.append({"a": index[id(a)], "b": index[id(b)], "result": r})
            return -r  # sorted() puts smaller first, and compare_plans returns 1 when a is better

        ranked = sorted(plans, key=cmp_to_key(cmp))
        self.log("rank_plans", comparisons=comparisons, order=[index[id(p)] for p in ranked],
                 plans=[[c["title"] for c in p] for p in plans])
        return ranked

    def summarize(self, ranked, kind):
        """Short summary of rank_candidates() / rank_plans() output for the agent's router.
        kind: "activities" or "candidate plans"."""
        return f"recommender: ranked {len(ranked)} {kind}"
