# MultiAgent Debate

Debate workflow simulates a multi-turn discussion between different agents, mostly several solvers and an aggregator.
Typically, the solvers generate and exchange their answers, while the aggregator collects and summarizes the answers.

We implement the examples in [EMNLP 2024](https://aclanthology.org/2024.emnlp-main.992/), where two debater agents
will discuss a topic in a fixed order, and express their arguments based on the previous debate history.
At each round a moderator agent will decide whether the correct answer can be obtained in the current iteration.

## Setup

The example is built upon DashScope LLM API in [main.py](https://github.com/agentscope-ai/agentscope/blob/main/examples/workflows/multiagent_debate/main.py).
You can also change to the other LLMs by modifying the ``model`` and ``formatter`` parameters in the code.

To run the example, first install the latest version of AgentScope, then run:

```bash
python examples/workflows/multiagent_debate/main.py
```


> Note: The example is built with DashScope chat model. If you want to change the model in this example, don't forget
> to change the formatter at the same time! The corresponding relationship between built-in models and formatters are
> list in [our tutorial](https://doc.agentscope.io/tutorial/task_prompt.html#id1)