# -*- coding: utf-8 -*-
"""Apple-Container-backed workspace.

Uses Apple's ``container`` CLI to run Linux containers as lightweight
VMs on macOS 26+ with Apple silicon. MCP servers run *inside* the
container behind a FastAPI gateway and are reached through
:class:`GatewayClient` via :class:`AppleContainerBackend`.

Requires:
- macOS 26+ with Apple silicon
- ``container`` CLI installed
- ``container system start`` running
"""

from ._applecontainer_backend import AppleContainerBackend
from ._applecontainer_workspace import AppleContainerWorkspace

__all__ = ["AppleContainerBackend", "AppleContainerWorkspace"]
