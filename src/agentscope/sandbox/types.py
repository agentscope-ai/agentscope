# -*- coding: utf-8 -*-
"""Shared value types for sandbox connection + factory layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ExecResult:
    """Result of executing a command inside a sandbox."""

    exit_code: int
    stdout: bytes
    stderr: bytes

    def ok(self) -> bool:
        """Return ``True`` if ``exit_code == 0``."""
        return self.exit_code == 0


@dataclass(slots=True)
class SandboxCreateOptions:
    """Normalized inputs for ``SandboxConnection.create``.

    ``extra`` holds backend-specific flags (E2B template, Docker image, etc.)
    without forcing the core layer to know each vendor's schema.
    """

    backend: str
    env: dict[str, str] = field(default_factory=dict)
    exposed_ports: list[int] = field(default_factory=list)
    volumes: dict[str, str] = field(default_factory=dict)
    startup_commands: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SerializedSandboxState:
    """Serializable snapshot for resume / reconnect.

    Attributes:
        backend: Must match the connection class's ``backend_id`` so
            ``resume(state)`` can dispatch to the right factory.
        payload: Opaque to the core layer; typically holds vendor ids
            (e.g. E2B ``sandbox_id``), workspace root path, tokens, etc.
    """

    backend: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SandboxConnectionCapabilities:
    """Optional features a backend may support.

    Check before calling PTY / networking / snapshot APIs.
    """

    pty: bool = False
    exposed_ports: bool = False
    snapshot: bool = False


@dataclass(frozen=True, slots=True)
class ExposedPortEndpoint:
    """Host endpoint for a logical container port when port mapping exists."""

    host: str
    port: int
    tls: bool = False
