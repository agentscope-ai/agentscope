# -*- coding: utf-8 -*-
"""Workspace manager implementations."""

from abc import ABC, abstractmethod
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Self

from ..._utils._common import _generate_id
from ...workspace import WorkspaceBase
from ...workspace import WorkspaceActor, WorkspaceRunHandle, WorkspaceView
from ..storage import StorageBase, WorkspaceBinding, WorkspaceRecord


class WorkspaceManagerBase(ABC):
    """Abstract base for workspace managers.

    Subclasses are expected to be used as async context managers — entering
    the context activates any background machinery the subclass needs (e.g.
    a TTL sweeper task) and exiting it tears that machinery down and closes
    every cached workspace via :meth:`close_all`.

    The default ``__aenter__`` / ``__aexit__`` cover the common case where a
    subclass has no background machinery: enter is a no-op, exit just calls
    :meth:`close_all`. Subclasses that own background tasks should override
    both.
    """

    _storage: StorageBase | None = None
    backend_name: str = "local"

    def bind_storage(self, storage: StorageBase) -> None:
        """Attach the store used for distributed workspace run leases."""
        self._storage = storage

    def _active_run_counts(self) -> dict[tuple[str, str], int]:
        return self.__dict__.setdefault("_active_runs", {})

    async def can_evict(self, user_id: str, workspace_id: str) -> bool:
        """Check local and distributed run leases before eviction."""
        if self._active_run_counts().get((user_id, workspace_id), 0) > 0:
            return False
        if self._storage is not None:
            return not await self._storage.has_workspace_run_leases(
                user_id,
                workspace_id,
            )
        return True

    @abstractmethod
    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str,
    ) -> WorkspaceBase:
        """Return an initialized workspace.

        Args:
            user_id (`str`):
                The user id.
            agent_id (`str`):
                The agent id.
            session_id (`str`):
                The session id.
            workspace_id (`str`):
                The workspace id (reconnection credential).
        """

    @abstractmethod
    async def create_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> WorkspaceBase:
        """Create a new workspace and return it."""

    @abstractmethod
    async def close(
        self,
        workspace_id: str,
        user_id: str | None = None,
    ) -> None:
        """Close and evict a single workspace from the cache."""

    @abstractmethod
    async def close_all(self) -> None:
        """Close every cached workspace.

        Pure "close all currently tracked workspaces" semantics — does not
        imply the manager itself is being torn down. Use ``async with`` (or
        :meth:`__aexit__` directly) for full manager shutdown.
        """

    async def __aenter__(self) -> Self:
        """Enter the manager's lifetime. Default is a no-op."""
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Exit the manager's lifetime — closes all cached workspaces."""
        await self.close_all()

    @asynccontextmanager
    async def open_workspace(
        self,
        binding: WorkspaceBinding,
    ) -> AsyncIterator[WorkspaceRunHandle]:
        """Open an actor-scoped handle over a cached workspace runtime."""
        runtime = await self.get_workspace(
            binding.user_id,
            binding.agent_id,
            binding.session_id,
            binding.workspace_id,
        )
        if self._storage is not None:
            record = await self._storage.get_workspace(
                binding.user_id,
                binding.workspace_id,
            )
            if record is None:
                record = WorkspaceRecord(
                    id=binding.workspace_id,
                    owner_user_id=binding.user_id,
                    scope="team" if binding.team_id else "session",
                    scope_id=binding.team_id or binding.session_id,
                    backend=self.backend_name,
                    backend_ref={"workdir": runtime.workdir},
                    status="ready",
                )
                await self._storage.upsert_workspace(
                    binding.user_id,
                    record,
                )
            await self._storage.upsert_workspace_binding(
                binding.user_id,
                binding,
            )
        actor = WorkspaceActor(
            user_id=binding.user_id,
            agent_id=binding.agent_id,
            session_id=binding.session_id,
            team_id=binding.team_id,
            role=binding.role,
            capabilities=set(binding.capabilities),
        )

        if self._storage is None:
            lease_id = _generate_id()
        else:
            lease_id = await self._storage.acquire_workspace_run_lease(
                binding.user_id,
                binding.workspace_id,
            )
        run_key = (binding.user_id, binding.workspace_id)
        counts = self._active_run_counts()
        counts[run_key] = counts.get(run_key, 0) + 1

        async def _release() -> None:
            try:
                if self._storage is not None:
                    await self._storage.release_workspace_run_lease(
                        binding.user_id,
                        binding.workspace_id,
                        lease_id,
                    )
            finally:
                remaining = counts.get(run_key, 1) - 1
                if remaining > 0:
                    counts[run_key] = remaining
                else:
                    counts.pop(run_key, None)

        async def _heartbeat() -> None:
            while self._storage is not None:
                await asyncio.sleep(1200)
                renewed = await self._storage.renew_workspace_run_lease(
                    binding.user_id,
                    binding.workspace_id,
                    lease_id,
                )
                if not renewed:
                    return

        handle = WorkspaceRunHandle(
            view=WorkspaceView(runtime=runtime, actor=actor),
            lease_id=lease_id,
            _release=_release,
            _heartbeat=(
                asyncio.create_task(_heartbeat())
                if self._storage is not None
                else None
            ),
        )
        try:
            yield handle
        finally:
            await handle.close()
