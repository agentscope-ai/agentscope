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
from ._session import (
    CreateSessionRequest,
    CreateSessionResponse,
    UpdateSessionRequest,
    ListSessionsResponse,
    ListMessagesResponse,
    SessionView,
    TeamDetailResponse,
    TeamMemberView,
)

__all__ = [
    # Agent
    "AgentSchemaResponse",
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
    "UpdateSessionRequest",
    "ListSessionsResponse",
    "ListMessagesResponse",
    "SessionView",
    "TeamDetailResponse",
    "TeamMemberView",
]
