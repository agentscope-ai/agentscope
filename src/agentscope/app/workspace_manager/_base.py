# -*- coding: utf-8 -*-
"""Workspace manager implementations."""

import hashlib
from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Self

from ..._utils._common import _generate_id
from ...workspace import WorkspaceBase


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

    def assign_workspace_id(
        self,
        *,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> str:
        """Mint a workspace id under :attr:`_isolation`.

        Pure function — no I/O, no storage access. Called by the
        session-creation flow when the caller did not supply an
        explicit ``workspace_id``.

        * ``PER_SESSION`` → fresh UUID.
        * ``PER_AGENT`` → deterministic BLAKE2b of ``user::agent``.
        * ``PER_USER`` → deterministic BLAKE2b of ``user::``.

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
        if self._isolation is IsolationPolicy.PER_AGENT:
            return hashlib.blake2b(
                f"{user_id}::{agent_id}".encode("utf-8"),
                digest_size=8,
            ).hexdigest()
        if self._isolation is IsolationPolicy.PER_USER:
            return hashlib.blake2b(
                f"user::{user_id}".encode("utf-8"),
                digest_size=8,
            ).hexdigest()
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
