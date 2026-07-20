# -*- coding: utf-8 -*-
"""The workspace module in agentscope."""


from ._base import WorkspaceBase
from ._local_workspace import LocalWorkspace
from ._offload_protocol import Offloader
from ._docker import DockerBackend, DockerWorkspace
from ._e2b import E2BWorkspace, E2BBackend
from ._daytona import DaytonaBackend, DaytonaWorkspace
from ._k8s import K8sBackend, K8sWorkspace
from ._opensandbox import OpenSandboxBackend, OpenSandboxWorkspace


__all__ = [
    "WorkspaceBase",
    "LocalWorkspace",
    "DockerBackend",
    "DockerWorkspace",
    "E2BBackend",
    "E2BWorkspace",
    "DaytonaBackend",
    "DaytonaWorkspace",
    "K8sBackend",
    "K8sWorkspace",
    "Offloader",
    "OpenSandboxBackend",
    "OpenSandboxWorkspace",
]
