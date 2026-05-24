# -*- coding: utf-8 -*-
"""The workspace module in agentscope."""


from ._base import WorkspaceBase
from ._local_workspace import LocalWorkspace
from ._offload_protocol import Offloader
from ._docker import DockerWorkspace
from ._e2b import E2BWorkspace


__all__ = [
    "WorkspaceBase",
    "LocalWorkspace",
    "DockerWorkspace",
    "E2BWorkspace",
    "Offloader",
]
