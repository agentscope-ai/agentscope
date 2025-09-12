# Multiagent Concurrent

This example demonstrates how to run multiple agents concurrently in AgentScope, where each agent operates
independently and can perform tasks simultaneously.

Specifically, we showcase two ways to achieve concurrency:

- Using Python's `asyncio.gather` to run multiple agents asynchronously.
- Using `fanout_pipeline` to execute multiple agents in parallel and gather their results.

The fanout pipeline will distribute the input to multiple agents and collect their outputs, which is appropriate for
scenarios like voting or parallel question answering.

## QuickStart

Install the agentscope package if you haven't already:

```bash
pip install agentscope
```

Then run the example script:

```bash
python main.py
```

## Further Reading
- [Pipelines](https://doc.agentscope.io/tutorial/task_pipeline.html)
