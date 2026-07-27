# -*- coding: utf-8 -*-
"""App routers."""
from ._agent import agent_router
from ._chat import chat_router
from ._credential import credential_router
from ._knowledge_base import knowledge_base_router
from ._schedule import schedule_router
from ._session import session_router
from ._model import model_router
from ._tts_model import tts_model_router
from ._workspace import workspace_router

__all__ = [
    "agent_router",
    "model_router",
    "tts_model_router",
    "chat_router",
    "credential_router",
    "knowledge_base_router",
    "schedule_router",
    "session_router",
    "workspace_router",
]
