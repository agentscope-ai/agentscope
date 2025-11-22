# Human-in-the-Loop MCP in AgentScope

This example demonstrates how to:

- create a ReAct agent with tools that require **human approval** before execution,
- connect to an **MCP (Model Context Protocol)** server via SSE and register its tools into a `Toolkit`,
- use a **human-in-the-loop permit function** to approve, deny, or modify tool calls (including MCP tools) at runtime.

The agent will use:

- local tools: `execute_shell_command`, `execute_python_code`
- an MCP tool: `add_one` (provided by the `mcp_add_one.py` server)

For every tool call, the user is asked whether to:

- run the tool as-is,
- refuse the call, or
- edit the tool name and/or parameters before running.

## Prerequisites

- Python 3.10 or higher
- DashScope API key from Alibaba Cloud (`DASHSCOPE_API_KEY` in your environment)

## Installation

### Install AgentScope

```bash
# Install from source
cd {PATH_TO_AGENTSCOPE}
pip install -e .
```

Or install from PyPI:

```bash
pip install agentscope
```

## QuickStart

1. **Set your DashScope API key** in the environment:

   ```bash
   # Linux / macOS
   export DASHSCOPE_API_KEY=YOUR_API_KEY

   # Windows (PowerShell)
   setx DASHSCOPE_API_KEY "YOUR_API_KEY"
   ```

2. **Start the MCP server** (SSE transport) in one terminal:

   ```bash
   cd {PATH_TO_AGENTSCOPE}/examples/functionality/human_in_the_loop
   python mcp_add_one.py
   ```

   This will start an MCP server on `http://127.0.0.1:8001` exposing the `add_one` tool.

3. **Run the human-in-the-loop agent** in another terminal:

   ```bash
   cd {PATH_TO_AGENTSCOPE}/examples/functionality/human_in_the_loop
   python main.py
   ```

## What the Example Does

When you run `main.py`, the example will:

1. **Create a `Toolkit`** and register:
   - `execute_shell_command` (local tool, guarded by human approval),
   - `execute_python_code` (local tool, guarded by human approval),
   - `add_one` from the MCP server via a stateful `HttpStatefulClient` (also guarded by human approval).
2. **Create a ReAct agent** named `Friday` using the DashScope chat model (`qwen-max`) with streaming output.
3. **Send two user messages** to the agent:
   - Ask for the version of AgentScope (using Python execution),
   - Request: “add 11 and 1 using add_one tool”.
4. **For each tool call**, prompt you in the console to:
   - enter `y` to approve and run the tool as-is,
   - enter `n` to refuse the tool call,
   - enter `e` to edit the tool name and/or its parameters before execution.

This showcases how to keep a human in the loop for **all tool executions**, including tools coming from an external MCP server.


