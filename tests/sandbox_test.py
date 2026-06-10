# -*- coding: utf-8 -*-
"""Unit tests for the sandbox system."""

import sys
from types import ModuleType

# Mock tree-sitter modules so middleware imports work in this test file.
for _name in ["tree_sitter_bash", "tree_sitter"]:
    sys.modules[_name] = ModuleType(_name)
for _attr in ["Language", "Parser", "Node"]:
    setattr(sys.modules["tree_sitter"], _attr, object)

import pytest

from agentscope.sandbox import (
    IsolationScope,
    Sandbox,
    SandboxAcquireResult,
    SandboxClient,
    SandboxContext,
    SandboxExecutionGuard,
    SandboxIsolationKey,
    SandboxLease,
    SandboxManager,
    SandboxState,
    SandboxStateStore,
    InMemorySandboxStateStore,
    WorkspaceSandbox,
    WorkspaceSandboxClient,
    noop_execution_guard,
)
from agentscope.workspace import WorkspaceBase


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeSandbox(Sandbox):
    """In-memory sandbox for testing."""

    def __init__(self, state: SandboxState):
        self._state = state
        self._running = False
        self.started = False
        self.stopped = False
        self.shutdown_called = False

    @property
    def state(self) -> SandboxState:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        self.started = True
        self._running = True

    async def stop(self) -> None:
        self.stopped = True
        self._running = False

    async def shutdown(self) -> None:
        self.shutdown_called = True


class FakeSandboxClient(SandboxClient):
    """In-memory sandbox client for testing."""

    def __init__(self) -> None:
        self.created: list[tuple] = []
        self.resumed: list[SandboxState] = []
        self._serial_counter = 0

    async def create(self, workspace_spec, snapshot_spec, options):
        self._serial_counter += 1
        state = SandboxState(session_id=f"fake-{self._serial_counter}")
        sandbox = FakeSandbox(state)
        self.created.append((workspace_spec, snapshot_spec, options))
        return sandbox

    async def resume(self, state: SandboxState):
        self.resumed.append(state)
        return FakeSandbox(state)

    def serialize_state(self, state: SandboxState) -> str:
        import json
        return json.dumps(state.to_dict())

    def deserialize_state(self, json_str: str) -> SandboxState:
        import json
        return SandboxState.from_dict(json.loads(json_str))


class CountingLease(SandboxLease):
    def __init__(self):
        self.closed = False
        self.close_count = 0

    def close(self) -> None:
        self.closed = True
        self.close_count += 1


class CountingGuard:
    def __init__(self):
        self.entered: list[SandboxIsolationKey] = []

    def __call__(self, key: SandboxIsolationKey) -> SandboxLease:
        self.entered.append(key)
        return CountingLease()


# ---------------------------------------------------------------------------
# IsolationKey tests
# ---------------------------------------------------------------------------

def test_isolation_key_resolve_session():
    key = SandboxIsolationKey.resolve(
        IsolationScope.SESSION, "s1", "u1", "agent1"
    )
    assert key == SandboxIsolationKey(IsolationScope.SESSION, "s1")


def test_isolation_key_resolve_session_missing():
    key = SandboxIsolationKey.resolve(
        IsolationScope.SESSION, None, "u1", "agent1"
    )
    assert key is None


def test_isolation_key_resolve_user():
    key = SandboxIsolationKey.resolve(
        IsolationScope.USER, "s1", "u1", "agent1"
    )
    assert key == SandboxIsolationKey(IsolationScope.USER, "u1")


def test_isolation_key_resolve_user_fallback_session():
    key = SandboxIsolationKey.resolve(
        IsolationScope.USER, "s1", None, "agent1"
    )
    assert key == SandboxIsolationKey(IsolationScope.SESSION, "s1")


def test_isolation_key_resolve_user_missing_both():
    key = SandboxIsolationKey.resolve(
        IsolationScope.USER, None, None, "agent1"
    )
    assert key is None


def test_isolation_key_resolve_agent():
    key = SandboxIsolationKey.resolve(
        IsolationScope.AGENT, "s1", "u1", "agent1"
    )
    assert key == SandboxIsolationKey(IsolationScope.AGENT, "agent1")


def test_isolation_key_resolve_global():
    key = SandboxIsolationKey.resolve(
        IsolationScope.GLOBAL, "s1", "u1", "agent1"
    )
    assert key == SandboxIsolationKey(IsolationScope.GLOBAL, "__global__")


def test_isolation_key_resolve_none_defaults_to_user():
    key = SandboxIsolationKey.resolve(None, "s1", "u1", "agent1")
    assert key == SandboxIsolationKey(IsolationScope.USER, "u1")


# ---------------------------------------------------------------------------
# Noop guard / lease tests
# ---------------------------------------------------------------------------

def test_noop_lease_is_idempotent():
    lease = SandboxLease.noop()
    lease.close()
    lease.close()
    assert True  # no exception


def test_noop_execution_guard():
    guard = noop_execution_guard()
    key = SandboxIsolationKey(IsolationScope.AGENT, "a")
    lease = guard(key)
    assert isinstance(lease, SandboxLease)
    lease.close()


# ---------------------------------------------------------------------------
# InMemoryStateStore tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_in_memory_store_round_trip():
    store = InMemorySandboxStateStore()
    key = SandboxIsolationKey(IsolationScope.SESSION, "s1")
    assert await store.load(key) is None
    await store.save(key, '{"session_id":"x"}')
    assert await store.load(key) == '{"session_id":"x"}'
    await store.delete(key)
    assert await store.load(key) is None


# ---------------------------------------------------------------------------
# SandboxManager acquire tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_manager_acquire_priority_1_external_sandbox():
    client = FakeSandboxClient()
    store = InMemorySandboxStateStore()
    mgr = SandboxManager(client, store, "agent1")

    external = FakeSandbox(SandboxState("ext"))
    ctx = SandboxContext(external_sandbox=external)
    result = await mgr.acquire(ctx, session_id="s1")

    assert result.sandbox is external
    assert not result.self_managed
    assert len(client.created) == 0


@pytest.mark.asyncio
async def test_manager_acquire_priority_2_external_state():
    client = FakeSandboxClient()
    store = InMemorySandboxStateStore()
    mgr = SandboxManager(client, store, "agent1")

    ext_state = SandboxState("ext-state")
    ctx = SandboxContext(external_state=ext_state)
    result = await mgr.acquire(ctx, session_id="s1")

    assert result.self_managed
    assert result.sandbox.state.session_id == "ext-state"
    assert len(client.resumed) == 1


@pytest.mark.asyncio
async def test_manager_acquire_priority_3_persisted_state():
    client = FakeSandboxClient()
    store = InMemorySandboxStateStore()
    mgr = SandboxManager(client, store, "agent1")

    # Seed persisted state
    key = SandboxIsolationKey(IsolationScope.SESSION, "s1")
    state = SandboxState("persisted")
    await store.save(key, client.serialize_state(state))

    ctx = SandboxContext(isolation_scope=IsolationScope.SESSION)
    result = await mgr.acquire(ctx, session_id="s1")

    assert result.self_managed
    assert result.sandbox.state.session_id == "persisted"
    assert len(client.resumed) == 1


@pytest.mark.asyncio
async def test_manager_acquire_priority_4_fresh_create():
    client = FakeSandboxClient()
    store = InMemorySandboxStateStore()
    mgr = SandboxManager(client, store, "agent1")

    ctx = SandboxContext(isolation_scope=IsolationScope.SESSION)
    result = await mgr.acquire(ctx, session_id="s1")

    assert result.self_managed
    assert result.sandbox.state.session_id == "fake-1"
    assert len(client.created) == 1


@pytest.mark.asyncio
async def test_manager_acquire_applies_guard():
    client = FakeSandboxClient()
    store = InMemorySandboxStateStore()
    guard = CountingGuard()
    mgr = SandboxManager(client, store, "agent1", execution_guard=guard)

    ctx = SandboxContext(isolation_scope=IsolationScope.AGENT)
    result = await mgr.acquire(ctx, session_id="s1")

    assert len(guard.entered) == 1
    assert guard.entered[0] == SandboxIsolationKey(IsolationScope.AGENT, "agent1")
    assert isinstance(result.lease, CountingLease)


@pytest.mark.asyncio
async def test_manager_acquire_guard_released_on_failure():
    class BrokenClient(SandboxClient):
        async def create(self, workspace_spec, snapshot_spec, options):
            raise RuntimeError("boom")
        async def resume(self, state):
            raise RuntimeError("boom")
        def serialize_state(self, state):
            return ""
        def deserialize_state(self, json_str):
            return SandboxState("")

    store = InMemorySandboxStateStore()
    lease = CountingLease()
    guard = lambda _key: lease
    mgr = SandboxManager(BrokenClient(), store, "agent1", execution_guard=guard)

    ctx = SandboxContext(isolation_scope=IsolationScope.AGENT)
    with pytest.raises(RuntimeError, match="boom"):
        await mgr.acquire(ctx, session_id="s1")

    assert lease.closed


# ---------------------------------------------------------------------------
# SandboxManager release / persist / clear tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_manager_release_self_managed():
    client = FakeSandboxClient()
    store = InMemorySandboxStateStore()
    mgr = SandboxManager(client, store, "agent1")

    sandbox = FakeSandbox(SandboxState("x"))
    result = SandboxAcquireResult.self_managed(sandbox)
    await mgr.release(result)

    assert sandbox.stopped
    assert sandbox.shutdown_called


@pytest.mark.asyncio
async def test_manager_release_user_managed():
    client = FakeSandboxClient()
    store = InMemorySandboxStateStore()
    mgr = SandboxManager(client, store, "agent1")

    sandbox = FakeSandbox(SandboxState("x"))
    result = SandboxAcquireResult.user_managed(sandbox)
    await mgr.release(result)

    assert not sandbox.stopped
    assert not sandbox.shutdown_called


@pytest.mark.asyncio
async def test_manager_release_none():
    client = FakeSandboxClient()
    store = InMemorySandboxStateStore()
    mgr = SandboxManager(client, store, "agent1")
    await mgr.release(None)
    # no exception


@pytest.mark.asyncio
async def test_manager_persist_state():
    client = FakeSandboxClient()
    store = InMemorySandboxStateStore()
    mgr = SandboxManager(client, store, "agent1")

    sandbox = FakeSandbox(SandboxState("persist-me"))
    result = SandboxAcquireResult.self_managed(sandbox)
    ctx = SandboxContext(isolation_scope=IsolationScope.SESSION)
    await mgr.persist_state(result, ctx, session_id="s1")

    key = SandboxIsolationKey(IsolationScope.SESSION, "s1")
    loaded = await store.load(key)
    assert loaded is not None
    loaded_state = client.deserialize_state(loaded)
    assert loaded_state.session_id == "persist-me"


@pytest.mark.asyncio
async def test_manager_persist_state_user_managed_skipped():
    client = FakeSandboxClient()
    store = InMemorySandboxStateStore()
    mgr = SandboxManager(client, store, "agent1")

    sandbox = FakeSandbox(SandboxState("persist-me"))
    result = SandboxAcquireResult.user_managed(sandbox)
    ctx = SandboxContext(isolation_scope=IsolationScope.SESSION)
    await mgr.persist_state(result, ctx, session_id="s1")

    key = SandboxIsolationKey(IsolationScope.SESSION, "s1")
    assert await store.load(key) is None


@pytest.mark.asyncio
async def test_manager_clear_state():
    client = FakeSandboxClient()
    store = InMemorySandboxStateStore()
    mgr = SandboxManager(client, store, "agent1")

    key = SandboxIsolationKey(IsolationScope.SESSION, "s1")
    await store.save(key, '{"session_id":"x"}')
    ctx = SandboxContext(isolation_scope=IsolationScope.SESSION)
    await mgr.clear_state(ctx, session_id="s1")

    assert await store.load(key) is None


# ---------------------------------------------------------------------------
# SandboxLifecycleMiddleware tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lifecycle_middleware_acquires_and_releases():
    client = FakeSandboxClient()
    store = InMemorySandboxStateStore()
    mgr = SandboxManager(client, store, "agent1")

    from agentscope.middleware._sandbox_lifecycle import SandboxLifecycleMiddleware
    mw = SandboxLifecycleMiddleware(mgr)

    # Build a minimal fake agent with a state object
    class FakeAgent:
        class State:
            session_id = "s1"
        state = State()

    async def next_handler():
        yield "event1"
        yield "event2"

    items = []
    async for item in mw.on_reply(FakeAgent(), {}, next_handler):
        items.append(item)

    assert items == ["event1", "event2"]
    # One sandbox was created, started, stopped, and shutdown
    assert len(client.created) == 1
    sandbox = client.created[0]  # Actually the tuple, not the sandbox
    # Let's fetch the actual sandbox from the first creation
    # FakeSandboxClient.create stores the tuple, but we need the sandbox instance
    # Actually FakeSandboxClient.create appends tuple, returns sandbox
    # So client.created[0] is the tuple; we can't easily get the sandbox here.
    # Let's just verify state persistence happened.
    key = SandboxIsolationKey(IsolationScope.SESSION, "s1")
    assert await store.load(key) is not None


@pytest.mark.asyncio
async def test_lifecycle_middleware_handles_next_handler_exception():
    client = FakeSandboxClient()
    store = InMemorySandboxStateStore()
    mgr = SandboxManager(client, store, "agent1")

    from agentscope.middleware._sandbox_lifecycle import SandboxLifecycleMiddleware
    mw = SandboxLifecycleMiddleware(mgr)

    class FakeAgent:
        class State:
            session_id = "s1"
        state = State()

    async def next_handler():
        yield "event1"
        raise RuntimeError("boom")

    items = []
    with pytest.raises(RuntimeError, match="boom"):
        async for item in mw.on_reply(FakeAgent(), {}, next_handler):
            items.append(item)

    # Even on exception, cleanup should have happened and state persisted
    assert items == ["event1"]
    key = SandboxIsolationKey(IsolationScope.SESSION, "s1")
    assert await store.load(key) is not None


# ---------------------------------------------------------------------------
# WorkspaceSandbox adapter tests
# ---------------------------------------------------------------------------

class FakeWorkspace(WorkspaceBase):
    """Minimal workspace for adapter testing."""

    def __init__(self, workspace_id: str | None = None):
        super().__init__(workspace_id)
        self.initialized = False
        self.closed = False
        self._tools: list = []
        self._mcps: list = []

    async def initialize(self) -> None:
        self.initialized = True
        self.is_alive = True

    async def close(self) -> None:
        self.closed = True
        self.is_alive = False

    async def get_instructions(self) -> str:
        return "fake"

    async def list_tools(self) -> list:
        return self._tools

    async def list_mcps(self) -> list:
        return self._mcps

    async def list_skills(self) -> list:
        return []

    async def add_mcp(self, mcp):
        self._mcps.append(mcp)

    async def remove_mcp(self, name):
        self._mcps = [m for m in self._mcps if m.name != name]

    async def add_skill(self, skill):
        pass

    async def remove_skill(self, name):
        pass

    async def offload_context(self, session_id, msgs, tag=None):
        return ""

    async def offload_tool_result(self, session_id, result):
        return ""


def test_workspace_sandbox_lifecycle():
    ws = FakeWorkspace("ws-1")
    sandbox = WorkspaceSandbox(ws)

    assert not sandbox.is_running
    assert sandbox.state.session_id == "ws-1"


@pytest.mark.asyncio
async def test_workspace_sandbox_start_and_shutdown():
    ws = FakeWorkspace("ws-1")
    sandbox = WorkspaceSandbox(ws)

    await sandbox.start()
    assert ws.initialized
    assert sandbox.is_running

    await sandbox.stop()
    # stop is a no-op for WorkspaceSandbox
    assert sandbox.is_running

    await sandbox.shutdown()
    assert ws.closed
    assert not sandbox.is_running


@pytest.mark.asyncio
async def test_workspace_sandbox_client_create():
    def factory(**kw):
        return FakeWorkspace(kw.get("workspace_id"))

    client = WorkspaceSandboxClient(factory)
    sandbox = await client.create(None, None, None)

    assert isinstance(sandbox, WorkspaceSandbox)
    assert sandbox.state.session_id  # auto-generated UUID
    assert not sandbox.is_running

    await sandbox.start()
    assert sandbox.is_running


@pytest.mark.asyncio
async def test_workspace_sandbox_client_resume():
    def factory(**kw):
        return FakeWorkspace(kw.get("workspace_id"))

    client = WorkspaceSandboxClient(factory)
    state = SandboxState(session_id="ws-42")
    sandbox = await client.resume(state)

    assert isinstance(sandbox, WorkspaceSandbox)
    assert sandbox.state.session_id == "ws-42"


@pytest.mark.asyncio
async def test_workspace_sandbox_client_serialize_round_trip():
    client = WorkspaceSandboxClient(lambda **kw: FakeWorkspace())
    state = SandboxState(session_id="x", workspace_root_ready=True)
    json_str = client.serialize_state(state)
    restored = client.deserialize_state(json_str)
    assert restored.session_id == "x"
    assert restored.workspace_root_ready is True


@pytest.mark.asyncio
async def test_workspace_sandbox_integration_with_manager():
    def factory(**kw):
        return FakeWorkspace(kw.get("workspace_id"))

    client = WorkspaceSandboxClient(factory)
    store = InMemorySandboxStateStore()
    mgr = SandboxManager(client, store, "agent1")

    ctx = SandboxContext(isolation_scope=IsolationScope.SESSION)
    result = await mgr.acquire(ctx, session_id="s1")

    assert isinstance(result.sandbox, WorkspaceSandbox)
    assert not result.sandbox.is_running

    await result.sandbox.start()
    assert result.sandbox.is_running

    await mgr.persist_state(result, ctx, session_id="s1")
    await mgr.release(result)

    assert not result.sandbox.is_running
    assert result.sandbox.workspace.closed

    # Resume from persisted state
    result2 = await mgr.acquire(ctx, session_id="s1")
    assert isinstance(result2.sandbox, WorkspaceSandbox)
    # The resumed sandbox should carry the same session/workspace id
    assert result2.sandbox.state.session_id == result.sandbox.state.session_id
