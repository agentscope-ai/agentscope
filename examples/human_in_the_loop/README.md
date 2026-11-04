# Human-in-the-Loop Tool Confirmation with FastAPI

This example shows how to stop an agent before a specific tool call,
wait for the user to confirm or cancel it over HTTP, and then resume
the agent.

## How it works

`main.py` creates an agent with two tools:

- `get_weather` is registered with an `ALLOW` permission, so it runs
  right away.
- `send_email` is registered without a permission, so every call asks
  the user first. A `FunctionTool` asks by default.

When the agent calls `send_email`, `Agent.reply_stream` emits a
`RequireUserConfirmEvent` and the reply is parked. To resume it, pass
a `UserConfirmResultEvent` to `reply_stream`, with one `ConfirmResult`
per pending tool call:

- `confirmed=True`: the tool is executed and the agent keeps going.
- `confirmed=False`: the tool is not executed. The agent gets a
  "denied by user" tool result and keeps reasoning without it.

The FastAPI server wraps this in two endpoints:

| Endpoint        | Body                                                    | What it does                                   |
|-----------------|---------------------------------------------------------|------------------------------------------------|
| `POST /chat`    | `{"session_id", "message"}`                             | Sends a user message to the agent              |
| `POST /confirm` | `{"session_id", "decisions": [{"tool_call_id", "confirmed"}]}` | Confirms or cancels the pending tool calls and resumes the agent |

Both endpoints return the same shape:

```json
{
  "status": "awaiting_confirmation",
  "reply": "I'm waiting for your permission or the external execution to finish.",
  "pending_tool_calls": [
    {"id": "call_1", "name": "send_email", "input": "{\"to\": \"bob@example.com\", ...}"}
  ]
}
```

`status` is `completed` once the agent has finished its reply.

## Quickstart

```bash
uv pip install agentscope[full]

export DASHSCOPE_API_KEY=sk-...
python main.py
```

In another terminal:

```bash
# A read-only tool runs without confirmation
curl -s localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"session_id": "demo", "message": "What is the weather in Paris?"}'

# send_email parks the reply and returns the pending tool call
curl -s localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"session_id": "demo", "message": "Email bob@example.com that the meeting is moved to 3pm"}'

# Confirm it (use "confirmed": false to cancel it instead)
curl -s localhost:8000/confirm -H 'Content-Type: application/json' \
  -d '{"session_id": "demo", "decisions": [{"tool_call_id": "<id from above>", "confirmed": true}]}'
```

## Going further

- **Choose tools by rule instead of per tool**: add `PermissionRule`s
  to `agent.state.permission_context` (`allow_rules`, `deny_rules`,
  `ask_rules`) to decide which calls need confirmation.
- **Remember "always allow"**: set `ConfirmResult.rules` to the rules
  the user accepted, so matching calls stop asking.
- **Abort a parked reply**: send a `UserInterruptEvent` to
  `reply_stream` to close all pending tool calls without running them
  and end the reply.
- **Persist sessions**: this example keeps agents in memory. In
  production, save and load `agent.state` (a Pydantic model) between
  requests. The [agent service](../agent_service) example does this
  with Redis and includes a Web UI for the confirmation flow.
