# ACP Agent

Expose an AgentScope agent over stdio using Agent Client Protocol (ACP).

## Install

From the repository root, using Python 3.11+:

```sh
python -m pip install -e ".[acp]"
```

Set `DASHSCOPE_API_KEY` in your environment. Optionally set `AGENTSCOPE_MODEL` to override the default DashScope model.

## Run the example

```sh
export DASHSCOPE_API_KEY="your-api-key"
python examples/acp/acp_agent/client.py "Explain the project structure" --cwd /absolute/project
```

On Windows PowerShell, use `$env:DASHSCOPE_API_KEY = "your-api-key"` to set the key, then run the same Python command.

The client starts `agent.py`, creates a session, sends one prompt, prints the streamed response, and closes the session and process. This calls the real DashScope model. Without arguments, the prompt is `hello` and the working directory is your current directory.

When a tool needs permission, enter `y` to allow once or press Enter to reject. EOF cancels the permission request.

## Connect another ACP client

Configure your client to launch the agent with these values:

| Field | Value |
| --- | --- |
| Command | Absolute path to the Python interpreter containing `agentscope[acp]` |
| Arguments | Absolute path to `examples/acp/acp_agent/agent.py` |
| Environment | `DASHSCOPE_API_KEY` and optionally `AGENTSCOPE_MODEL` |

Pass the executable and arguments separately, including paths with spaces. The agent reads ACP JSON-RPC from stdin; it does not accept plain text typed into the terminal. Stdout carries protocol messages, and the client should drain stderr for logs.

Each session gets a fresh Agent using the client's working directory. The adapter supports streaming, tool permissions, cancellation, and client-provided stdio/HTTP MCP servers. Sessions are held in memory; history loading and client-side file/terminal RPC are not supported.

## Customize

Edit `create_agent` in `agent.py` to change the model, prompt, or tools. Yield a fresh Agent for each session. Factory cleanup must propagate `asyncio.CancelledError` so the shutdown timeout can take effect.
