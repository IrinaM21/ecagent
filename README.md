# ecagent
Agentic AI assistant for discovering and planning extracurricular activities

## Repo Structure

```
ecagent/
├── agent/                  # Agent implementation
│   ├── __main__.py         
│   ├── TBD                 # TBD
│   └── ...
│
├── docs/                   # Project documentation
│   ├── architecture.md
│   ├── benchmark.md
│   └── evaluation.md
│
├── eval/                   # Benchmark and evaluation
│   ├── eval.py             # Evaluation pipeline script (rule-based + single-pass + compact)
│   ├── rule-based.py       # Comparison against rule-based scheduler
│   ├── single-pass.py      # Comparison against single-pass Sonnet 4-5
│   ├── compact-eval.py     # Test if relevance preserved after compaction
│   ├── profiles/           # Simulated student profiles
│   └── ...
│
├── unit_tests/             # Unit and integration tests
│
├── LICENSE
└── README.md
```
