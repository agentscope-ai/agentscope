# -*- coding: utf-8 -*-
"""Schema models for the agent service."""

from ._chat import ChatRequest, ChatTriggerResponse
from ._model import ListModelsResponse, ListModelsRequest
from ._tts_model import ListTTSModelsResponse, ListTTSModelsRequest
from ._schedule import (
    CreateScheduleRequest,
    CreateScheduleResponse,
    ListSchedulesResponse,
    ScheduleSessionsResponse,
    UpdateScheduleRequest,
)
from ._agent import (
    AgentSchemaResponse,
    AgentSchemaV2Response,
    ListAgentsResponse,
    CreateAgentRequest,
    CreateAgentResponse,
    UpdateAgentRequest,
)
from ._credential import (
    CreateCredentialRequest,
    CreateCredentialResponse,
    UpdateCredentialRequest,
    ListCredentialsResponse,
    ListCredentialSchemasResponse,
)
from ._knowledge_base import (
    ChunkerInfo,
    CreateKnowledgeBaseRequest,
    CreateKnowledgeBaseResponse,
    KbEmbeddingProvider,
    KbMiddlewareParametersSchemaResponse,
    KnowledgeDocumentView,
    ListChunkersResponse,
    ListKbEmbeddingModelsResponse,
    ListKnowledgeBasesResponse,
    ListKnowledgeDocumentsResponse,
    ListKnowledgeDocumentStatusResponse,
    ListSupportedContentTypesResponse,
    SearchKnowledgeBaseRequest,
    SearchKnowledgeBaseResponse,
    UpdateKnowledgeBaseRequest,
    UploadKnowledgeDocumentResponse,
)
from ._session import (
    CreateSessionRequest,
    CreateSessionResponse,
    InterruptSessionResponse,
    UpdateSessionRequest,
    ListSessionsResponse,
    ListMessagesResponse,
    SessionStatus,
    SessionStatusResponse,
    SessionView,
    TeamDetailResponse,
    TeamMemberView,
)

__all__ = [
    # Agent
    "AgentSchemaResponse",
    "AgentSchemaV2Response",
    "ListAgentsResponse",
    "CreateAgentRequest",
    "CreateAgentResponse",
    "UpdateAgentRequest",
    "ListSchedulesResponse",
    # Chat
    "ChatRequest",
    "ChatTriggerResponse",
    # Credential
    "CreateCredentialRequest",
    "CreateCredentialResponse",
    "UpdateCredentialRequest",
    "ListCredentialsResponse",
    "ListCredentialSchemasResponse",
    # Knowledge base
    "ChunkerInfo",
    "CreateKnowledgeBaseRequest",
    "CreateKnowledgeBaseResponse",
    "KbEmbeddingProvider",
    "KbMiddlewareParametersSchemaResponse",
    "KnowledgeDocumentView",
    "ListChunkersResponse",
    "ListKbEmbeddingModelsResponse",
    "ListKnowledgeBasesResponse",
    "ListKnowledgeDocumentsResponse",
    "ListKnowledgeDocumentStatusResponse",
    "ListSupportedContentTypesResponse",
    "SearchKnowledgeBaseRequest",
    "SearchKnowledgeBaseResponse",
    "UpdateKnowledgeBaseRequest",
    "UploadKnowledgeDocumentResponse",
    # Model
    "ListModelsRequest",
    "ListModelsResponse",
    # TTS Model
    "ListTTSModelsRequest",
    "ListTTSModelsResponse",
    # Schedule
    "CreateScheduleRequest",
    "CreateScheduleResponse",
    "ListSchedulesResponse",
    "ScheduleSessionsResponse",
    "UpdateScheduleRequest",
    # Session
    "CreateSessionRequest",
    "CreateSessionResponse",
    "InterruptSessionResponse",
    "UpdateSessionRequest",
    "ListSessionsResponse",
    "ListMessagesResponse",
    "SessionStatus",
    "SessionStatusResponse",
    "SessionView",
    "TeamDetailResponse",
    "TeamMemberView",
]
