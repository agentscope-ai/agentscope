# -*- coding: utf-8 -*-
"""OpenSandbox local handles and coordinated remote sandbox lifecycle.

Local cache eviction and shutdown only detach clients. Usage leases protect
execution; idle collectors pause only after globally coordinated exclusion.
All nodes sharing sandboxes must share one Redis lifecycle namespace.
"""

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Literal, Self

from ._workspace_lifecycle import (
    LocalWorkspaceLifecycle,
    WorkspaceLifecycle,
)

from ..._logging import logger
from ...mcp import MCPClient
from ...workspace._opensandbox._constants import (
    DEFAULT_GATEWAY_PORT,
    DEFAULT_IMAGE,
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_TIMEOUT,
)
from ...workspace import OpenSandboxWorkspace

from ._base import IsolationPolicy, WorkspaceManagerBase

DEFAULT_SWEEP_INTERVAL = 300.0


class OpenSandboxWorkspaceManager(WorkspaceManagerBase):
    """Manages OpenSandbox workspaces with TTL-based caching.

    Use the manager as an ``async with`` context manager: entering it
    starts idle collection. Exiting drains local borrowers and releases
    local connections, without pausing sandboxes used by other nodes.
    Multi-process deployments must supply a shared Redis coordinator.
    """

    def __init__(
        self,
        *,
        isolation: IsolationPolicy = IsolationPolicy.PER_AGENT,
        image: str = DEFAULT_IMAGE,
        api_key: str = "",
        domain: str = "",
        protocol: Literal["http", "https"] = "http",
        request_timeout_seconds: float | None = DEFAULT_REQUEST_TIMEOUT,
        timeout_seconds: int = DEFAULT_TIMEOUT,
        gateway_port: int = DEFAULT_GATEWAY_PORT,
        env: dict[str, str] | None = None,
        sandbox_metadata: dict[str, str] | None = None,
        resource: dict[str, str] | None = None,
        entrypoint: list[str] | None = None,
        network_policy: Any | None = None,
        extra_pip: list[str] | None = None,
        default_mcps: list[MCPClient] | None = None,
        skill_paths: list[str] | None = None,
        lifecycle: WorkspaceLifecycle | None = None,
        usage_lease_seconds: float = 60.0,
        ttl: float = 3600.0,
        sweep_interval: float = DEFAULT_SWEEP_INTERVAL,
    ) -> None:
        """Initialize the OpenSandbox workspace manager.

        Args:
            isolation (`IsolationPolicy`, defaults to `PER_AGENT`):
                Isolation grain for :meth:`assign_workspace_id`, used
                when :meth:`get_workspace` is called without an explicit
                ``workspace_id``.
            image (`str`, defaults to `DEFAULT_IMAGE`):
                OpenSandbox image passed to every workspace this manager
                produces.
            api_key (`str`, defaults to `""`):
                OpenSandbox API key. ``""`` lets the SDK fall back to
                its environment-based configuration.
            domain (`str`, defaults to `""`):
                Optional OpenSandbox domain, for local or self-hosted
                deployments.
            protocol (`str`, defaults to `"http"`):
                Scheme used when OpenSandbox returns endpoint values as
                ``host:port`` without a protocol.
            request_timeout_seconds (`float | None`, optional):
                HTTP request timeout forwarded through
                ``ConnectionConfig``. Defaults to 600 seconds so the
                SDK HTTP streaming layer is not shorter than bootstrap
                command timeout.
            timeout_seconds (`int`, defaults to `DEFAULT_TIMEOUT`):
                Sandbox keep-alive / resume timeout.
            gateway_port (`int`, defaults to `DEFAULT_GATEWAY_PORT`):
                TCP port the in-sandbox gateway listens on.
            env (`dict[str, str] | None`, optional):
                Environment variables applied when creating a sandbox.
            sandbox_metadata (`dict[str, str] | None`, optional):
                Extra metadata merged with the per-workspace
                ``agentscope.workspace.id`` / ``agentscope.user.id`` /
                ``agentscope.agent.id`` keys.
            resource (`dict[str, str] | None`, optional):
                Resource limits forwarded to OpenSandbox create.
            entrypoint (`list[str] | None`, optional):
                Optional sandbox entrypoint forwarded to OpenSandbox
                create.
            network_policy (`Any | None`, optional):
                Creation-time OpenSandbox network policy. Runtime egress
                patching is intentionally left for a follow-up.
            extra_pip (`list[str] | None`, optional):
                Extra Python packages installed into the gateway venv
                during bootstrap.
            default_mcps (`list[MCPClient] | None`, optional):
                MCP clients seeded into brand-new workspaces. On
                reattach, the sandbox's persisted ``.mcp`` file wins.
            skill_paths (`list[str] | None`, optional):
                Skill directories seeded into brand-new workspaces.
            lifecycle (`WorkspaceLifecycle | None`, optional):
                Shared lifecycle coordinator. Defaults to an in-process
                coordinator; multi-process deployments must supply a
                RedisWorkspaceLifecycle with the same namespace.
            usage_lease_seconds (`float`, defaults to `60.0`):
                Renewable usage lease, independent of idle TTL.
            ttl (`float`, defaults to `3600.0`):
                Seconds before an idle cached workspace is evicted and
                its sandbox paused.
            sweep_interval (`float`, defaults to `DEFAULT_SWEEP_INTERVAL`):
                How often the background sweeper wakes up to look for
                idle workspaces.
        """
        self._image = image
        self._api_key = api_key
        self._domain = domain
        self._protocol = protocol
        self._request_timeout_seconds = request_timeout_seconds
        self._timeout_seconds = timeout_seconds
        self._gateway_port = gateway_port
        self._env = dict(env or {})
        self._sandbox_metadata = dict(sandbox_metadata or {})
        self._resource = dict(resource or {})
        self._entrypoint = list(entrypoint or [])
        self._network_policy = network_policy
        self._extra_pip = list(extra_pip or [])
        self._default_mcps = list(default_mcps or [])
        self._skill_paths = list(skill_paths or [])
        if usage_lease_seconds <= 0 or ttl < 0 or sweep_interval <= 0:
            raise ValueError("Lease and sweep interval must be positive")
        self._lifecycle = lifecycle or LocalWorkspaceLifecycle()
        self._usage_lease_seconds = usage_lease_seconds
        self._active: dict[str, int] = {}
        self._generations: dict[str, int] = {}
        self._borrowers: dict[
            tuple[asyncio.Task, str],
            AbstractAsyncContextManager[OpenSandboxWorkspace],
        ] = {}
        self._cleanup_tasks: set[asyncio.Task] = set()
        self._drained = asyncio.Event()
        self._drained.set()
        self._closing = False
        self._ttl = ttl
        self._sweep_interval = sweep_interval
        super().__init__(isolation=isolation)

        # workspace_id -> (workspace, last_access_monotonic)
        self._cache: dict[str, tuple[OpenSandboxWorkspace, float]] = {}
        self._lock = asyncio.Lock()
        self._sweep_task: asyncio.Task | None = None

    def _new_workspace(
        self,
        *,
        workspace_id: str,
        user_id: str,
        agent_id: str,
    ) -> OpenSandboxWorkspace:
        """Construct an uninitialized OpenSandbox workspace.

        ``workspace_id`` is always concrete here — :meth:`get_workspace`
        resolves an unset id via :meth:`assign_workspace_id` first —
        and is forwarded so metadata-based reattachment works on the
        next cache miss.
        """
        ws = OpenSandboxWorkspace(
            workspace_id=workspace_id,
            image=self._image,
            api_key=self._api_key,
            domain=self._domain,
            protocol=self._protocol,
            request_timeout_seconds=self._request_timeout_seconds,
            timeout_seconds=self._timeout_seconds,
            gateway_port=self._gateway_port,
            env=self._env,
            sandbox_metadata={
                "agentscope.user.id": user_id,
                "agentscope.agent.id": agent_id,
                **self._sandbox_metadata,
            },
            resource=self._resource,
            entrypoint=self._entrypoint,
            network_policy=self._network_policy,
            extra_pip=self._extra_pip,
            default_mcps=self._default_mcps,
            skill_paths=self._skill_paths,
        )
        return ws

    async def _get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str | None = None,
    ) -> OpenSandboxWorkspace:
        """Return an initialized workspace, reattaching on cache miss.

        On miss, the manager constructs ``OpenSandboxWorkspace`` with
        the requested ``workspace_id`` and relies on its ``initialize``
        method to find an existing sandbox by metadata, connect or
        resume it depending on state, or create a fresh sandbox
        otherwise.

        Idle eviction is not performed here; the background sweeper
        started by :meth:`__aenter__` handles that.

        Args:
            user_id (`str`):
                Owning user identifier (forwarded as sandbox metadata
                only — not part of the cache key).
            agent_id (`str`):
                Agent identifier (forwarded as sandbox metadata only —
                not part of the cache key).
            session_id (`str`):
                Session identifier (unused; sandboxes are per-workspace,
                sessions partition under ``sessions/<session_id>/``).
            workspace_id (`str | None`, optional):
                Stable workspace identifier — the cache key and the
                value stored in the sandbox's ``agentscope.workspace.id``
                metadata. When unset the manager falls back to
                :meth:`assign_workspace_id` under its isolation policy.

        Returns:
            `OpenSandboxWorkspace`:
                A live, initialized workspace.
        """
        del session_id  # accepted for interface parity; not used here

        if self._closing:
            raise RuntimeError("Workspace manager is closing")
        if not workspace_id:
            workspace_id = await self.assign_workspace_id(
                user_id=user_id,
                agent_id=agent_id,
                session_id="",
            )

        async with self._lock:
            cached = self._cache.get(workspace_id)
            if cached is not None:
                ws, _ = cached
                self._cache[workspace_id] = (ws, time.monotonic())
                return ws

        # Cache miss: build under the lock to prevent two concurrent
        # get_workspace(workspace_id=X) calls from creating two
        # workspaces (and thus two sandboxes) for the same id.
        async with self._lock:
            cached = self._cache.get(workspace_id)
            if cached is not None:
                ws, _ = cached
                self._cache[workspace_id] = (ws, time.monotonic())
                return ws

            ws = self._new_workspace(
                workspace_id=workspace_id,
                user_id=user_id,
                agent_id=agent_id,
            )
            try:
                await ws.initialize()
            except BaseException:
                await ws.release_local()
                raise
            self._cache[workspace_id] = (ws, time.monotonic())
            return ws

    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str | None = None,
    ) -> OpenSandboxWorkspace:
        """Borrow a workspace until the calling asyncio task finishes.

        Args:
            user_id (`str`): Owning user.
            agent_id (`str`): Agent identity.
            session_id (`str`): Session identity.
            workspace_id (`str | None`): Explicit workspace binding.

        Returns:
            `OpenSandboxWorkspace`: Task-protected workspace. Prefer
            ``acquire_workspace`` for a shorter, explicit lifetime.
        """
        if self._closing:
            raise RuntimeError("Workspace manager is closing")
        if not workspace_id:
            workspace_id = await self.assign_workspace_id(
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
            )
        task = asyncio.current_task()
        assert task is not None
        key = (task, workspace_id)
        existing = self._borrowers.get(key)
        if existing is not None:
            return self._cache[workspace_id][0]
        scope = self.acquire_workspace(
            user_id,
            agent_id,
            session_id,
            workspace_id,
        )
        ws = await scope.__aenter__()
        self._borrowers[key] = scope

        def done(_: asyncio.Task) -> None:
            if self._borrowers.pop(key, None) is None:
                return
            cleanup = asyncio.create_task(scope.__aexit__(None, None, None))
            self._cleanup_tasks.add(cleanup)

            def cleaned(finished: asyncio.Task) -> None:
                self._cleanup_tasks.discard(finished)
                if not finished.cancelled() and finished.exception():
                    logger.error(
                        "Task workspace release failed: %s",
                        finished.exception(),
                    )

            cleanup.add_done_callback(cleaned)

        task.add_done_callback(done)
        return ws

    @asynccontextmanager
    async def acquire_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str | None = None,
    ) -> AsyncIterator[OpenSandboxWorkspace]:
        """Hold a renewable lease and local pin throughout execution.

        Args:
            user_id (`str`): Owning user.
            agent_id (`str`): Agent identity.
            session_id (`str`): Session identity.
            workspace_id (`str | None`): Explicit workspace binding.

        Yields:
            `OpenSandboxWorkspace`: Workspace protected from collection.

        Raises:
            `RuntimeError`: Manager is closing or a lease is lost.
        """
        if self._closing:
            raise RuntimeError("Workspace manager is closing")
        if not workspace_id:
            workspace_id = await self.assign_workspace_id(
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
            )
        token = uuid.uuid4().hex
        generation = 0
        deadline = time.monotonic() + self._timeout_seconds
        while not generation:
            if time.monotonic() >= deadline:
                raise TimeoutError("Workspace pause has not completed")
            if self._closing:
                raise RuntimeError("Workspace manager is closing")
            generation = await self._lifecycle.update(
                workspace_id,
                "acquire",
                token,
                self._usage_lease_seconds,
            )
            if not generation:
                await asyncio.sleep(0.05)
        if self._closing:
            await self._lifecycle.update(workspace_id, "release", token, 0)
            raise RuntimeError("Workspace manager is closing")
        owner = asyncio.current_task()
        assert owner is not None
        lost = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._renew_usage(workspace_id, token, owner, lost),
        )
        self._active[workspace_id] = self._active.get(workspace_id, 0) + 1
        self._drained.clear()
        try:
            async with self._lock:
                if self._generations.get(workspace_id) != generation:
                    if self._active[workspace_id] > 1:
                        raise RuntimeError("Stale workspace users must drain")
                    stale = self._cache.pop(workspace_id, None)
                    if stale is not None:
                        await stale[0].release_local()
                    self._generations[workspace_id] = generation
            ws = await self._get_workspace(
                user_id,
                agent_id,
                session_id,
                workspace_id,
            )
            if self._closing:
                raise RuntimeError("Workspace manager is closing")
            yield ws
        except asyncio.CancelledError:
            if lost.is_set():
                raise RuntimeError("Workspace usage lease was lost") from None
            raise
        finally:
            cleanup = asyncio.create_task(
                self._release_usage(workspace_id, token, heartbeat),
            )
            self._cleanup_tasks.add(cleanup)
            cleanup.add_done_callback(self._cleanup_tasks.discard)
            # A second cancellation (e.g. tool-offload teardown) must not
            # interrupt lease release or leave a permanent local pin.
            await asyncio.shield(cleanup)

    async def _release_usage(
        self,
        workspace_id: str,
        token: str,
        heartbeat: asyncio.Task,
    ) -> None:
        """Finish release even when the owning task is being cancelled."""
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        try:
            async with asyncio.timeout(self._usage_lease_seconds):
                await self._lifecycle.update(
                    workspace_id,
                    "release",
                    token,
                    0,
                )
        except Exception:
            # The remote lease expires naturally if Redis is unavailable.
            logger.exception("Workspace usage lease release failed")
        finally:
            self._active[workspace_id] -= 1
            if not self._active[workspace_id]:
                del self._active[workspace_id]
                async with self._lock:
                    cached = self._cache.get(workspace_id)
                    if cached:
                        self._cache[workspace_id] = (
                            cached[0],
                            time.monotonic(),
                        )
                if self._closing:
                    try:
                        await self.close(workspace_id)
                    except Exception:
                        logger.exception("Local workspace release failed")
            if not self._active:
                self._drained.set()

    async def _renew_usage(
        self,
        workspace_id: str,
        token: str,
        owner: asyncio.Task,
        lost: asyncio.Event,
    ) -> None:
        """Cancel the owning execution if protection cannot be renewed."""
        try:
            while not owner.done():
                await asyncio.sleep(self._usage_lease_seconds / 3)
                async with asyncio.timeout(self._usage_lease_seconds / 3):
                    renewed = await self._lifecycle.update(
                        workspace_id,
                        "renew",
                        token,
                        self._usage_lease_seconds,
                    )
                if not renewed:
                    raise RuntimeError("Workspace lease expired")
        except Exception:
            logger.exception("Workspace usage lease renewal failed")
            lost.set()
            owner.cancel()

    async def close(self, workspace_id: str) -> None:
        """Release an idle local connection; never pause the sandbox.

        Args:
            workspace_id (`str`): Workspace to detach. Active borrowers
                retain their connection until they finish.
        """
        async with self._lock:
            if self._active.get(workspace_id):
                return
            entry = self._cache.pop(workspace_id, None)
            if entry is not None:
                await entry[0].release_local()

    async def close_all(self) -> None:
        """Release inactive local connections without remote pause."""
        for workspace_id in list(self._cache):
            await self.close(workspace_id)

    async def __aenter__(self) -> Self:
        """Start idle collection and admit workspace borrowers."""
        self._closing = False
        if self._sweep_task is None:
            self._sweep_task = asyncio.create_task(self._sweep_loop())
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Stop admission, drain local users, and detach local clients."""
        self._closing = True
        if self._sweep_task is not None:
            self._sweep_task.cancel()
            await asyncio.gather(self._sweep_task, return_exceptions=True)
            self._sweep_task = None
        # A context-manager owner may itself have used legacy get_workspace.
        # Release its implicit borrows before waiting for other tasks.
        current = asyncio.current_task()
        for key, scope in list(self._borrowers.items()):
            if key[0] is current:
                self._borrowers.pop(key)
                await scope.__aexit__(None, None, None)
        await self._drained.wait()
        await self.close_all()

    async def _sweep_loop(self) -> None:
        """Periodically close idle workspaces.

        Runs until cancelled. Each tick pops expired entries and closes
        them outside the lock; failures are logged and swallowed so one
        bad sandbox does not poison the sweeper.
        """
        while True:
            try:
                await asyncio.sleep(self._sweep_interval)
            except asyncio.CancelledError:
                return
            try:
                await self._sweep_once()
            except Exception:
                logger.exception("OpenSandbox workspace sweeper tick failed")

    async def _sweep_once(self) -> None:
        """Collect globally idle sandboxes and evict idle local handles."""
        for workspace_id in await self._lifecycle.candidates():
            token = uuid.uuid4().hex
            if not await self._lifecycle.update(
                workspace_id,
                "claim",
                token,
                self._ttl,
            ):
                continue
            collecting = asyncio.create_task(
                self._pause_claimed(workspace_id, token),
            )
            try:
                await asyncio.shield(collecting)
            except asyncio.CancelledError:
                # Graceful node exit must settle an in-flight pause before
                # stopping collection, rather than strand its durable claim.
                await asyncio.shield(collecting)
                raise
        now = time.monotonic()
        for workspace_id, (_, accessed) in list(self._cache.items()):
            if now - accessed > self._ttl:
                await self.close(workspace_id)

    async def _pause_claimed(self, workspace_id: str, token: str) -> None:
        """Settle a pause under a non-expiring exclusion claim."""
        try:
            ws = self._new_workspace(
                workspace_id=workspace_id,
                user_id="",
                agent_id="",
            )
            await ws.pause_remote()
            await self._lifecycle.update(workspace_id, "finish", token, 0)
        except Exception:
            # A delayed remote pause cannot be fenced by Redis. Ambiguous
            # failure retains the claim until an operator reconciles it.
            logger.exception(
                "Workspace %s pause is uncertain; collector claim "
                "retained for reconciliation",
                workspace_id,
            )
