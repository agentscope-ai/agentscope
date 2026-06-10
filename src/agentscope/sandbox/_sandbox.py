# -*- coding: utf-8 -*-
"""Sandbox abstract interface."""

from abc import ABC, abstractmethod

from ._types import SandboxState


class Sandbox(ABC):
    """An active sandbox with a fully isolated workspace.

    Lifecycle:
    1. Acquire via :meth:`SandboxClient.create` (new) or
       :meth:`SandboxClient.resume` (existing).
    2. Call :meth:`start` — initializes or restores the workspace.
    3. Use the sandbox for command execution, file operations, etc.
    4. Call :meth:`stop` — persists the snapshot (does **not** destroy
       resources).
    5. Call :meth:`shutdown` — destroys backend resources (container,
       tmpdir, etc.).
    6. Or use ``async with`` which calls ``stop`` then ``shutdown`` on
       exit.

    The distinction between :meth:`stop` and :meth:`shutdown` is critical:
    - ``stop()``: persist snapshot only — safe for both self-managed and
      user-managed sandboxes.
    - ``shutdown()``: destroy backend resources — only called on
      self-managed sandboxes.
    """

    @abstractmethod
    async def start(self) -> None:
        """Initialize or restore the workspace."""

    @abstractmethod
    async def stop(self) -> None:
        """Persist snapshot without destroying resources."""

    async def shutdown(self) -> None:
        """Destroy backend resources."""
        # no-op by default

    @property
    @abstractmethod
    def state(self) -> SandboxState:
        """Return the current serializable state of this sandbox."""

    @property
    def is_running(self) -> bool:
        """Return whether the sandbox is currently running."""
        return False
