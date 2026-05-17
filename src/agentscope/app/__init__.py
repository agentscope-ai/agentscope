# -*- coding: utf-8 -*-
"""The FastAPI based agent service module, which contains all service-related
components and a configurable FastAPI app factory.
"""

from ._app import create_app
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
    BackgroundTaskManager,
    SchedulerManager,
    SessionManager,
)

__all__ = [
    "create_app",
    "RedisStorage",
    "AgentRecord",
    "CredentialRecord",
    "SessionConfig",
    "SessionRecord",
    "UserRecord",
    "WorkspaceManagerBase",
    "BackgroundTaskManager",
    "LocalWorkspaceManager",
    "BackgroundTaskManager",
    "SchedulerManager",
    "SessionManager",
]
