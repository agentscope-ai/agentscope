# -*- coding: utf-8 -*-
"""The storage module in agentscope."""

from ._base import StorageBase
from ._redis_storage import RedisStorage, RedisKeyConfig
from ._model import (
    AgentRecord,
    CredentialRecord,
    ScheduleData,
    ScheduleRecord,
    SessionConfig,
    SessionRecord,
    ChatModelConfig,
    UserRecord,
)

__all__ = [
    "StorageBase",
    "RedisKeyConfig",
    "RedisStorage",
    "AgentRecord",
    "CredentialRecord",
    "SessionConfig",
    "SessionRecord",
    "ChatModelConfig",
    "UserRecord",
    "ScheduleData",
    "ScheduleRecord",
]
