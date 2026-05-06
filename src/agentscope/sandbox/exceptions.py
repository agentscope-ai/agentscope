"""Errors for the sandbox layer."""


class SandboxError(Exception):
    """Base class for sandbox-related failures."""


class CapabilityError(SandboxError):
    """Raised when the current backend does not support an optional capability.

    Attributes:
        capability: The capability that was requested.
        backend: The backend that does not support the capability.
    """

    def __init__(self, capability: str, *, backend: str | None = None) -> None:
        self.capability = capability
        self.backend = backend
        msg = f"Capability not available: {capability}"
        if backend:
            msg += f" (backend={backend})"
        super().__init__(msg)


class UnsupportedOperation(SandboxError):
    """Raised when an operation is not implemented for this backend (e.g. resume)."""
