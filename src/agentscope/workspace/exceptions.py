# -*- coding: utf-8 -*-
"""Workspace exception hierarchy."""


class WorkspaceError(Exception):
    """Base class for workspace-related failures."""


class CapabilityError(WorkspaceError):
    """The current backend does not support a requested capability.

    Attributes:
        capability: The capability that was requested.
        backend: The backend that does not support the capability.
    """

    def __init__(
        self,
        capability: str,
        *,
        backend: str | None = None,
    ) -> None:
        self.capability = capability
        self.backend = backend
        msg = f"Capability not available: {capability}"
        if backend:
            msg += f" (backend={backend})"
        super().__init__(msg)


class UnsupportedOperation(WorkspaceError):
    """The backend does not implement an optional operation."""
