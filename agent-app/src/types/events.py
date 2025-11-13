"""
事件定义

定义系统中使用的各种事件类型。
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4


class EventType(str, Enum):
    """事件类型枚举"""
    # 知识相关事件
    KNOWLEDGE_CREATED = "knowledge.created"
    KNOWLEDGE_UPDATED = "knowledge.updated"
    KNOWLEDGE_DELETED = "knowledge.deleted"
    KNOWLEDGE_SYNCED = "knowledge.synced"

    # Prompt相关事件
    PROMPT_CREATED = "prompt.created"
    PROMPT_UPDATED = "prompt.updated"
    PROMPT_DELETED = "prompt.deleted"
    PROMPT_RENDERED = "prompt.rendered"

    # PKM相关事件
    PKM_FILE_CREATED = "pkm.file_created"
    PKM_FILE_UPDATED = "pkm.file_updated"
    PKM_FILE_DELETED = "pkm.file_deleted"
    PKM_SYNC_STARTED = "pkm.sync_started"
    PKM_SYNC_COMPLETED = "pkm.sync_completed"
    PKM_SYNC_FAILED = "pkm.sync_failed"

    # 智能体相关事件
    AGENT_STARTED = "agent.started"
    AGENT_STOPPED = "agent.stopped"
    AGENT_ERROR = "agent.error"
    AGENT_TASK_CREATED = "agent.task_created"
    AGENT_TASK_COMPLETED = "agent.task_completed"

    # 配置相关事件
    CONFIG_LOADED = "config.loaded"
    CONFIG_UPDATED = "config.updated"
    CONFIG_VALIDATED = "config.validated"

    # 系统相关事件
    SYSTEM_STARTED = "system.started"
    SYSTEM_STOPPED = "system.stopped"
    SYSTEM_ERROR = "system.error"


@dataclass
class Event:
    """事件数据类"""
    type: EventType
    data: dict[str, Any]
    source: str
    event_id: UUID = Field(default_factory=uuid4)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    correlation_id: Optional[UUID] = None
    version: str = "1.0"
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "event_id": str(self.event_id),
            "type": self.type.value,
            "data": self.data,
            "source": self.source,
            "timestamp": self.timestamp.isoformat(),
            "correlation_id": str(self.correlation_id) if self.correlation_id else None,
            "version": self.version,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Event":
        """从字典创建事件"""
        return cls(
            type=EventType(data["type"]),
            data=data["data"],
            source=data["source"],
            event_id=UUID(data["event_id"]),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            correlation_id=UUID(data["correlation_id"]) if data.get("correlation_id") else None,
            version=data.get("version", "1.0"),
            metadata=data.get("metadata", {}),
        )


# 便捷的事件创建函数
def create_knowledge_event(
    event_type: EventType,
    knowledge_id: UUID,
    data: dict[str, Any],
    source: str = "knowledge_service",
) -> Event:
    """创建知识相关事件"""
    event_data = {
        "knowledge_id": str(knowledge_id),
        **data,
    }
    return Event(type=event_type, data=event_data, source=source)


def create_prompt_event(
    event_type: EventType,
    prompt_id: UUID,
    data: dict[str, Any],
    source: str = "prompt_service",
) -> Event:
    """创建Prompt相关事件"""
    event_data = {
        "prompt_id": str(prompt_id),
        **data,
    }
    return Event(type=event_type, data=event_data, source=source)


def create_pkm_event(
    event_type: EventType,
    file_path: str,
    data: dict[str, Any],
    source: str = "pkm_service",
) -> Event:
    """创建PKM相关事件"""
    event_data = {
        "file_path": file_path,
        **data,
    }
    return Event(type=event_type, data=event_data, source=source)