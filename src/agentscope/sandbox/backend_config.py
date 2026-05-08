# -*- coding: utf-8 -*-
"""Backend parameter sets for SandboxConnection backends.

Each subclass provides typed fields for one vendor. ``type`` is a read-only
property returning the backend identifier string.

``Sandbox._merge_infra_requirements()`` flattens these into a vendor-neutral
``SandboxInitializationConfig`` before passing to
``SandboxConnection.create()``.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class BackendParams:
    """User-facing backend config — typed fields for each vendor.

    Subclasses must override the :attr:`type` property to return the
    backend identifier string (e.g. ``"docker"``, ``"e2b"``).
    """

    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def type(self) -> str:
        """Backend identifier string. Must be overridden by subclasses."""
        raise NotImplementedError("Subclasses must define the backend type")


@dataclass(slots=True)
class DockerBackendParams(BackendParams):
    """Parameters for the Docker ``SandboxConnection`` backend."""

    image: str = "ubuntu:22.04"

    @property
    def type(self) -> str:
        return "docker"


@dataclass(slots=True)
class E2BBackendParams(BackendParams):
    """Parameters for the E2B ``SandboxConnection`` backend."""

    template: str = "base"
    api_key: str = ""
    domain: str = ""
    timeout: int = 300
    env: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def type(self) -> str:
        return "e2b"


@dataclass(slots=True)
class LocalBackendParams(BackendParams):
    """Parameters for the local temp-dir ``SandboxConnection`` backend."""

    base_dir: str = "/tmp"

    @property
    def type(self) -> str:
        return "local_temp"
