# -*- coding: utf-8 -*-
"""Discord agent tools, one per module.

Two families forming a closed chain: **discovery** (``ListChats`` /
``ListChatMembers``) hands back a ``target_id`` + ``target_type`` pair that
**send** (``SendMessage`` / ``SendFile``) consumes to reach a channel/user
other than the current conversation. Images ride ``SendFile`` — Discord
uploads every media type through the same attachment path.
"""
from ._list_chat_members import ListChatMembers
from ._list_chats import ListChats
from ._send_file import SendFile
from ._send_message import SendMessage

__all__ = [
    "ListChatMembers",
    "ListChats",
    "SendFile",
    "SendMessage",
]
