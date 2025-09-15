# MCP in AgentScope

This example demonstrates how to

- create MCP client with different transports (SSE and Streamable HTTP) and type (Stateless and Stateful),
- register MCP tool functions and use them in a ReAct agent, and
- get MCP tool function as a local callable object from the MCP client.


## Prerequisites

- Python 3.10 or higher
- DashScope API key from Alibaba Cloud

## Installation

### Install AgentScope

```bash
# Install from source
cd {PATH_TO_AGENTSCOPE}
pip install -e .
```

## QuickStart

Install agentscope and ensure you have a valid DashScope API key in your environment variables.

> Note: The example is built with DashScope chat model. If you want to change the model in this example, don't forget
> to change the formatter at the same time! The corresponding relationship between built-in models and formatters are
> list in [our tutorial](https://doc.agentscope.io/tutorial/task_prompt.html#id1)

```bash
pip install agentscope
```

Start the MCP servers by the following commands in two separate terminals:

```bash
# In one terminal, run:
python mcp_add.py

# In another terminal, run:
python mcp_multiply.py
```

Two MCP servers will be started on `http://127.0.0.1:8001` (SSE server) and `http://127.0.0.1:8002` (streamable
HTTP server).

After starting the MCP servers, you can run the agent example:

```bash
python main.py
```

The agent will:
1. Register the MCP tools from the servers
2. Use a ReAct agent to solve a calculation problem (multiplying two numbers and then adding another number)
3. Return structured output with the final result
