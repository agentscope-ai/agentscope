```
Module: `src/agentscope/evaluate/_ace_benchmark`
Responsibility: ACE benchmark framework for agent evaluation and task orchestration.

Key Types: `AceBenchmark`, `AceMetric`, `AceToolManager`

Key Functions/Methods
- `run_benchmark(tasks, agents, metrics=None)` — executes comprehensive agent evaluation scenarios
  - Purpose: Coordinates task distribution, agent execution, and metric collection across benchmark tasks

Related SOP: `docs/evaluate/SOP.md`