# Stream Printing Messages

The AgentScope agent is designed to communicate with the user and the other agents by passing messages explicitly.
However, we notice the requirements that obtain the printing messages from the agent in a streaming manner.
Therefore, in example we demonstrate how to achieve this functionality by using the `stream_printing_messages` pipeline.


## Quick Start

Run the following command to see the streaming printing messages from the agent.
Note the messages with the same ID are the chunks of the same message in accumulated manner.

```bash
python main.py
```

> Note: The example is built with DashScope chat model. If you want to change the model in this example, don't forget
> to change the formatter at the same time! The corresponding relationship between built-in models and formatters are
> list in [our tutorial](https://doc.agentscope.io/tutorial/task_prompt.html#id1)