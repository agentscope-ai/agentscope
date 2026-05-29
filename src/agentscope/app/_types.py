# -*- coding: utf-8 -*-
"""Shared type aliases for the agentscope app layer."""
from collections.abc import Awaitable, Callable

from ..middleware import MiddlewareBase
from ..tool import ToolBase


AgentMiddlewareFactory = Callable[
    [str, str, str],
    Awaitable[list[MiddlewareBase]],
]
# Async factory signature: ``(user_id, agent_id, session_id)`` →
# awaitable of :class:`~agentscope.middleware.MiddlewareBase` instances.

AgentToolFactory = Callable[
    [str, str, str],
    Awaitable[list[ToolBase]],
]
#  Async factory signature: ``(user_id, agent_id, session_id)`` →
#  awaitable of :class:`~agentscope.tool.ToolBase` instances.
