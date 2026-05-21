# -*- coding: utf-8 -*-
"""Schema models for the agent service."""

from ._chat import ChatRequest
from ._model import ListModelsResponse, ListModelsRequest
from ._schedule import (
    CreateScheduleRequest,
    CreateScheduleResponse,
    ListSchedulesResponse,
    ScheduleSessionsResponse,
    UpdateScheduleRequest,
)
from ._agent import (
    ListAgentsResponse,
    CreateAgentRequest,
    CreateAgentResponse,
    UpdateAgentRequest,
)
from ._background_task import (
    ListBackgroundTasksResponse,
    BackgroundTaskInfo,
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
)

__all__ = [
    # Agent
    "ListAgentsResponse",
    "CreateAgentRequest",
    "CreateAgentResponse",
    "UpdateAgentRequest",
    "ListSchedulesResponse",
    # Background Task
    "ListBackgroundTasksResponse",
    "BackgroundTaskInfo",
    # Chat
    "ChatRequest",
    # Credential
    "CreateCredentialRequest",
    "CreateCredentialResponse",
    "UpdateCredentialRequest",
    "ListCredentialsResponse",
    "ListCredentialSchemasResponse",
    # Model
    "ListModelsRequest",
    "ListModelsResponse",
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
]
