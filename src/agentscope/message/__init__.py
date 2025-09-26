# -*- coding: utf-8 -*-
"""The message module of AgentScope."""

# from .msg import (
#     Msg,
# )

# from .block import (
#     ToolUseBlock,
#     ToolResultBlock,
#     TextBlock,
#     ImageBlock,
#     AudioBlock,
#     VideoBlock,
#     FileBlock,
#     ContentBlock,
# )

from ._message_base import Msg
from ._message_block import (
    ContentBlock,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    ToolResultBlock,
    ImageBlock,
    AudioBlock,
    VideoBlock,
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
    # "FileBlock",
    "ThinkingBlock",
    "ContentBlock",
]
