# -*- coding: utf-8 -*-
"""Shared value types for workspace backends."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Result of executing a command inside a workspace backend."""

    exit_code: int
    stdout: bytes
    stderr: bytes

    def is_ok(self) -> bool:
        """Return ``True`` if ``exit_code == 0``."""
        return self.exit_code == 0


@dataclass(slots=True)
class SerializedWorkspaceState:
    """Serializable snapshot for workspace resume / reconnect.

    Attributes:
        backend_type: Must match the workspace class so ``restore``
            can dispatch to the right factory.
        payload: Opaque data for the specific backend (container ids,
            working directory, tokens, etc.).
    """

    backend_type: str
    payload: dict[str, Any] = field(default_factory=dict)
