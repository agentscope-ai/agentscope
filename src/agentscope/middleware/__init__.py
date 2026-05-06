# -*- coding: utf-8 -*-
"""Middleware system for AgentScope agents.

This module provides the middleware infrastructure for intercepting and
modifying agent behavior at key execution points.

Middleware can be implemented by:
1. Subclassing MiddlewareBase and implementing one or more hooks
2. Using simple async functions for single-hook scenarios

Example:
    # Using MiddlewareBase
    class LoggingMiddleware(MiddlewareBase):
        async def on_reasoning(self, tool_choice, next_handler):
            print("Before reasoning")
            async for item in next_handler():
                yield item
            print("After reasoning")

    agent.register_middleware(LoggingMiddleware())

    # Using a simple function
    async def log_tool_calls(tool_call, next_handler):
        print(f"Executing: {tool_call.name}")
        async for item in next_handler():
            yield item

    agent.register_middleware(log_tool_calls, target="acting")
"""

from .base import MiddlewareBase

__all__ = [
    "MiddlewareBase",
]
