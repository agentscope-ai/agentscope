# FastAPI human-in-the-loop

This example keeps one AgentScope `Agent` per `session_id` and shows two
human-in-the-loop paths:

- A reply parked on `AskUser` or a permission prompt can be resumed through
  `/confirm/{session_id}`.
- An actively generating reply can be cancelled through
  `/interrupt/{session_id}`. A parked reply can also be interrupted, after
  which `/chat` can add new information to the same agent and conversation.

The API returns AgentScope events as JSON so a frontend can render streaming
blocks and detect `REQUIRE_EXTERNAL_EXECUTION` or `REQUIRE_USER_CONFIRM`.

## Run it

Install AgentScope with its service dependencies, set a DashScope API key,
and start exactly one worker:

```bash
pip install -e ".[service]"
export DASHSCOPE_API_KEY=sk-...
uvicorn examples.fastapi_hitl.main:app --workers 1
```

Start a conversation:

```bash
curl -s http://127.0.0.1:8000/chat \
  -H 'content-type: application/json' \
  -d '{"session_id":"demo","message":"Plan a migration for my API"}'
```

When the response contains a `REQUIRE_EXTERNAL_EXECUTION` event for
`AskUser`, copy its tool-call ID and answer the questions verbatim:

```bash
curl -s http://127.0.0.1:8000/confirm/demo \
  -H 'content-type: application/json' \
  -d '{
    "tool_call_id":"TOOL_CALL_ID",
    "answers":[{
      "question":"Proceed with this plan?",
      "selected":["Approve"],
      "other":null
    }]
  }'
```

For a pending permission prompt, send the same tool-call ID with
`"confirmed": true` instead. To interrupt either active generation or a
parked prompt:

```bash
curl -s -X POST http://127.0.0.1:8000/interrupt/demo
```

Then send another `/chat` request with `session_id: "demo"`; the same agent
retains the earlier conversation.

## Scope and deployment limitation

This is deliberately an in-memory, single-process example. Per-session locks
prevent requests in one process from overwriting each other's active task,
but neither the agent objects nor cancellation signals are shared between
processes. Do not run it with multiple Uvicorn workers.

For production or distributed deployments, persist agent state in shared
storage and route interruption through a distributed signalling mechanism
instead of storing `asyncio.Task` objects in a process-local dictionary.

