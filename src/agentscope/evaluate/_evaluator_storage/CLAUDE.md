```
Module: `src/agentscope/evaluate/_evaluator_storage`
Responsibility: Persistent storage and retrieval of agent evaluation results and metrics data.

Key Types: `EvaluationStorage`, `ResultRecord`, `MetricAggregate`

Key Functions/Methods
- `save_evaluation(evaluation_id, results)` — stores comprehensive evaluation outcomes
  - Purpose: Provides durable persistence for agent performance data across evaluation sessions
  - Inputs: Evaluation identifier, result dictionaries with scores and metadata
  - Returns: Storage confirmation with potential optimization hints
  - Side-effects: Disk I/O operations, data serialization, versioning

Related SOP: `docs/evaluate/SOP.md`