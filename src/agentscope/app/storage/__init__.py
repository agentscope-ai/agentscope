# -*- coding: utf-8 -*-
"""The storage module in agentscope."""
from typing import TYPE_CHECKING

from ._base import StorageBase
from ._redis_storage import RedisStorage
from ._model import (
    AgentData,
    AgentRecord,
    CredentialRecord,
    KnowledgeBaseData,
    KnowledgeBaseRecord,
    KnowledgeDocumentData,
    KnowledgeDocumentRecord,
    KnowledgeDocumentStatus,
    ScheduleData,
    ScheduleRecord,
    ScheduleSource,
    SessionConfig,
    SessionKnowledgeConfig,
    SessionRecord,
    SessionSource,
    ChatModelConfig,
    TTSModelConfig,
    EmbeddingModelConfig,
    TeamData,
    TeamRecord,
    UserRecord,
    TeamMember,
    InviteConfig,
)

if TYPE_CHECKING:
    # Re-export for static type checkers only; the actual class is
    # imported lazily by :func:`__getattr__` so users without the
    # ``sql`` extra do not pay the SQLAlchemy import cost.
    from ._sql import SqlStorage  # noqa: F401


def __getattr__(name: str) -> object:
    """Lazy loader for optional backends.

    Delays the SQLAlchemy import triggered by :class:`SqlStorage`
    until a caller actually asks for it — the rest of
    :mod:`agentscope.app.storage` (records, ``RedisStorage``,
    ``StorageBase``) stays import-cheap for users that never touch
    SQL.
    """
    if name == "SqlStorage":
        from ._sql import SqlStorage as _SqlStorage

        return _SqlStorage
    raise AttributeError(
        f"module 'agentscope.app.storage' has no attribute {name!r}",
    )


__all__ = [
    "StorageBase",
    "RedisStorage",
    "SqlStorage",
    # The ORM models
    "InviteConfig",
    "AgentData",
    "AgentRecord",
    "CredentialRecord",
    "KnowledgeBaseData",
    "KnowledgeBaseRecord",
    "KnowledgeDocumentData",
    "KnowledgeDocumentRecord",
    "KnowledgeDocumentStatus",
    "SessionConfig",
    "SessionKnowledgeConfig",
    "SessionRecord",
    "SessionSource",
    "ChatModelConfig",
    "TTSModelConfig",
    "EmbeddingModelConfig",
    "TeamMember",
    "TeamData",
    "TeamRecord",
    "UserRecord",
    "ScheduleData",
    "ScheduleRecord",
    "ScheduleSource",
]
