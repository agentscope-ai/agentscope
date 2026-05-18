# -*- coding: utf-8 -*-
"""App routers."""
from ._agent import agent_router
from ._background_task import background_task_router
from ._chat import chat_router
from ._credential import credential_router
from ._schedule import schedule_router
from ._session import session_router
from ._model import model_router
from ._workspace import workspace_router

__all__ = [
    "agent_router",
    "background_task_router",
    "model_router",
    "chat_router",
    "credential_router",
    "schedule_router",
    "session_router",
    "workspace_router",
]
