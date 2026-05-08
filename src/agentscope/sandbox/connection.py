# -*- coding: utf-8 -*-
"""SandboxConnection — the backend abstraction class.

Each backend (E2B, Docker, local_temp, ...) subclasses this **once**.
The subclass provides:

  - ``@classmethod create(options) -> Self``  (factory)
  - instance methods: exec / read / write / destroy / close /
    is_running
  - optional: resume / PTY / ports / snapshot

``create_sandbox_connection(options)`` dispatches via
``options.backend_id`` through the registry
(``register_sandbox_connection_type`` / ``get_sandbox_connection_type``).
"""

from abc import ABC, abstractmethod

from .exceptions import CapabilityError, UnsupportedOperation
from .types import (
    SandboxExecutionResult,
    SandboxInternalEndpoint,
    SandboxConnectionCapabilities,
    SandboxInitializationConfig,
    SerializedSandboxState,
)


class SandboxConnection(ABC):
    """Handle to one running sandbox instance.

    Required: exec + read/write + destroy + close + is_running.
    Optional: PTY, ports, snapshot, resume (gate on ``get_capabilities()``).
    """

    @property
    @abstractmethod
    def backend_id(self) -> str:
        """Identifier for this backend (e.g. ``'local_temp'``, ``'e2b'``)."""

    # ─── factory ──────────────────────────────────────────────

    @classmethod
    @abstractmethod
    async def create(
        cls,
        options: SandboxInitializationConfig,
    ) -> "SandboxConnection":
        """Provision a new sandbox and return a connected instance."""

    @classmethod
    async def resume(
        cls,
        state: SerializedSandboxState,
    ) -> "SandboxConnection":
        """Reattach to an existing sandbox from serialized state (optional)."""
        raise UnsupportedOperation(
            "resume not implemented for this backend",
        )

    # ─── execution ────────────────────────────────────────────

    @abstractmethod
    async def exec(
        self,
        command: str,
        *,
        timeout: float | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> SandboxExecutionResult:
        """Run a shell command string inside the sandbox."""

    # ─── filesystem ───────────────────────────────────────────

    @abstractmethod
    async def read(self, path: str) -> bytes:
        """Read a sandbox-relative file path as bytes."""

    @abstractmethod
    async def write(self, path: str, data: bytes) -> None:
        """Write bytes to a sandbox-relative path."""

    # ─── lifecycle ────────────────────────────────────────────

    @abstractmethod
    async def destroy(self) -> None:
        """Hard cleanup: release **all** backend resources.

        Idempotent. After ``destroy()`` the connection is unusable.
        """

    async def close(self) -> None:
        """Soft cleanup: release local handles only.

        Default delegates to ``destroy()``; override if you want a lighter
        teardown that keeps the remote sandbox alive (e.g. for pool reuse).
        """
        await self.destroy()

    @abstractmethod
    async def is_running(self) -> bool:
        """Best-effort liveness check."""

    async def __aenter__(self) -> "SandboxConnection":
        """Enter context manager (identity)."""
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Leave context manager — destroy connection."""
        await self.destroy()

    # ─── capabilities ─────────────────────────────────────────

    def get_capabilities(self) -> SandboxConnectionCapabilities:
        """Return capability flags for optional features."""
        return SandboxConnectionCapabilities(
            has_pty=self.supports_pty(),
            has_exposed_ports=self.supports_exposed_ports(),
            has_snapshot=self.supports_snapshot(),
        )

    _supports_pty: bool = False
    _supports_exposed_ports: bool = False
    _supports_snapshot: bool = False

    def supports_pty(self) -> bool:
        """Whether PTY APIs are implemented."""
        return self._supports_pty

    def supports_exposed_ports(self) -> bool:
        """Whether host port mapping can be resolved."""
        return self._supports_exposed_ports

    def supports_snapshot(self) -> bool:
        """Whether workspace snapshot/restore is implemented."""
        return self._supports_snapshot

    # ─── optional: PTY ────────────────────────────────────────

    async def pty_start(self, command: str, **kwargs: object) -> int:
        """PTY attach — unsupported unless overridden."""
        raise CapabilityError("pty", backend=self.backend_id)

    async def pty_write(self, session_id: int, data: str) -> str:
        """PTY write — unsupported unless overridden."""
        raise CapabilityError("pty", backend=self.backend_id)

    # ─── optional: networking ─────────────────────────────────

    async def resolve_exposed_port(
        self,
        port: int,
    ) -> SandboxInternalEndpoint:
        """Map logical container port to host endpoint."""
        raise CapabilityError("exposed_ports", backend=self.backend_id)

    # ─── optional: persistence ────────────────────────────────

    async def export_state(self) -> SerializedSandboxState:
        """Serialize connection state for resume."""
        raise CapabilityError("export_state", backend=self.backend_id)

    async def snapshot_workspace(self) -> bytes:
        """Export workspace as archive bytes."""
        raise CapabilityError("snapshot", backend=self.backend_id)

    async def restore_workspace(self, data: bytes) -> None:
        """Restore workspace from archive bytes."""
        raise CapabilityError("snapshot", backend=self.backend_id)


# ---------------------------------------------------------------------------
# Global registry: backend_id → Connection class
# ---------------------------------------------------------------------------

_registry: dict[str, type[SandboxConnection]] = {}


def register_sandbox_connection_type(
    cls: type[SandboxConnection],
) -> None:
    """Register a ``SandboxConnection`` subclass by its ``backend_id``."""
    bid = cls.backend_id.fget(cls)  # type: ignore[attr-defined]
    if isinstance(bid, property):
        raise TypeError(
            f"Cannot read backend_id from {cls.__name__}; "
            "ensure backend_id is a concrete @property on the class.",
        )
    if bid in _registry:
        raise ValueError(f"SandboxConnection already registered for {bid!r}")
    _registry[bid] = cls


def get_sandbox_connection_type(
    backend_id: str,
) -> type[SandboxConnection]:
    """Look up a registered ``SandboxConnection`` class by backend id."""
    try:
        return _registry[backend_id]
    except KeyError as e:
        available = list(_registry.keys())
        raise KeyError(
            f"No SandboxConnection registered for {backend_id!r}. "
            f"Available backends: {available}",
        ) from e


async def create_sandbox_connection(
    options: SandboxInitializationConfig,
) -> SandboxConnection:
    """Create a connection using the registry for ``options.backend_id``.

    Dispatches to the registered ``SandboxConnection`` subclass.
    """
    cls = get_sandbox_connection_type(options.backend_id)
    return await cls.create(options)
