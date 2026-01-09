# Agent Skills in AgentScope

[Agent Skill](https://claude.com/blog/skills) is an approach proposed by
Anthropic to improve agent capabilities on specific tasks.

In this example, we demonstrate how to integrate Agent Skills into an
ReAct agent in AgentScope via the `toolkit.register_agent_skill` API.

Specifically, we prepare a demonstration skill that helps the agent to
learn about the AgentScope framework itself in the `skill` directory.
In `main.py`, we register this skill to the agent's toolkit, and ask it
to answer questions about AgentScope.

## Quick Start

Install the latest version of AgentScope to run this example:

```bash
pip install agentscope --upgrade
```

Then, run the example with:

```bash
python main.py
```

> Note:
> - The example is built with DashScope chat model. If you want to change the model used in this example, don't
> forget to change the formatter at the same time! The corresponding relationship between built-in models and
> formatters are list in [our tutorial](https://doc.agentscope.io/tutorial/task_prompt.html#id1)
> - For local models, ensure the model service (like Ollama) is running before starting the agent.

