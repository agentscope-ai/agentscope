```
Module: `src/agentscope/evaluate`
Responsibility: Distributed and parallel evaluation framework for multi-agent systems.
Key Types: `EvaluationMetric`, `DatasetLoader`, `Evaluator`

Key Functions/Methods
- `run_evaluation(agent, dataset, metrics)`
  - Purpose: Evaluates agent performance across multiple metrics
  - Inputs: Agent instances, evaluation datasets, metric configurations

Call Graph
- `evaluate_pipeline(agent, messages)` → `metric.compute()`
  - References: `src/agentscope/evaluate/__init__.py`

Related SOP: `docs/evaluation_workflow.md`