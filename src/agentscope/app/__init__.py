# -*- coding: utf-8 -*-
"""The FastAPI based agent service module, which contains all service-related
components and a configurable FastAPI app factory.
"""

from ._app import create_app
from ._manager import (
    BackgroundTaskManager,
    DockerWorkspaceManager,
    E2BWorkspaceManager,
    LocalWorkspaceManager,
    RLWorkspaceManager,
    SchedulerManager,
    SessionManager,
    WorkspaceManagerBase,
)
from ._middleware import (
    AGUIProtocolMiddleware,
    ProtocolMiddlewareBase,
    ToolOffloadMiddleware,
)
from .storage import (
    AgentRecord,
    CredentialRecord,
    RedisStorage,
    SessionConfig,
    SessionRecord,
    UserRecord,
)

__all__ = [
    "create_app",
    "ProtocolMiddlewareBase",
    "AGUIProtocolMiddleware",
    "ToolOffloadMiddleware",
    "RedisStorage",
    "AgentRecord",
    "CredentialRecord",
    "SessionConfig",
    "SessionRecord",
    "UserRecord",
    "WorkspaceManagerBase",
    "BackgroundTaskManager",
    "LocalWorkspaceManager",
    "DockerWorkspaceManager",
    "E2BWorkspaceManager",
    "RLWorkspaceManager",
    "BackgroundTaskManager",
    "SchedulerManager",
    "SessionManager",
]
