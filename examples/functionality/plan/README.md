# Plan with ReAct Agent

This example demonstrates how to use the plan module in AgentScope to make an agent create and manage a plan formally.

Specifically, we provide two examples: manual specification plan and Agent-managed plan.

## Manual Specification Plan

In this example, we first manually specify a plan for the agent to follow, then we let the agent execute the plan step by step.

To execute this example, run:

```bash
python main_manual_plan.py
```

## Agent-managed Plan

In this example, we let the agent create and manage its own plan.
Specifically, we use a query "Review the recent changes in AgentScope GitHub repository over the past month."

To run the example, execute:

```bash
python main_agent_managed_plan.py
```

> Note: The example is built with DashScope chat model. If you want to change the model in this example, don't forget
> to change the **formatter** at the same time! The corresponding relationship between built-in models and formatters
> are list in [our tutorial](https://doc.agentscope.io/tutorial/task_prompt.html#id1)