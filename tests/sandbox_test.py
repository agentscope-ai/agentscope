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
    StorageBackedSandboxStateStore,
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
    # Sandbox was created and started
    assert len(client.created) == 1
    # Python-native style: container is NOT stopped/shutdown here
    # (that is the workspace manager's TTL sweeper job).
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


# ---------------------------------------------------------------------------
# MCP tool injection tests (direction B)
# ---------------------------------------------------------------------------

class FakeMCPClient:
    """Minimal MCP client stand-in for testing injection."""

    def __init__(self, name: str):
        self.name = name
        self.closed = False

    async def close(self):
        self.closed = True

    async def list_tools(self):
        return []


class FakeWorkspaceWithMcps(WorkspaceBase):
    """Workspace that returns MCP clients for injection testing."""

    def __init__(self, workspace_id: str | None = None, mcps: list | None = None):
        super().__init__(workspace_id)
        self._mcps = mcps or []
        self.initialized = False
        self.closed = False

    async def initialize(self) -> None:
        self.initialized = True
        self.is_alive = True

    async def close(self) -> None:
        self.closed = True
        self.is_alive = False

    async def get_instructions(self) -> str:
        return "fake"

    async def list_tools(self) -> list:
        return []

    async def list_mcps(self) -> list:
        return list(self._mcps)

    async def list_skills(self) -> list:
        return []

    async def add_mcp(self, mcp):
        self._mcps.append(mcp)

    async def remove_mcp(self, name):
        pass

    async def add_skill(self, skill):
        pass

    async def remove_skill(self, name):
        pass

    async def offload_context(self, session_id, msgs, tag=None):
        return ""

    async def offload_tool_result(self, session_id, result):
        return ""


class FakeToolkit:
    """Minimal toolkit stand-in."""

    def __init__(self):
        self.tool_groups = []

    def clear(self):
        self.tool_groups.clear()


class FakeAgentWithToolkit:
    """Agent with toolkit and state for middleware testing."""

    class State:
        session_id = "s1"
        tool_context = type("TC", (), {"activated_groups": []})()

    state = State()
    toolkit = FakeToolkit()


@pytest.mark.asyncio
async def test_mcp_injection_injects_tool_group():
    mcp = FakeMCPClient("fs")

    def factory(**kw):
        return FakeWorkspaceWithMcps(kw.get("workspace_id"), mcps=[mcp])

    client = WorkspaceSandboxClient(factory)
    store = InMemorySandboxStateStore()
    mgr = SandboxManager(client, store, "agent1")

    from agentscope.middleware._sandbox_lifecycle import SandboxLifecycleMiddleware
    mw = SandboxLifecycleMiddleware(mgr, inject_mcp_tools=True)

    agent = FakeAgentWithToolkit()

    async def next_handler():
        yield "event1"

    items = []
    async for item in mw.on_reply(agent, {}, next_handler):
        items.append(item)

    assert items == ["event1"]
    # After cleanup the group should be removed
    assert len(agent.toolkit.tool_groups) == 0
    assert "sandbox" not in agent.state.tool_context.activated_groups
    assert mcp.closed


@pytest.mark.asyncio
async def test_mcp_injection_skips_when_no_mcps():
    def factory(**kw):
        return FakeWorkspaceWithMcps(kw.get("workspace_id"), mcps=[])

    client = WorkspaceSandboxClient(factory)
    store = InMemorySandboxStateStore()
    mgr = SandboxManager(client, store, "agent1")

    from agentscope.middleware._sandbox_lifecycle import SandboxLifecycleMiddleware
    mw = SandboxLifecycleMiddleware(mgr, inject_mcp_tools=True)

    agent = FakeAgentWithToolkit()

    async def next_handler():
        yield "event1"

    items = []
    async for item in mw.on_reply(agent, {}, next_handler):
        items.append(item)

    assert len(agent.toolkit.tool_groups) == 0


@pytest.mark.asyncio
async def test_mcp_injection_disabled():
    mcp = FakeMCPClient("fs")

    def factory(**kw):
        return FakeWorkspaceWithMcps(kw.get("workspace_id"), mcps=[mcp])

    client = WorkspaceSandboxClient(factory)
    store = InMemorySandboxStateStore()
    mgr = SandboxManager(client, store, "agent1")

    from agentscope.middleware._sandbox_lifecycle import SandboxLifecycleMiddleware
    mw = SandboxLifecycleMiddleware(mgr, inject_mcp_tools=False)

    agent = FakeAgentWithToolkit()

    async def next_handler():
        yield "event1"

    items = []
    async for item in mw.on_reply(agent, {}, next_handler):
        items.append(item)

    assert len(agent.toolkit.tool_groups) == 0
    assert not mcp.closed


@pytest.mark.asyncio
async def test_mcp_injection_cleans_up_on_start_failure():
    """If sandbox.start() fails after tool injection, tools must be ejected."""
    mcp = FakeMCPClient("x")

    class BrokenWorkspace(WorkspaceBase):
        async def initialize(self): raise RuntimeError("boom")
        async def close(self): pass
        async def get_instructions(self): return ""
        async def list_tools(self): return []
        async def list_mcps(self): return [mcp]
        async def list_skills(self): return []
        async def add_mcp(self, m): pass
        async def remove_mcp(self, n): pass
        async def add_skill(self, s): pass
        async def remove_skill(self, n): pass
        async def offload_context(self, s, m, t=None): return ""
        async def offload_tool_result(self, s, r): return ""

    client = WorkspaceSandboxClient(lambda **kw: BrokenWorkspace("ws-broken"))
    store = InMemorySandboxStateStore()
    mgr = SandboxManager(client, store, "agent1")

    from agentscope.middleware._sandbox_lifecycle import SandboxLifecycleMiddleware
    mw = SandboxLifecycleMiddleware(mgr, inject_mcp_tools=True)

    agent = FakeAgentWithToolkit()

    async def next_handler():
        yield "event1"

    with pytest.raises(RuntimeError, match="boom"):
        async for _ in mw.on_reply(agent, {}, next_handler):
            pass

    # Group should have been removed even though start() failed
    assert len(agent.toolkit.tool_groups) == 0


# ---------------------------------------------------------------------------
# StorageBackedSandboxStateStore
# ---------------------------------------------------------------------------

class FakeStorage:
    """Minimal storage stand-in for StorageBackedSandboxStateStore testing."""

    def __init__(self):
        self._data: dict[str, str] = {}
        self.calls: list[tuple[str, tuple, dict]] = []

    def _key(self, user_id, agent_id, session_id, isolation_key):
        return f"{user_id}:{agent_id}:{session_id}:{isolation_key}"

    async def get_sandbox_state(self, user_id, agent_id, session_id, isolation_key):
        self.calls.append(("get", (user_id, agent_id, session_id, isolation_key), {}))
        return self._data.get(self._key(user_id, agent_id, session_id, isolation_key))

    async def set_sandbox_state(self, user_id, agent_id, session_id, isolation_key, state_json):
        self.calls.append(("set", (user_id, agent_id, session_id, isolation_key, state_json), {}))
        self._data[self._key(user_id, agent_id, session_id, isolation_key)] = state_json

    async def delete_sandbox_state(self, user_id, agent_id, session_id, isolation_key):
        self.calls.append(("delete", (user_id, agent_id, session_id, isolation_key), {}))
        return self._data.pop(self._key(user_id, agent_id, session_id, isolation_key), None) is not None


@pytest.mark.asyncio
async def test_storage_backed_state_store_round_trip():
    storage = FakeStorage()
    store = StorageBackedSandboxStateStore(
        storage=storage,
        user_id="u1",
        agent_id="a1",
        session_id="s1",
    )
    key = SandboxIsolationKey(IsolationScope.SESSION, "s1")

    assert await store.load(key) is None
    await store.save(key, '{"session_id":"x"}')
    assert await store.load(key) == '{"session_id":"x"}'
    await store.delete(key)
    assert await store.load(key) is None
    await store.delete(key)  # idempotent

    assert len(storage.calls) == 6
    assert storage.calls[0][0] == "get"
    assert storage.calls[1][0] == "set"
    assert storage.calls[2][0] == "get"
    assert storage.calls[3][0] == "delete"
    assert storage.calls[4][0] == "get"
    assert storage.calls[5][0] == "delete"


# ---------------------------------------------------------------------------
# SandboxLifecycleMiddleware MCP dedupe
# ---------------------------------------------------------------------------

class FakeToolkitWithMcps:
    """Toolkit that already holds some MCPs."""

    def __init__(self, mcps=None):
        self.tool_groups = []
        self.mcps = mcps or []


class FakeAgentWithExistingMcps:
    """Agent whose toolkit already contains an MCP named 'fs'."""

    class State:
        session_id = "s1"
        tool_context = type("TC", (), {"activated_groups": []})()

    state = State()
    toolkit = FakeToolkitWithMcps(mcps=[FakeMCPClient("fs")])


@pytest.mark.asyncio
async def test_mcp_injection_skips_duplicates():
    """If toolkit already has an MCP with the same name, skip it."""
    mcp_existing = FakeMCPClient("fs")
    mcp_new = FakeMCPClient("db")

    def factory(**kw):
        return FakeWorkspaceWithMcps(
            kw.get("workspace_id"),
            mcps=[mcp_existing, mcp_new],
        )

    client = WorkspaceSandboxClient(factory)
    store = InMemorySandboxStateStore()
    mgr = SandboxManager(client, store, "agent1")

    from agentscope.middleware._sandbox_lifecycle import SandboxLifecycleMiddleware
    mw = SandboxLifecycleMiddleware(mgr, inject_mcp_tools=True)

    agent = FakeAgentWithExistingMcps()

    injected_during_run: list = []

    async def next_handler():
        # _inject_tools runs before next_handler, so group is present here.
        injected_during_run.extend(agent.toolkit.tool_groups)
        yield "event1"

    items = []
    async for item in mw.on_reply(agent, {}, next_handler):
        items.append(item)

    # Only 'db' should have been injected; 'fs' was already present.
    assert len(injected_during_run) == 1
    injected = injected_during_run[0]
    assert len(injected.mcps) == 1
    assert injected.mcps[0].name == "db"

    # After cleanup the group should be removed
    assert len(agent.toolkit.tool_groups) == 0
    assert "sandbox" not in agent.state.tool_context.activated_groups
    assert "sandbox" not in agent.state.tool_context.activated_groups
