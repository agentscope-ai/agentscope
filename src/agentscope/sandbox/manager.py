"""SandboxManager — creates, tracks, and destroys Sandbox instances.

Optional pool support for RL rollout scenarios: keep warm sandbox instances
and acquire/release them instead of creating fresh ones each time.

Naming convention:
    - ``sandbox_id`` is the unique identifier for each Sandbox instance.
    - ``_instances`` maps ``sandbox_id → Sandbox``.
    - ``list_sandboxes()`` returns info about all live sandbox instances.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from dataclasses import replace

from .config import SandboxConfig
from .sandbox import Sandbox

logger = logging.getLogger(__name__)


class SandboxManager:
    """Central registry for live Sandbox instances.

    Usage::

        mgr = SandboxManager()
        sid = await mgr.create(config)
        sandbox = mgr.get(sid)
        await sandbox.connection.exec("echo hello")
        await mgr.destroy(sid)
    """

    def __init__(self) -> None:
        self._instances: dict[str, Sandbox] = {}
        self._pool: SandboxPool | None = None

    async def create(self, config: SandboxConfig, *, endpoint: str | None = None) -> str:
        """Create & start a sandbox, return its sandbox_id."""
        if endpoint is not None:
            config = replace(config, endpoint=endpoint)
        sandbox = Sandbox(config)
        await sandbox.start()
        self._instances[sandbox.sandbox_id] = sandbox
        logger.info(
            "SandboxManager: created sandbox %s (backend=%s)",
            sandbox.sandbox_id,
            config.backend.type,
        )
        return sandbox.sandbox_id

    def get(self, sandbox_id: str) -> Sandbox:
        """Look up a live sandbox by its unique sandbox_id."""
        try:
            return self._instances[sandbox_id]
        except KeyError as e:
            raise KeyError(
                f"Sandbox {sandbox_id!r} not found. "
                f"Active sandbox_ids: {list(self._instances)}"
            ) from e

    async def destroy(self, sandbox_id: str) -> None:
        """Destroy a sandbox and remove it from the registry."""
        sandbox = self._instances.pop(sandbox_id, None)
        if sandbox is None:
            logger.warning("SandboxManager: destroy called for unknown id %s", sandbox_id)
            return
        await sandbox.close()
        logger.info("SandboxManager: destroyed sandbox %s", sandbox_id)

    def list_sandboxes(self) -> list[dict[str, Any]]:
        """Return info dicts for all live sandbox instances.

        Each dict contains:
          - ``sandbox_id``: unique id of the sandbox
          - ``backend``: backend type string
          - ``started``: whether the sandbox has been started
        """
        return [
            {
                "sandbox_id": sid,
                "backend": s._config.backend.type,
                "started": s._started,
            }
            for sid, s in self._instances.items()
        ]

    async def close_all(self) -> None:
        """Destroy all tracked sandboxes."""
        ids = list(self._instances.keys())
        await asyncio.gather(*(self.destroy(sid) for sid in ids), return_exceptions=True)

    # ─── Pool support ─────────────────────────────────────────

    def enable_pool(self, *, warm_size: int = 4) -> SandboxPool:
        """Create and attach a ``SandboxPool`` to this manager."""
        self._pool = SandboxPool(manager=self, warm_size=warm_size)
        return self._pool

    @property
    def pool(self) -> SandboxPool | None:
        return self._pool


class SandboxPool:
    """Pre-warmed sandbox pool for latency-sensitive workloads (e.g. RL rollout).

    All mutating methods are protected by an ``asyncio.Lock`` to prevent
    race conditions when multiple coroutines acquire/release concurrently.
    """

    def __init__(self, manager: SandboxManager, *, warm_size: int = 4) -> None:
        self._manager = manager
        self._warm_size = warm_size
        self._free: asyncio.Queue[str] = asyncio.Queue()
        self._in_use: set[str] = set()
        self._base_config: SandboxConfig | None = None
        self._lock = asyncio.Lock()

    async def warm(self, config: SandboxConfig) -> None:
        """Pre-create ``warm_size`` sandboxes and add them to the free pool."""
        async with self._lock:
            self._base_config = config
            for _ in range(self._warm_size):
                sid = await self._manager.create(config)
                await self._free.put(sid)
            logger.info("SandboxPool: warmed %d sandboxes", self._warm_size)

    async def acquire(self, *, timeout: float | None = None) -> Sandbox:
        """Acquire a free sandbox from the pool.

        Raises ``RuntimeError`` if no sandbox is available within the timeout.
        """
        try:
            sid = await asyncio.wait_for(self._free.get(), timeout=timeout)
        except asyncio.TimeoutError:
            raise RuntimeError("SandboxPool.acquire timed out — no free sandbox") from None
        async with self._lock:
            self._in_use.add(sid)
        return self._manager.get(sid)

    async def release(self, sandbox: Sandbox) -> None:
        """Return a sandbox to the pool or replace it if it's dead."""
        sid = sandbox.sandbox_id
        async with self._lock:
            self._in_use.discard(sid)
            if await sandbox.connection.running():
                await self._free.put(sid)
            else:
                await self._manager.destroy(sid)
                if self._base_config:
                    new_sid = await self._manager.create(self._base_config)
                    await self._free.put(new_sid)
                logger.info("SandboxPool: replaced dead sandbox %s", sid)

    async def resize(self, new_size: int) -> None:
        """Grow or shrink the pool to ``new_size``."""
        async with self._lock:
            delta = new_size - self._warm_size
            self._warm_size = new_size
            if delta > 0 and self._base_config:
                for _ in range(delta):
                    sid = await self._manager.create(self._base_config)
                    await self._free.put(sid)
            elif delta < 0:
                for _ in range(-delta):
                    if not self._free.empty():
                        sid = await self._free.get()
                        await self._manager.destroy(sid)

    @property
    def stats(self) -> dict[str, int]:
        return {
            "warm_size": self._warm_size,
            "free": self._free.qsize(),
            "in_use": len(self._in_use),
        }
