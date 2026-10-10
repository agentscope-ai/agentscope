# -*- coding: utf-8 -*-
"""Workspace manager implementations."""

import asyncio
import hashlib
from abc import ABC, abstractmethod
from collections import defaultdict
from enum import StrEnum
from typing import TYPE_CHECKING, Self

from ..._utils._common import _generate_id
from ...workspace import WorkspaceBase

if TYPE_CHECKING:
    from ..storage import StorageBase


class IsolationPolicy(StrEnum):
    """Workspace isolation grain for
    :meth:`WorkspaceManagerBase.assign_workspace_id`.
    """

    PER_SESSION = "per_session"
    PER_AGENT = "per_agent"
    PER_USER = "per_user"


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

    def __init__(
        self,
        *,
        isolation: IsolationPolicy = IsolationPolicy.PER_AGENT,
    ) -> None:
        """Bind the isolation policy for :meth:`assign_workspace_id`.

        Subclasses MUST forward ``isolation`` here via
        ``super().__init__(isolation=isolation)``.

        Args:
            isolation (`IsolationPolicy`, defaults to `PER_AGENT`):
                Isolation grain for the manager.
        """
        self._isolation: IsolationPolicy = isolation
        self._storage: "StorageBase | None" = None
        # Serialises read-binding-then-mint per (user, agent), so two
        # concurrent first sessions cannot each mint a workspace.
        self._bind_locks: defaultdict[
            tuple[str, str],
            asyncio.Lock,
        ] = defaultdict(asyncio.Lock)
        # Ids handed out but not yet persisted by the session flow.
        # The lock is released before that write lands, so without
        # this the next request would read an empty session list and
        # bind a second workspace to the same pair.
        self._reserved: dict[tuple[str, str], str] = {}

    def bind_storage(self, storage: "StorageBase") -> None:
        """Hand the manager the backend holding workspace bindings.

        Wired by :func:`agentscope.app.create_app` rather than taken as
        a constructor argument, because the application author builds
        the manager before the app exists to supply a storage backend.

        Args:
            storage (`StorageBase`):
                The application's storage backend.
        """
        self._storage = storage

    async def assign_workspace_id(
        self,
        *,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> str:
        """Mint a workspace id under :attr:`_isolation`.

        Called by the session-creation flow when the caller did not
        supply an explicit ``workspace_id``.

        * ``PER_SESSION`` → fresh UUID.
        * ``PER_AGENT`` → the id an earlier session of this
          ``(user, agent)`` already bound, else a fresh one. A team
          worker's binding is its leader's workspace, which the team
          flow assigns on purpose. Without a storage backend the
          binding cannot be read, so a deterministic BLAKE2b of
          ``user::agent`` stands in for it.
          A minted id is held until a session record carries it, since
          that write lands after this returns. Both the serialisation
          and the reservation are process-local: two app workers
          racing the very first session of one pair can still bind two
          workspaces to it.
        * ``PER_USER`` → deterministic BLAKE2b of ``user::``.

        Managers that pre-warm override this to draw the fresh ids from
        their buffer, so the id of an already-running workspace becomes
        the binding rather than naming one still to be built.

        Args:
            user_id (`str`):
                The owning user id.
            agent_id (`str`):
                The agent the session belongs to.
            session_id (`str`):
                The session id being provisioned (only used by the
                per-session grain to underline its randomness).

        Returns:
            `str`:
                A workspace id.
        """
        del session_id
        if self._isolation is IsolationPolicy.PER_USER:
            return hashlib.blake2b(
                f"user::{user_id}".encode("utf-8"),
                digest_size=8,
            ).hexdigest()
        if self._isolation is not IsolationPolicy.PER_AGENT:
            return await self._mint_workspace_id()

        if self._storage is None:
            return hashlib.blake2b(
                f"{user_id}::{agent_id}".encode("utf-8"),
                digest_size=8,
            ).hexdigest()
        async with self._bind_locks[(user_id, agent_id)]:
            for record in await self._storage.list_sessions(
                user_id,
                agent_id,
            ):
                if record.config.workspace_id:
                    self._reserved.pop((user_id, agent_id), None)
                    return record.config.workspace_id
            reserved = self._reserved.get((user_id, agent_id))
            if reserved:
                return reserved
            workspace_id = await self._mint_workspace_id()
            self._reserved[(user_id, agent_id)] = workspace_id
            return workspace_id

    async def _mint_workspace_id(self) -> str:
        """Produce an id for a workspace nobody holds yet.

        The pre-warming managers override this to return the id of a
        buffered, already-running workspace.
        """
        return _generate_id()

    @abstractmethod
    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str | None = None,
    ) -> WorkspaceBase:
        """Return an initialized workspace.

        Args:
            user_id (`str`):
                The user id.
            agent_id (`str`):
                The agent id.
            session_id (`str`):
                The session id.
            workspace_id (`str | None`, optional):
                Explicit workspace binding. An empty id or ``None``
                triggers the :meth:`assign_workspace_id` fallback —
                expected only for callers without a persisted binding.
        """

    @abstractmethod
    async def close(self, workspace_id: str) -> None:
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
