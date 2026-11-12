# -*- coding: utf-8 -*-
"""Execution context shared by nested callers and acting middleware."""

from contextvars import ContextVar


nested_tool_call: ContextVar[bool] = ContextVar(
    "agentscope_nested_tool_call",
    default=False,
)
