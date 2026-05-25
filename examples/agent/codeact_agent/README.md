# CodeAct Agent Example

This example showcases a **CodeAct** agent in AgentScope. The CodeAct agent leverages a ReAct agent that, for certain tasks, writes and executes Python code, which involves one or multiple tool calls, to solve problems rather than calling tools directly. A `CodeActToolCallServer` runs locally to handle remote tool calls from within the executed code, enabling the agent to access tools through code rather than through explicit tool-call APIs.

In this example, a user question asking about the current temperature will trigger the agent's CodeAct behavior. The agent writes a Python function to get the temperature in celsius. It first calls `get_fahrenheit_temperature` tool to get the fahrenheit temperature reading. The code then uses it as the input to call `convert_fahrenheit_to_celsius` tool. Then the code returns  the celsius temperature value to user. When the user explicitly asks for temperature in fahrenheit, the CodeAct code should only make one `get_fahrenheit_temperature` remote call.

Note for demo purpose, in this example the system prompt mentions that the agent use the two specific temperature related tool. In realy world application, the model may face a much simpler system prompt just asking it to wirte code to solve as many things as possible. The model needs to figure out the nature of the tools by its own. Or the model may have detailed instructions on how to use coding to solve another specific question. This example only shows the basic workflow of a CodeAct approach, to prove the concept.

## How It Works

1. A `Toolkit` is created and registered with tool functions (e.g., `execute_python_code`, `get_fahrenheit_temperature`, `convert_fahrenheit_to_celsius`).
2. A `CodeActToolCallServer` is started on a local port, serving those tool functions over HTTP.
3. The agent's system prompt includes instructions to write Python code that calls tools via a `remote_tool_call` function (an HTTP client hitting the server's `/call_tool` endpoint).
4. The agent uses `execute_python_code` to run its generated code, which in turn calls the server for tool results.
5. The agent may generate Python functions like the this:
```python
async def remote_tool_call(tool_name: str, tool_args: dict | None = None) -> dict:
    import httpx
    url = f"http://localhost:1024/call_tool"
    payload = {"tool_name": tool_name, "tool_args": tool_args or {}}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json=payload)
            if resp and resp.raise_for_status():
                return resp.json() or {}
            raise ValueError("no response from code act server")
    except Exception as e:
        raise ValueError("cannot get tool result") from e

async def solve_problem():
    fahrenheit_temp_result = await remote_tool_call('get_fahrenheit_temperature')
    fahrenheit_temp = fahrenheit_temp_result.get('fahrenheit', 0)
    celsius_temp_result = await remote_tool_call('convert_fahrenheit_to_celsius', {'fahrenheit': fahrenheit_temp})
    celsius_temp = celsius_temp_result.get('celsius', 0)
    return f'The current temperature is {celsius_temp} °C.'

import asyncio
print(asyncio.run(solve_problem()))
```

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
