# -*- coding: utf-8 -*-
"""
The message module of AgentScope.
"""

# 先导入基础模块
from ._message_base import Msg
from ._message_block import (
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    ImageBlock,
    AudioBlock,
    VideoBlock,
    ThinkingBlock,
    ContentBlock,
    Base64Source,
    URLSource,
)

__all__ = [
    "Msg",
    "ToolUseBlock",
    "ToolResultBlock",
    "TextBlock",
    "ImageBlock",
    "AudioBlock",
    "VideoBlock",
    "ThinkingBlock",
    "ContentBlock",
]
