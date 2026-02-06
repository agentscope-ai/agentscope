# High-code Deployment of a Routing Agent

This example demonstrates how to deploy a multi-agent system using AgentScope. The system is composed of a main
routing agent equipped with a tool function named `create_worker` to dispatch tasks to specialized worker agents.

Specifically, the routing agent is deployed as a chat endpoint in server hold by the `Quart` library.
Once receiving an input request, we
- set up a routing agent
- load the session state if any
- invoke the routing agent to handle the request, and return the streaming response
- save the session state


# Example Structure

```
planning_agent/
    ├── main.py              # Entry point to start the Quart server with routing agent
    ├── tool.py              # Tool function to create worker agents
    └── test_post.py         # Preset test script to send requests to the server
```


## Note

1. The printing messages from sub-agent/worker agents is converted to the streaming response of the tool
function `create_worker`, meaning the sub-agent won't be exposing to the user directly.

2. The sub-agent in `tool.py` is equipped with the following tools. For GitHub and AMap tools, they will be activated only
if the corresponding environment variables are set.
You can customize the toolset by modifying the `tool.py` file.

| Tool                  | Description                                         | Required Environment Variable |
|-----------------------|-----------------------------------------------------|-------------------------------|
| write/view text files | Read and write text files                           | -                             |
| Playwright MCP server | Automate browser actions using Microsoft Playwright | -                             |
| GitHub MCP server     | Access GitHub repositories and data                 | GITHUB_TOKEN                  |
| AMap MCP server       | Access AMap services for location-based tasks       | GAODE_API_KEY                 |


3. Optionally, you can also expose the sub-agent's response to the user by modifying the `tool.py` file.

## Quick Start

Install the latest agentscope and Quart packages:

```bash
pip install agentscope quart
```

Ensure you have set `DASHSCOPE_API_KEY` in your environment for DashScope LLM API, or change the used model in
both `main.py` and `tool.py` (Remember to change the formatter correspondingly).

Set the environment variables for GitHub and AMap tools if needed.

Run the Quart server:

```bash
python main.py
```

In another terminal, run the test script to send a request to the server:

```bash
python test_post.py
```
