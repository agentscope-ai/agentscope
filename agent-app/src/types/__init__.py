"""
共享类型模块

定义跨域共享的数据类型、枚举和异常。
"""

from .base_models import (
    BaseModel,
    TimestampMixin,
    IDMixin,
    PaginationParams,
    PaginatedResponse,
)
from .events import Event, EventType
from .exceptions import (
    BaseError,
    NotFoundError,
    ValidationError,
    DatabaseError,
    ConfigError,
    PromptError,
    KnowledgeError,
    PKMError,
)

__all__ = [
    # 基础模型
    "BaseModel",
    "TimestampMixin",
    "IDMixin",
    "PaginationParams",
    "PaginatedResponse",
    # 事件
    "Event",
    "EventType",
    # 异常
    "BaseError",
    "NotFoundError",
    "ValidationError",
    "DatabaseError",
    "ConfigError",
    "PromptError",
    "KnowledgeError",
    "PKMError",
]