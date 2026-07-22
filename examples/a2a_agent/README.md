# A2A Agent

`A2AAgent` lets AgentScope applications call a remote
[A2A 1.0](https://a2a-protocol.org/) agent through the official Python SDK.
The instance owns the remote `contextId`, so keep one instance for one logical
conversation.

`A2AAgent` deliberately exposes Agent-like methods without inheriting from
`Agent`. A local `Agent` owns a model, toolkit, state, and reasoning loop;
`A2AAgent` delegates those responsibilities to the remote server and only
manages the client-side A2A conversation and Task lifecycle.

## Prerequisites

- Python 3.11 or newer
- AgentScope with the A2A extra:

  ```bash
  pip install "agentscope[a2a]"
  ```

  When running from an AgentScope source checkout, use `uv sync --extra a2a`
  instead.

- A running A2A 1.0 server whose Agent Card is available from its standard
  well-known endpoint. An official local A2A Python sample can be used; no
  paid model or production service is required by this client example.

## Run the interactive example

Set the base URL of the remote agent and start the client:

```bash
export A2A_AGENT_URL=http://localhost:9999
uv run --extra a2a python examples/a2a_agent/main.py
```

The program resolves the Agent Card, streams replies, handles
`INPUT_REQUIRED`, and keeps the same remote context across ordinary turns.
Enter `quit` to close the client cleanly.

## Minimal usage

Resolve the remote card once, create one `A2AAgent` per conversation, and use
it as an async context manager:

```python
import httpx

from a2a.client import A2ACardResolver

from agentscope.agent import A2AAgent
from agentscope.message import UserMsg


async with httpx.AsyncClient() as httpx_client:
    card = await A2ACardResolver(
        httpx_client=httpx_client,
        base_url="http://localhost:9999",
    ).get_agent_card()

async with A2AAgent(card) as agent:
    reply = await agent.reply(
        UserMsg(name="user", content="Plan a weekend in Hangzhou."),
    )
    print(reply.get_text_content())

    # The second call automatically reuses the remote contextId.
    reply = await agent.reply(
        UserMsg(name="user", content="Make it suitable for children."),
    )
```

Use `reply_stream()` when the application needs AgentScope events. Text arrives
as `TextBlockDeltaEvent`; A2A Task progress arrives as a `CustomEvent` named
`a2a_status_update`. The final `ReplyEndEvent` and returned `Msg` metadata have
an `a2a` object containing the context ID, Task ID, state, and artifact IDs.

Messages passed to `observe()` are prepended to the next request. They are
cleared after a valid A2A response and retained when sending fails:

```python
await agent.observe(previous_messages)
reply = await agent.reply(current_message)
```

## Task lifecycle

- `INPUT_REQUIRED`: catch `A2ATaskStateError` and call `reply()` again with the
  requested user input. The active context ID and Task ID are reused.
- `SUBMITTED` or `WORKING`: call `resume()` / `resume_stream()` to subscribe to
  the active Task. If subscription is unsupported, the adapter fetches the
  current Task snapshot.
- `AUTH_REQUIRED`: complete authorization through the application-specific,
  out-of-band flow and then call `resume()`.
- Use `get_task()` to inspect the active Task and `cancel_task()` to request
  remote cancellation.
- `FAILED`, `REJECTED`, and `CANCELED` are reported as `A2ATaskStateError` with
  `context_id`, `task_id`, `task_state`, and an optional `status_message`.

The adapter does not translate local coroutine cancellation into remote Task
cancellation; call `cancel_task()` explicitly when that is the desired action.
The `INPUT_REQUIRED` handling above supports ordinary continuation of an A2A
Task; durable AgentScope-native HITL persistence and recovery are outside this
client adapter's current scope.

## Content support

Text, raw bytes, and URL Parts are mapped to AgentScope text/data blocks.
Streaming is supported for text and raw-byte artifacts. Structured-data Parts,
thinking/tool/hint blocks, push notifications, and A2A 0.3 interfaces are not
supported by this adapter.

`compress_context()` is intentionally a no-op because the remote A2A server,
not the local adapter, owns and compresses its conversation context.

## Local E2E test

Contributors can run the deterministic end-to-end test directly:

```bash
uv run --extra dev pytest tests/a2a_agent_e2e_test.py -v
```

The test starts an ephemeral A2A 1.0 server on localhost using the official
SDK server primitives. `A2AAgent` resolves its Agent Card and connects through
real JSON-RPC/SSE, then the test checks streamed artifact assembly and remote
`contextId` continuity across two turns. It requires no model, API key, or
external network access and shuts the server down when complete.
