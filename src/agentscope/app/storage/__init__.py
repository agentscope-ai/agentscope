# -*- coding: utf-8 -*-
"""The storage module in agentscope."""

from ._base import StorageBase
from ._redis_storage import RedisStorage
from ._model import (
    AgentRecord,
    CredentialRecord,
    SessionData,
    SessionRecord,
    UserRecord,
    WorkspaceRecord,
)

__all__ = [
    "StorageBase",
    "RedisStorage",
    "AgentRecord",
    "CredentialRecord",
    "SessionData",
    "SessionRecord",
    "UserRecord",
    "WorkspaceRecord",
]
