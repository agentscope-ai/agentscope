# -*- coding: utf-8 -*-
"""The workspace module in agentscope."""

from typing import Annotated, Union

from pydantic import Field

from ._base import WorkspaceBase
from ._local_workspace import LocalWorkspace

AnyWorkspace = Annotated[Union[LocalWorkspace], Field(discriminator="type")]

__all__ = [
    "AnyWorkspace",
    "WorkspaceBase",
    "LocalWorkspace",
]
