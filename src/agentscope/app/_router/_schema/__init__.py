# -*- coding: utf-8 -*-
"""Schema models for the agent service."""

from ._channel import (
    ChannelActionResponse,
    ChannelChatId,
    ChannelChatIdsResponse,
    ChannelResponse,
    ChannelSessionsResponse,
    CreateChannelRequest,
    UpdateChannelRequest,
)
from ._chat import ChatRequest, ChatTriggerResponse
from ._health import ComponentStatus, HealthResponse
from ._hub import HubInfo
from ._hub_mcp import InstallMCPRequest, MCPView, UpdateMCPRequest
from ._hub_skill import SkillView
from ._workspace import (
    AddFromLibraryRequest,
    AddFromLibraryResponse,
    AddSkillRequest,
    AddSkillsFromLibraryRequest,
    DirectoryEntry,
    DirectoryListing,
    DownloadTokenResponse,
    MCPClientStatus,
    ToolInfo,
)
from ._embedding_model import (
    ListEmbeddingModelsResponse,
    ListEmbeddingModelsRequest,
)
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
    ListDocumentChunksResponse,
    DocumentDownloadTokenResponse,
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
    # Health
    "ComponentStatus",
    "HealthResponse",
    # Hub
    "HubInfo",
    "InstallMCPRequest",
    "MCPView",
    "UpdateMCPRequest",
    "SkillView",
    # Workspace
    "AddFromLibraryRequest",
    "AddFromLibraryResponse",
    "AddSkillRequest",
    "AddSkillsFromLibraryRequest",
    "DirectoryEntry",
    "DirectoryListing",
    "DownloadTokenResponse",
    "MCPClientStatus",
    "ToolInfo",
    # Agent
    "AgentSchemaResponse",
    "AgentSchemaV2Response",
    "ListAgentsResponse",
    "CreateAgentRequest",
    "CreateAgentResponse",
    "UpdateAgentRequest",
    "ListSchedulesResponse",
    # Channel
    "ChannelActionResponse",
    "ChannelChatId",
    "ChannelChatIdsResponse",
    "ChannelResponse",
    "ChannelSessionsResponse",
    "CreateChannelRequest",
    "UpdateChannelRequest",
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
    "ListDocumentChunksResponse",
    "DocumentDownloadTokenResponse",
    "ListKnowledgeDocumentsResponse",
    "ListKnowledgeDocumentStatusResponse",
    "ListSupportedContentTypesResponse",
    "SearchKnowledgeBaseRequest",
    "SearchKnowledgeBaseResponse",
    "UpdateKnowledgeBaseRequest",
    "UploadKnowledgeDocumentResponse",
    # Model
    "ListEmbeddingModelsRequest",
    "ListEmbeddingModelsResponse",
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
