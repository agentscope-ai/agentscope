# -*- coding: utf-8 -*-
"""WeCom agent tools, one per module.

All three send to a chat or person other than the current conversation.
The AI bot API exposes no directory lookup, so unlike Feishu there are no
discovery tools — the target id comes from the user or from a chat the
agent has already seen.
"""
from ._send_file import SendFile
from ._send_image import SendImage
from ._send_message import SendMessage

__all__ = [
    "SendFile",
    "SendImage",
    "SendMessage",
]
