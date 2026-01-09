# ReAct Agent Example

This example showcases a **ReAct** agent in AgentScope. Specifically, the ReAct agent will discuss with the user in
an alternative manner, i.e., chatbot style. It is equipped with a suite of tools to assist in answering user queries.

> 💡 Tip: Try ``Ctrl+C`` to interrupt the agent's reply to experience the realtime steering/interruption feature!

## Quick Start

Ensure you have installed agentscope and set ``DASHSCOPE_API_KEY`` in your environment variables.

Run the following commands to set up and run the example:

```bash
python main.py
```

> Note:
> - The example is built with DashScope chat model. If you want to change the model used in this example, don't
> forget to change the formatter at the same time! The corresponding relationship between built-in models and
> formatters are list in [our tutorial](https://doc.agentscope.io/tutorial/task_prompt.html#id1)
> - For local models, ensure the model service (like Ollama) is running before starting the agent.
