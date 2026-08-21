# -*- coding: utf-8 -*-
"""Workspace manager implementations."""

import hashlib
from abc import ABC, abstractmethod
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

    async def assign_workspace_id(
        self,
        *,
        user_id: str,
        agent_id: str,
        session_id: str,
        storage: "StorageBase | None" = None,
    ) -> str:
        """Mint a workspace id under :attr:`_isolation`.

        Called by the session-creation flow when the caller did not
        supply an explicit ``workspace_id``.

        * ``PER_SESSION`` → fresh UUID.
        * ``PER_AGENT`` → the id an earlier session of this
          ``(user, agent)`` already bound, else a fresh one. With
          ``storage`` absent the binding cannot be read, so a
          deterministic BLAKE2b of ``user::agent`` stands in for it.
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
            storage (`StorageBase | None`, optional):
                Backend to read the ``(user, agent)`` binding from.
                ``None`` falls back to the deterministic hash.

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
        if self._isolation is IsolationPolicy.PER_AGENT:
            if storage is None:
                return hashlib.blake2b(
                    f"{user_id}::{agent_id}".encode("utf-8"),
                    digest_size=8,
                ).hexdigest()
            for record in await storage.list_sessions(user_id, agent_id):
                if record.config.workspace_id:
                    return record.config.workspace_id
        return await self._mint_workspace_id()

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
                Explicit workspace binding. ``None`` triggers
                :meth:`assign_workspace_id` fallback — expected only
                for callers without a persisted binding.
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
