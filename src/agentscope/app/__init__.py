# -*- coding: utf-8 -*-
"""The FastAPI based agent service module, which contains all service-related
components and a configurable FastAPI app factory.
"""

from ._app import create_app
from ._middleware import (
    ProtocolMiddlewareBase,
    AGUIProtocolMiddleware,
    ToolOffloadMiddleware,
)
from .storage import (
    RedisStorage,
    AgentRecord,
    CredentialRecord,
    SessionConfig,
    SessionRecord,
    UserRecord,
)
from ._manager import (
    WorkspaceManagerBase,
    LocalWorkspaceManager,
    DockerWorkspaceManager,
    E2BWorkspaceManager,
    BackgroundTaskManager,
    SchedulerManager,
    SessionManager,
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
    "BackgroundTaskManager",
    "SchedulerManager",
    "SessionManager",
]
