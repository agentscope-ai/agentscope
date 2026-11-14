# OpenSandbox shared workspace lifecycle

Local cache TTL is separate from remote sandbox idleness. The manager
releases idle local connections without pausing shared sandboxes. Remote
pause requires a lifecycle claim that excludes all active users.

## Configuration

One process can use the default in-memory coordinator. Multiple processes
or service nodes **must** share a Redis coordinator namespace. A namespace
identifies one OpenSandbox deployment; do not mix unrelated deployments or
change namespace while old nodes are running.

```python
from redis.asyncio import Redis
from agentscope.app.workspace_manager import (
    OpenSandboxWorkspaceManager,
    RedisWorkspaceLifecycle,
)

redis = Redis.from_url(
    "redis://localhost:6379/0",
    socket_connect_timeout=5,
    socket_timeout=5,
)
manager = OpenSandboxWorkspaceManager(
    lifecycle=RedisWorkspaceLifecycle(redis, namespace="production"),
    ttl=3600,
    usage_lease_seconds=60,
)

async with manager:
    async with manager.acquire_workspace(
        user_id="user", agent_id="agent", session_id="session",
        workspace_id="persisted-workspace-id",
    ) as workspace:
        # Hold this scope across the entire agent/tool execution.
        ...

# The application owns the Redis client and closes it after all managers.
await redis.aclose()
```

Install the existing `storage-redis` extra to obtain the optional Redis
client. All nodes must use compatible lifecycle configuration and idle
thresholds, and the Redis database must retain coordinator keys (no
key eviction; persistence appropriate to the deployment). Do not run old
nodes that still pause directly alongside upgraded nodes.

## Execution and shutdown

- `acquire_workspace()` holds a renewable distributed usage lease and a
  local reference count. Concurrent users are supported; the execution
  itself is not serialized across sessions.
- The existing `get_workspace()` API implicitly borrows until the calling
  asyncio task ends. Repeated acquisition in that task shares its borrow.
  Prefer explicit acquisition in a long-lived dispatcher or worker loop.
  Passing the handle to a longer-lived task requires that task to acquire
  its own scope before the original scope ends.
- Chat execution uses an explicit scope. Offloaded tools acquire their own
  scope so a background command remains protected after its chat turn ends.
  Existing request/file and realtime paths use task-scoped acquisition.
- Local TTL starts when the last local user releases its handle. The
  global idle period starts when the last distributed user releases; for
  a crashed user, its lease-expiration time is the idle starting point.
- `close()` and `close_all()` only detach idle local handles. They do not
  pause remote sandboxes. Active handles are left intact.
- Manager exit stops admissions and collection, waits for local users to
  drain, then detaches. Shutdown must first finish/cancel application work;
  exit does not cancel arbitrary user tasks itself. The shutdown caller's
  implicit task-scoped borrows are released automatically.
- A lost or failed usage-lease renewal cancels the owning execution rather
  than silently continuing without protection. Cancellation is cooperative;
  application code must not suppress cancellation or block the event loop.
- These leases protect against **manager idle collection**. They do not
  renew the OpenSandbox provider's own sandbox timeout, nor recover a
  provider-side expiration. Configure provider timeouts separately.

The gateway facade's `aclose()` has no remote shutdown action; local release
clears only local handles and closes the SDK client. It does not stop the
shared gateway/MCP processes. Direct calls to `workspace.close()` still
have the existing explicit remote-pause meaning and must not be used by
uncoordinated callers in shared deployments.

## Pause transitions and ambiguous failures

A collector atomically claims an idle workspace in Redis. New acquisitions
wait while that claim exists (bounded by `timeout_seconds`); they cannot
resume a sandbox while an earlier pause is still executing. Successful
pause increments the generation so other nodes discard cached handles and
reattach/resume before exposing the workspace again. The idle index also
lets another node collect workspaces whose original node has crashed.

OpenSandbox's pause RPC does not accept a fencing token. Consequently,
a collector claim has **no expiry**. Automatically timing it out would let
a delayed collector pause a newly admitted user's sandbox. A crashed
collector or ambiguous pause failure therefore leaves the workspace blocked
and emits a log naming its workspace ID.

To reconcile such a claim:

1. Stop the collector process and ensure its outstanding pause request can
   no longer execute; inspect the remote sandbox's settled state.
2. Read the collector token from
   `agentscope:workspace:{production}:<workspace_id>:state`, field `collector`.
3. Call `lifecycle.update(workspace_id, "finish", token, 0)` with that token.
   This clears only the matching claim and advances the generation. The
   next borrower reattaches/resumes. Never clear claims automatically based
   only on elapsed time.

Redis loss/rollback or eviction invalidates distributed coordination. Stop
users and reconcile sandbox state before restarting against a fresh registry.
Only cooperating callers using the same namespace are protected. A deployed
sweeper must remain running for automatic remote idle collection; otherwise
provider timeout policy remains the fallback.

## Verification

```bash
pytest tests/workspace_manager_opensandbox_test.py
AGENTSCOPE_DOCKER_LIFECYCLE_TEST=1 python -m pytest \
  tests/workspace_lifecycle_docker_test.py -v -s
```

The Docker suite uses the actual manager and Redis Lua implementation with
real temporary Alpine and Redis containers, including independent A/B
processes. Only provider provisioning/pause is adapted to Docker. It checks
long-turn renewal, node eviction/shutdown, admission racing pause, and
orphaned-lease collection. It is not an end-to-end OpenSandbox SDK test.
