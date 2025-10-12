```
Module: `src/agentscope/plan`
Responsibility: ReAct-based long-term planning framework for agents.
Key Types: `Planner`, `PlanStep`, `PlanContext`

Key Functions/Methods
- `generate_plan(objective, context)` — creates multi‑step plans for complex agent tasks
  - Purpose: Enables agents to break down complex goals into executable action sequences

Key Methods
- `execute_plan(plan, agent)` — orchestrates plan execution with state tracking
  - Inputs: Agent objective, available context
  - Returns: Plan object with steps, constraints, and execution state
  - Side‑effects: Updates plan execution state, modifies agent memory