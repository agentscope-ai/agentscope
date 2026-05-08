# -*- coding: utf-8 -*-
"""SandboxManager — creates, tracks, and destroys Sandbox instances.

Optional pool support for RL rollout scenarios: keep warm sandbox instances
and acquire/release them instead of creating fresh ones each time.

Naming convention:
    - ``sandbox_id`` is the unique identifier for each Sandbox instance.
    - ``_instances`` maps ``sandbox_id → Sandbox``.
    - ``list_sandboxes()`` returns info about all live sandbox instances.
"""

import asyncio
from dataclasses import replace
from typing import Any

from .._logging import logger
from .config import SandboxConfig
from .sandbox import Sandbox


class SandboxManager:
    """Central registry for live Sandbox instances.

    Optionally supports a warm pool of pre-created sandboxes for
    low-latency acquire/release (call :meth:`enable_pool`).

    Usage::

        mgr = SandboxManager()
        sandbox = await mgr.create_sandbox(config)
        await sandbox.connection.exec("echo hello")
        await mgr.destroy(sandbox.sandbox_id)
    """

    def __init__(self) -> None:
        self._instances: dict[str, Sandbox] = {}

        self._pool_capacity: int = 0
        self._pool_free: asyncio.Queue[str] = asyncio.Queue()
        self._pool_in_use: set[str] = set()
        self._pool_config: SandboxConfig | None = None
        self._pool_lock = asyncio.Lock()
        self._pool_enabled = False

    # ─── core CRUD ────────────────────────────────────────────

    async def create_sandbox(
        self,
        config: SandboxConfig,
        *,
        endpoint: str | None = None,
    ) -> Sandbox:
        """Create & start a sandbox, return the Sandbox instance."""
        if endpoint:
            config = replace(config, endpoint=endpoint)
        sandbox = Sandbox(config)
        await sandbox.start()
        self._instances[sandbox.sandbox_id] = sandbox
        logger.info(
            "SandboxManager: created sandbox %s (backend=%s)",
            sandbox.sandbox_id,
            config.backend.type,
        )
        return sandbox

    def get_sandbox(self, sandbox_id: str) -> Sandbox:
        """Look up a live sandbox by its unique sandbox_id."""
        try:
            return self._instances[sandbox_id]
        except KeyError as e:
            raise KeyError(
                f"Sandbox {sandbox_id!r} not found. "
                f"Active sandbox_ids: {list(self._instances)}",
            ) from e

    async def destroy(self, sandbox_id: str) -> None:
        """Destroy a sandbox and remove it from the registry."""
        sandbox = self._instances.pop(sandbox_id, None)
        if not sandbox:
            logger.warning(
                "SandboxManager: destroy called for unknown id %s",
                sandbox_id,
            )
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
                "backend": s.backend_type,
                "started": s.started,
            }
            for sid, s in self._instances.items()
        ]

    async def close_all(self) -> None:
        """Destroy all tracked sandboxes."""
        ids = list(self._instances.keys())
        await asyncio.gather(
            *(self.destroy(sid) for sid in ids),
            return_exceptions=True,
        )

    # ─── Pool (integrated to avoid circular references) ───────

    def enable_pool(self, *, capacity: int = 4) -> "SandboxManager":
        """Enable warm-pool mode with ``capacity`` pre-created sandboxes.

        Returns ``self`` for chaining.
        """
        self._pool_capacity = capacity
        self._pool_enabled = True
        return self

    @property
    def pool_enabled(self) -> bool:
        """Whether :meth:`enable_pool` has been called."""
        return self._pool_enabled

    async def warm_up_pool(self, config: SandboxConfig) -> None:
        """Pre-create ``capacity`` sandboxes and fill the free queue."""
        if not self._pool_enabled:
            raise RuntimeError("Call enable_pool() first")
        async with self._pool_lock:
            self._pool_config = config
            for _ in range(self._pool_capacity):
                sandbox = await self.create_sandbox(config)
                await self._pool_free.put(sandbox.sandbox_id)
            logger.info(
                "Pool: warmed %d sandboxes",
                self._pool_capacity,
            )

    async def acquire_from_pool(
        self,
        *,
        timeout: float | None = None,
    ) -> Sandbox:
        """Acquire a free sandbox from the pool.

        Raises ``RuntimeError`` if no sandbox is available
        within the timeout.
        """
        async with self._pool_lock:
            try:
                sid = await asyncio.wait_for(
                    self._pool_free.get(),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                raise RuntimeError(
                    "acquire_from_pool timed out — no free sandbox",
                ) from None
            self._pool_in_use.add(sid)
        return self.get_sandbox(sid)

    async def release_to_pool(self, sandbox: Sandbox) -> None:
        """Return a sandbox to the pool; replace it if dead."""
        sid = sandbox.sandbox_id
        async with self._pool_lock:
            self._pool_in_use.discard(sid)
            if await sandbox.connection.is_running():
                await self._pool_free.put(sid)
            else:
                await self.destroy(sid)
                if self._pool_config:
                    new_box = await self.create_sandbox(self._pool_config)
                    await self._pool_free.put(new_box.sandbox_id)
                logger.info("Pool: replaced dead sandbox %s", sid)

    async def resize_pool(self, new_size: int) -> None:
        """Grow or shrink the pool to ``new_size``."""
        async with self._pool_lock:
            delta = new_size - self._pool_capacity
            self._pool_capacity = new_size
            if delta > 0 and self._pool_config:
                for _ in range(delta):
                    sandbox = await self.create_sandbox(self._pool_config)
                    await self._pool_free.put(sandbox.sandbox_id)
            elif delta < 0:
                for _ in range(-delta):
                    if not self._pool_free.empty():
                        sid = await self._pool_free.get()
                        await self.destroy(sid)

    def get_pool_state(self) -> dict[str, int]:
        """Counts for capacity, free queue, and in-use sandboxes."""
        return {
            "capacity": self._pool_capacity,
            "free": self._pool_free.qsize(),
            "in_use": len(self._pool_in_use),
        }
