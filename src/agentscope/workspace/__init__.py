# -*- coding: utf-8 -*-
"""The workspace module in agentscope."""


from ._base import WorkspaceBase
from ._local_workspace import LocalWorkspace
from ._offload_protocol import Offloader
from ._docker import DockerBackend, DockerWorkspace
from ._e2b import E2BWorkspace, E2BBackend


__all__ = [
    "WorkspaceBase",
    "LocalWorkspace",
    "DockerBackend",
    "DockerWorkspace",
    "E2BBackend",
    "E2BWorkspace",
    "Offloader",
]
