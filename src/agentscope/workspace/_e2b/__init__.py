# -*- coding: utf-8 -*-
"""E2B-backed workspace package.

Re-exports :class:`E2BWorkspace` so callers can write
``from agentscope.workspace._e2b import E2BWorkspace`` without having to
poke at the underlying module layout.
"""

from ._e2b_workspace import E2BWorkspace
from ._e2b_backend import E2BBackend

__all__ = ["E2BWorkspace", "E2BBackend"]
