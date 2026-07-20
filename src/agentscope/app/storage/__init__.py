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
    # Re-exported for static type checkers only; at runtime the class is
    # loaded lazily by ``__getattr__`` below so callers without the
    # optional ``sql`` extra never trigger the SQLAlchemy import that the
    # ``_sql`` package does at load time (its declarative-tables module
    # cannot defer it).
    from ._sql import AsyncSQLAlchemyStorage  # noqa: F401


def __getattr__(name: str) -> object:
    """Lazily load the optional SQL backend on first attribute access.

    Keeps ``import agentscope.app.storage`` cheap — ``StorageBase``,
    ``RedisStorage`` and the record models never need SQLAlchemy — while
    still exposing ``AsyncSQLAlchemyStorage`` from this package for
    callers that do ``from agentscope.app.storage import
    AsyncSQLAlchemyStorage``. SQLAlchemy is imported only at that point.
    """
    if name == "AsyncSQLAlchemyStorage":
        from ._sql import AsyncSQLAlchemyStorage as _AsyncSQLAlchemyStorage

        return _AsyncSQLAlchemyStorage
    raise AttributeError(
        f"module 'agentscope.app.storage' has no attribute {name!r}",
    )


__all__ = [
    "StorageBase",
    "RedisStorage",
    "AsyncSQLAlchemyStorage",
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
