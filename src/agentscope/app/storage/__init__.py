# -*- coding: utf-8 -*-
"""The storage module in agentscope."""

from ._base import StorageBase
from ._redis_storage import RedisStorage
from ._model import (
    AgentData,
    AgentRecord,
    CredentialRecord,
    ScheduleData,
    ScheduleRecord,
    ScheduleSource,
    SessionConfig,
    SessionRecord,
    SessionSource,
    ChatModelConfig,
    TTSModelConfig,
    EmbeddingModelConfig,
    TeamData,
    TeamRecord,
    UserRecord,
)

__all__ = [
    "StorageBase",
    "RedisStorage",
    # The ORM models
    "AgentData",
    "AgentRecord",
    "CredentialRecord",
    "SessionConfig",
    "SessionRecord",
    "SessionSource",
    "ChatModelConfig",
    "TTSModelConfig",
    "EmbeddingModelConfig",
    "TeamData",
    "TeamRecord",
    "UserRecord",
    "ScheduleData",
    "ScheduleRecord",
    "ScheduleSource",
]
