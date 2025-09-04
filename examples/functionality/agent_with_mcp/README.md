# Agent with MCP Tools Example

This example demonstrates how to use AgentScope's ReAct agent with Model Context Protocol (MCP) tools using different transports. It showcases:

- Registering MCP tools with different transports (SSE and Streamable HTTP)
- Using a ReAct agent with registered MCP tools
- Getting structured output from the agent

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

## Setup

### 1. Environment Configuration

Set up your DashScope API key:

```bash
export DASHSCOPE_API_KEY="your_dashscope_api_key_here"
```

You can obtain a DashScope API key from [Alibaba Cloud DashScope Console](https://dashscope.console.aliyun.com/).

### 2. Start MCP Servers

Before running the agent, you need to start the MCP servers that provide the tools:

```bash
cd examples/agent_with_mcp
python mcp_servers.py
```

This will start a server on `http://127.0.0.1:8001` with two MCP tools:
- An addition tool available via SSE transport at `/sse_app/sse`
- A multiplication tool available via Streamable HTTP transport at `/streamable_http_app/mcp/`

## Usage

After starting the MCP servers, you can run the agent example:

```bash
cd examples/agent_with_mcp
python main.py
```

The agent will:
1. Register the MCP tools from the servers
2. Use a ReAct agent to solve a calculation problem (multiplying two numbers and then adding another number)
3. Return structured output with the final result