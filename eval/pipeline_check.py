# Pipeline check: one trial per agent/ablation on every eval_instance (36 x 3 = 108 trials), to check the
# whole pipeline (every fixture, every task's checks, every agent) before the real eval. NOT the eval:
# one trial, draft fixtures. Results go to eval/results/pipeline-check.json (not results.json), written
# up in docs/pipeline-check.md.
#
# Run from the repo root (real API calls, needs ANTHROPIC_API_KEY):
#   python -m eval.pipeline_check

from eval.eval import AGENTS, ROOT, Evaluator
from eval.fixtures import build_fixtures

OUT = ROOT / "eval" / "results" / "pipeline-check.json"


def main():
    build_fixtures.main()
    ev = Evaluator("pipeline check: 1 trial per agent per eval_instance, draft fixtures", trials=1)
    batch = ev.run(path=OUT)  # saved after every trial
    rep = ev.report(batch["trials"])
    for agent in AGENTS:
        trials = [t for t in batch["trials"] if t["agent"] == agent]
        picked = [t for t in trials if t["picked"]]  # empty picks have no automatic checks (manual nothing_fits)
        passed = sum(t["auto_grade"]["pass"] for t in picked)
        print(f"{agent:12} automatic checks passed {passed}/{len(trials)} "
              f"({len(picked) - passed} failed, {len(trials) - len(picked)} empty picks left for manual grading)")
    print("full_agent required actions pass^1:", rep["action_pass^3"]["pass_k"])
    print(f"wrote {len(batch['trials'])} trials to {OUT}")


if __name__ == "__main__":
    main()
