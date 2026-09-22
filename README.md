# ecagent
Agentic AI assistant for discovering and planning extracurricular activities

## Repo Structure

```
ecagent/
├── agent/                          # Agent implementation
│   ├── __main__.py         
│   ├── agent.py                    # Agent pipeline
│   ├── compactor.py                # Context compaction
│   ├── memory.py                   # Store user info, call compactor if approaching context limit
│   ├── scheduler.py                # Generate feasible schedule with existing commitments + ECAs
│   └── ...
│
├── docs/                           # Project documentation
│   ├── architecture.md
│   ├── benchmark.md
│   └── evaluation.md
│
├── eval/                           # Benchmark and evaluation
│   ├── eval.py                     # Evaluation pipeline script (rule-based + single-pass + compact)
│   ├── rule-based.py               # Comparison against rule-based scheduler
│   ├── single-pass.py              # Comparison against single-pass Sonnet 4-5
│   ├── compact-eval.py             # Test if relevance preserved after compaction
│   ├── simulated-profiles.json     # Simulated student profiles
│   ├── results.json                # Evaluation results for each version + profile/task combo
│   ├── benchmark-tasks.json        # Benchmark task ids + descriptions
│   └── ...
│
├── unit_tests/                     # Unit and integration tests
│   ├── test_eval.py                # Unit tests for eval script
│
├── LICENSE
└── README.md
```
