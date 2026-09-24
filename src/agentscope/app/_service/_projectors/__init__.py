# -*- coding: utf-8 -*-
"""Built-in event projectors.

Each projector mirrors one cross-session UI feed onto the owning
session via the shared
:class:`~agentscope.app._service._session_projection.SessionProjection`
primitive. See :class:`~agentscope.app._types.EventProjector`.
"""
from ._subagent_hitl import SubagentHitlProjector

__all__ = [
    "SubagentHitlProjector",
]
