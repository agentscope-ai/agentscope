# Permission Middleware Agent Service Example

English | [中文](README_zh.md)

A runnable AgentScope service demonstrating the `on_check_permission`
middleware hook through an audit-logging use case. It reuses the existing
`examples/web_ui` frontend unchanged.

## What it demonstrates

- `on_check_permission` wraps permission checking after tool lookup and input
  validation, and before Agent consumes the returned decision.
- As a standard onion hook, middleware can delegate to the remaining permission
  chain, replace its returned decision, or short-circuit it with an application
  decision.
- The audit middleware calls `next_handler(**input_kwargs)` once, records the
  returned ASK/DENY/ALLOW decision, and returns that same object unchanged.
- A user-tool policy middleware denies `PermissionDemoTool` for one
  configured user without invoking the built-in permission engine.
- Audit records carry application identity and correlation fields but exclude
  raw tool input.
- A side-effect-free demo tool deterministically exercises all three final
  decision behaviors.

The hook supports application-owned policies based on context such as user,
role, tenant, environment, budget, or an external policy service. Middleware
that changes permission outcomes becomes part of the application's trusted
authorization boundary. This example uses the same hook only to audit the
decision returned by the downstream permission chain.

## Screenshots

### ASK confirmation

With `decision=ask`, Agent pauses before tool execution and displays the
existing confirmation UI:

![ASK confirmation for PermissionDemoTool](assets/permission-ask.png)

### Permission audit record

The outer audit middleware records the decision returned by the complete
permission chain. This console entry shows the application-owned DENY for
`restricted-user`:

![Permission decision audit record](assets/permission-audit-log.png)

### Application-owned per-user DENY

Connect the Web UI as `restricted-user` to exercise the application policy:

![Web UI connected as restricted-user](assets/restricted-user-setup.png)

The application middleware then short-circuits a requested `decision=allow`
to DENY before the built-in permission engine and tool execution:

![Per-user tool denial](assets/permission-user-deny.png)

## Run the service

Install AgentScope with service extras and start Redis:

```bash
pip install agentscope[full]
redis-server                 # or: brew services start redis
```

Run this service:

```bash
cd examples/permission_middleware_service
python main.py
```

The restricted demo user defaults to `restricted-user`. Override it when
needed:

```bash
PERMISSION_MIDDLEWARE_RESTRICTED_USER_ID=another-user python main.py
```

Then start the existing Web UI:

```bash
cd examples/web_ui
pnpm install
pnpm dev
```

Point the UI at `http://localhost:8000`. The username entered on the connection
page is sent to the service as `X-User-ID`. Use `regular-user` for the normal
permission scenarios, then ask the agent to invoke
`PermissionDemoTool` with `decision=allow`, `decision=ask`, or
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
  "tool_name": "PermissionDemoTool",
  "mode": "default",
  "decision": {
    "behavior": "ask",
    "reason": "Mode: default",
    "bypass_immune": false
  }
}
```

This example registers the audit middleware first, making it the outermost
permission layer around the user-tool policy and built-in engine. The record is
therefore the decision that Agent will consume. Applications should preserve
that ordering when an audit middleware must record the decision returned by the
complete chain.

When an approved call resumes, it skips built-in engine re-evaluation but still
traverses `on_check_permission`. Application policy checks and decision auditing
therefore remain in effect immediately before tool execution.

## Scenarios

1. **ASK** — use `decision=ask` in DEFAULT mode. The console record contains
   the final ASK before Agent emits a confirmation request. If the user
   approves it, the resumed call produces a second record for the confirmed
   ALLOW before execution.
2. **DENY** — use `decision=deny`. The record contains DENY before Agent writes
   the denied tool result; the demo tool body is not executed.
3. **ALLOW** — use `decision=allow`. The record contains ALLOW before Agent
   executes the side-effect-free demo tool.
4. **Per-user DENY** — connect the UI as `restricted-user` and request
   `decision=allow`. `UserToolPolicyMiddleware` short-circuits the built-in
   engine with DENY, the outer audit middleware records that DENY, and the demo
   tool body is not executed.

For an unconfirmed request, `next_handler(**input_kwargs)` continues through
the remaining permission middleware and then the built-in permission engine.
The built-in result reflects the active permission mode, configured rules, and
tool-specific permission checks. A downstream middleware may also replace that
result or short-circuit before the engine; the audit middleware records whatever
decision the downstream chain returns.

## Privacy and failure behavior

Records deliberately exclude `tool_input` and `tool_call.input`. The `reason`
field carries `PermissionDecision.decision_reason` and may contain matched rule
content, so production sinks should redact or omit it when rules include
sensitive command or path patterns.

The Web UI uses the caller-supplied `X-User-ID` header as a temporary identity
mechanism. It makes the per-user policy easy to exercise but is not production
authentication. Real applications should build identity-aware permission
middleware from authenticated, server-trusted user or tenant context.

The example does not catch sink exceptions. A required audit sink failure
therefore propagates before Agent consumes the decision. Applications wanting
best-effort logging can catch transport errors inside their sink.

## Relationship to `examples/agent_service`

This service uses the same FastAPI/Redis/Web UI shape as
`examples/agent_service`, while omitting RAG and optional MCP integrations so
the permission hook remains the focus.
