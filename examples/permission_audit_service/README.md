# Permission Audit Agent Service Example

English | [中文](README_zh.md)

A runnable AgentScope service showing how application middleware can observe
the final permission decision for a tool call. It reuses the existing
`examples/web_ui` frontend unchanged.

## What it demonstrates

- `on_check_permission` runs after tool lookup and input validation, and before
  Agent consumes the returned permission decision.
- The audit middleware calls `next_handler(**input_kwargs)` once, records the
  final ASK/DENY/ALLOW decision, and returns that same object unchanged.
- Audit records carry application identity and correlation fields but exclude
  raw tool input.
- A side-effect-free demo tool deterministically exercises all three final
  decision behaviors.

The hook is an onion-style interception point, not an observer-only API.
Application middleware can also replace the returned decision or short-circuit
the built-in engine. That supports application-owned policies based on context
such as user, role, tenant, environment, budget, or an external policy service.
Such middleware becomes part of the trusted authorization boundary; this
example intentionally limits itself to unchanged audit observation.

## Run the service

Install AgentScope with service extras and start Redis:

```bash
pip install agentscope[full]
redis-server                 # or: brew services start redis
```

Run this service:

```bash
cd examples/permission_audit_service
python main.py
```

Then start the existing Web UI:

```bash
cd examples/web_ui
pnpm install
pnpm dev
```

Point the UI at `http://localhost:8000`. Ask the agent to invoke
`PermissionAuditDemoTool` with `decision=allow`, `decision=ask`, or
`decision=deny`. Permission interactions appear in the UI as usual and one
JSON audit record is written to the service console for each checked call.

## Audit record

```json
{
  "event": "permission_decision",
  "observed_at": "2026-07-17T12:34:56.123456+00:00",
  "user_id": "user-1",
  "agent_id": "agent-1",
  "session_id": "session-1",
  "reply_id": "reply-1",
  "tool_call_id": "call-1",
  "tool_name": "PermissionAuditDemoTool",
  "mode": "default",
  "decision": {
    "behavior": "ask",
    "reason": "Mode: default",
    "bypass_immune": false
  }
}
```

The record represents the decision returned by the complete middleware chain.
It does not expose the permission engine's internal rule-evaluation trace or a
suppressed intermediate candidate. Raw user confirmation input is a separate
lifecycle stage and can be observed through `on_reply`. When an approved call
resumes, it skips built-in engine re-evaluation but still traverses
`on_check_permission`, so application policy and final-decision auditing remain
in effect.

## Scenarios

1. **ASK** — use `decision=ask` in DEFAULT mode. The console record contains
   the final ASK before Agent emits a confirmation request. If the user
   approves it, the resumed call produces a second record for the confirmed
   ALLOW before execution.
2. **DENY** — use `decision=deny`. The record contains DENY before Agent writes
   the denied tool result; the demo tool body is not executed.
3. **ALLOW** — use `decision=allow`. The record contains ALLOW before Agent
   executes the side-effect-free demo tool.

For an unconfirmed request, the audit middleware delegates to the built-in
engine through `next_handler(**input_kwargs)`. Depending on mode and configured
rules, that result may differ from a tool's initial suggestion. Other
middleware may further replace or short-circuit the result; this example
records the decision returned by the complete chain.

## Privacy and failure behavior

Records deliberately exclude `tool_input` and `tool_call.input`. The `reason`
field carries `PermissionDecision.decision_reason` and may contain matched rule
content, so production sinks should redact or omit it when rules include
sensitive command or path patterns.

The example does not catch sink exceptions. A required audit sink failure
therefore propagates before Agent consumes the decision. Applications wanting
best-effort logging can catch transport errors inside their sink.

## Relationship to `examples/agent_service`

This service uses the same FastAPI/Redis/Web UI shape as
`examples/agent_service`, while omitting RAG and optional MCP integrations so
the permission hook remains the focus.
