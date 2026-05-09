# -*- coding: utf-8 -*-
"""The offload module in agentscope."""

from ._base import WorkspaceBase
from ._local import LocalWorkspace

__all__ = [
    "WorkspaceBase",
    "LocalWorkspace",
]
