# -*- coding: utf-8 -*-
"""Storage models for persisted resources."""

from ._agent import AgentRecord, AgentData, InviteConfig
from ._credential import CredentialRecord
from ._knowledge_base import KnowledgeBaseData, KnowledgeBaseRecord
from ._knowledge_document import (
    KnowledgeDocumentData,
    KnowledgeDocumentRecord,
    KnowledgeDocumentStatus,
)
from ._schedule import ScheduleData, ScheduleRecord, ScheduleSource
from ._session import (
    SessionRecord,
    SessionConfig,
    SessionKnowledgeConfig,
    ChatModelConfig,
    TTSModelConfig,
    EmbeddingModelConfig,
    SessionSource,
)
from ._team import TeamRecord, TeamData, TeamMember
from ._user import UserRecord
from ._voice_profile import (
    ENGINE_GPU_REQUIREMENT,
    ENGINE_SOURCE,
    ENGINE_TO_CREDENTIAL_TYPE,
    ENGINE_VOICE_CLONING,
    VoiceProfileData,
    VoiceProfileRecord,
)

__all__ = [
    "AgentData",
    "AgentRecord",
    "CredentialRecord",
    "KnowledgeBaseData",
    "KnowledgeBaseRecord",
    "KnowledgeDocumentData",
    "KnowledgeDocumentRecord",
    "KnowledgeDocumentStatus",
    "ScheduleData",
    "ScheduleRecord",
    "ScheduleSource",
    "SessionConfig",
    "SessionKnowledgeConfig",
    "SessionRecord",
    "SessionSource",
    "ChatModelConfig",
    "TTSModelConfig",
    "EmbeddingModelConfig",
    "TeamData",
    "TeamRecord",
    "TeamMember",
    "UserRecord",
    "InviteConfig",
    "ENGINE_TO_CREDENTIAL_TYPE",
    "ENGINE_SOURCE",
    "ENGINE_GPU_REQUIREMENT",
    "ENGINE_VOICE_CLONING",
    "VoiceProfileData",
    "VoiceProfileRecord",
]
