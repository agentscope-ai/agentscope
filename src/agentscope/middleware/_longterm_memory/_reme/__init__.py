# -*- coding: utf-8 -*-
"""ReMe-backed long-term memory middleware for AgentScope.

`ReMe <https://github.com/agentscope-ai/ReMe>`_ (``reme-ai``) is a
file-based memory toolkit built on AgentScope. This package embeds the
ReMe application in-process and exposes:

- :class:`ReMeMiddleware` — AgentScope middleware wiring an agent to an
  embedded ``reme.ReMe`` app (holds the app directly, mirroring how
  :class:`Mem0Middleware` holds a native ``mem0`` client).
"""
from ._middleware import ReMeMiddleware

__all__ = [
    "ReMeMiddleware",
]
