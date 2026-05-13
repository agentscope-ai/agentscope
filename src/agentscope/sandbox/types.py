# -*- coding: utf-8 -*-
"""Shared value types for sandbox connection + factory layers."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class SandboxExecutionResult:
    """Result of executing a command inside a sandbox."""

    exit_code: int
    stdout: bytes
    stderr: bytes

    def is_ok(self) -> bool:
        """Return ``True`` if ``exit_code == 0``."""
        return self.exit_code == 0


@dataclass(slots=True)
class SandboxInitializationConfig:
    """Normalized inputs for ``SandboxConnection.create``.

    ``extra`` holds backend-specific flags (E2B template, Docker image, etc.)
    without forcing the core layer to know each vendor's schema. For E2B and
    Docker, use ``working_dir`` for the path root inside the remote/container
    environment (legacy key ``workspace`` is still read for compatibility).
    """

    backend_type: str
    env: dict[str, str] = field(default_factory=dict)
    exposed_ports: list[int] = field(default_factory=list)
    volumes: dict[str, str] = field(default_factory=dict)
    startup_commands: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SerializedSandboxState:
    """Serializable snapshot for resume / reconnect.

    Produced by :meth:`SandboxConnection.export_state` to capture the
    minimal state needed to re-attach to an existing sandbox.

    Attributes:
        backend_type: Must match the connection class's ``backend_type`` so
            ``resume(state)`` can dispatch to the right factory.
        payload: Transparent to anyone outside of a sandbox instance.
            Typically holds vendor ids (e.g. E2B ``sandbox_id``), remote
            working-directory path (E2B / Docker: ``working_dir``), tokens,
            etc.
    """

    backend_type: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SandboxConnectionCapabilities:
    """Optional features a backend may support.

    Check before calling PTY / networking / working-dir snapshot APIs.
    """

    has_pty: bool = False
    has_exposed_ports: bool = False
    has_snapshot: bool = False


@dataclass(frozen=True, slots=True)
class SandboxInternalEndpoint:
    """Endpoint for a service inside the sandbox, resolved to a
    host-accessible address when port mapping is available.

    Attributes:
        host: Hostname or IP address.
        port: Port number.
        is_tls_enabled: Whether TLS is active on this endpoint.
    """

    host: str
    port: int
    is_tls_enabled: bool = False
