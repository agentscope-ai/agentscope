# -*- coding: utf-8 -*-
"""Core types for the sandbox system."""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class IsolationScope(enum.Enum):
    """Controls how agent sandbox state is isolated and shared across calls.

    Scope selection:
    - ``SESSION`` — isolated per session.
    - ``USER`` — shared across all sessions of the same user; the default.
      When ``user_id`` is absent, falls back to ``SESSION``.
    - ``AGENT`` — shared across all users and sessions of the same agent.
    - ``GLOBAL`` — globally shared within the same state store.
    """

    SESSION = "session"
    USER = "user"
    AGENT = "agent"
    GLOBAL = "global"


@dataclass(frozen=True)
class SandboxIsolationKey:
    """Immutable key that uniquely identifies a sandbox state slot."""

    scope: IsolationScope
    value: str

    @staticmethod
    def resolve(
        scope: IsolationScope | None,
        session_id: str | None,
        user_id: str | None,
        agent_id: str,
    ) -> Optional["SandboxIsolationKey"]:
        """Resolve an isolation key from scope and runtime identifiers.

        Resolution rules:
        - ``SESSION`` – requires a non-blank ``session_id``.
        - ``USER`` – uses ``user_id`` when present; falls back to
          ``SESSION`` using ``session_id`` when absent. Returns ``None``
          only when both are absent.
        - ``AGENT`` – value = ``agent_id`` (always present).
        - ``GLOBAL`` – value = ``__global__`` (always present).
        - ``None`` scope – treated as ``USER``.
        """
        effective = scope if scope is not None else IsolationScope.USER
        if effective == IsolationScope.SESSION:
            if not session_id:
                return None
            return SandboxIsolationKey(IsolationScope.SESSION, session_id)
        if effective == IsolationScope.USER:
            if user_id:
                return SandboxIsolationKey(IsolationScope.USER, user_id)
            if session_id:
                logger.debug(
                    "[sandbox] USER isolation requested but user_id is absent"
                    " — falling back to SESSION scope using session_id",
                )
                return SandboxIsolationKey(IsolationScope.SESSION, session_id)
            logger.warning(
                "[sandbox] USER isolation requested but both user_id and"
                " session_id are absent — skipping state lookup; a fresh"
                " sandbox will be created",
            )
            return None
        if effective == IsolationScope.AGENT:
            return SandboxIsolationKey(IsolationScope.AGENT, agent_id)
        if effective == IsolationScope.GLOBAL:
            return SandboxIsolationKey(IsolationScope.GLOBAL, "__global__")
        return None

    def __str__(self) -> str:
        return f"SandboxIsolationKey(scope={self.scope.value}, value={self.value})"


@dataclass
class SandboxState:
    """Serializable state of a sandbox, persisted for resume across calls."""

    session_id: str
    workspace_spec: dict[str, Any] | None = None
    snapshot: dict[str, Any] | None = None
    workspace_root_ready: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "workspace_spec": self.workspace_spec,
            "snapshot": self.snapshot,
            "workspace_root_ready": self.workspace_root_ready,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "SandboxState":
        return SandboxState(
            session_id=data.get("session_id", ""),
            workspace_spec=data.get("workspace_spec"),
            snapshot=data.get("snapshot"),
            workspace_root_ready=data.get("workspace_root_ready", False),
        )


@dataclass
class SandboxContext:
    """Configuration for sandbox behaviour."""

    external_sandbox: "Sandbox | None" = None
    external_state: SandboxState | None = None
    isolation_scope: IsolationScope | None = IsolationScope.USER
    workspace_spec: Any | None = None
    snapshot_spec: Any | None = None
    client_options: dict[str, Any] | None = None


class SandboxLease:
    """A handle that represents a held execution right on a sandbox isolation slot."""

    def close(self) -> None:
        """Release the execution right. Must be idempotent."""
        raise NotImplementedError

    @staticmethod
    def noop() -> "SandboxLease":
        return _NoopSandboxLease()


class _NoopSandboxLease(SandboxLease):
    def close(self) -> None:
        pass


# Type alias for pluggable execution guards.
SandboxExecutionGuard = Callable[[SandboxIsolationKey], SandboxLease]


def noop_execution_guard() -> SandboxExecutionGuard:
    """Return a no-op guard that always allows execution immediately."""
    return lambda _key: SandboxLease.noop()


@dataclass
class SandboxAcquireResult:
    """Result of acquiring a sandbox from :class:`SandboxManager`."""

    sandbox: "Sandbox"
    self_managed: bool = False
    lease: SandboxLease = field(default_factory=SandboxLease.noop)

    @staticmethod
    def self_managed(sandbox: "Sandbox", lease: SandboxLease | None = None) -> "SandboxAcquireResult":
        return SandboxAcquireResult(sandbox, True, lease or SandboxLease.noop())

    @staticmethod
    def user_managed(sandbox: "Sandbox") -> "SandboxAcquireResult":
        return SandboxAcquireResult(sandbox, False, SandboxLease.noop())
