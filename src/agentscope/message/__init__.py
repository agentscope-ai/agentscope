# -*- coding: utf-8 -*-
"""The message module in agentscope."""

from ._block import (
    ContentBlock,
    ContentBlockTypes,
    TextBlock,
    ThinkingBlock,
    HintBlock,
    ToolCallBlock,
    ToolResultBlock,
    DataBlock,
    Base64Source,
    URLSource,
)
from ._base import Msg, UserMsg, AssistantMsg, SystemMsg


__all__ = [
    "TextBlock",
    "ThinkingBlock",
    "HintBlock",
    "ToolCallBlock",
    "ToolResultBlock",
    "DataBlock",
    "Base64Source",
    "URLSource",
    "ContentBlock",
    "ContentBlockTypes",
    "Msg",
    "UserMsg",
    "AssistantMsg",
    "SystemMsg",
]
