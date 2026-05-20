# -*- coding: utf-8 -*-
"""The tracing interface class in agentscope."""

from ._setup import setup_tracing
from ._trace import TracingMiddleware

__all__ = [
    "setup_tracing",
    "TracingMiddleware",
]
