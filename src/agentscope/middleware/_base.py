# -*- coding: utf-8 -*-
""""""
from typing import Callable

from agentscope.message import Msg
from agentscope.model import ChatResponse
from agentscope.tool import ToolResponse


class AgentMiddleWareBase:
    """The base class for all middlewares."""

    async def wrap_model_call(self, request: Callable[..., ChatResponse]) -> None:
        """Wrap the model call with additional functionality."""

    async def wrap_call_tool_function(self, request: Callable[..., ToolResponse]) -> None:
        """Wrap the tool call function with additional functionality."""

    async def wrap_reasoning(self, kwargs: dict, handler: Callable[..., Msg]) -> None:
        """Wrap the reasoning process with additional functionality."""

    async def wrap_acting(self, ) -> None:
        """Wrap the acting process with additional functionality."""

    async def wrap_replying(self):