# -*- coding: utf-8 -*-
"""OpenSandbox-backed workspace.

OpenSandbox support follows the E2B remote-sandbox model: one sandbox
per workspace id, filesystem persistence inside the sandbox, and MCPs
reached through the in-sandbox gateway.
"""

from ._opensandbox_backend import OpenSandboxBackend
from ._opensandbox_workspace import OpenSandboxWorkspace

__all__ = ["OpenSandboxWorkspace", "OpenSandboxBackend"]
