# -*- coding: utf-8 -*-
"""Channel module — connect AgentScope agents to IM platforms.

Channels translate a platform (DingTalk, Feishu, ...) to/from normalised
events;
the stateless :class:`ChannelGateway` orchestrates each event;
:class:`~agentscope.app._service.ChannelService` owns CRUD;
:class:`ChannelLifecycleDispatcher` keeps this node's live instances
reconciled with storage.
"""
from ._base import (
    ChannelBase,
    ChannelCapability,
    ChannelConfirmationResultEvent,
    ChannelEvent,
    ChannelHeartbeat,
    ChannelStatus,
    ChatKind,
)
from ._clients import ChannelClients
from ._dispatcher import ChannelLifecycleDispatcher
from ._errors import ChannelError
from ._gateway import ChannelGateway
from ._registry import ChannelTypeRegistry, ChannelTypeSchema
from ._dingtalk import DingTalkChannel
from ._discord import DiscordChannel
from ._feishu import FeishuChannel

__all__ = [
    "ChannelBase",
    "ChannelClients",
    "ChannelCapability",
    "ChannelConfirmationResultEvent",
    "ChannelError",
    "ChannelHeartbeat",
    "ChannelEvent",
    "ChannelStatus",
    "ChatKind",
    "ChannelGateway",
    "ChannelLifecycleDispatcher",
    "ChannelTypeRegistry",
    "ChannelTypeSchema",
    "DingTalkChannel",
    "DiscordChannel",
    "FeishuChannel",
]
