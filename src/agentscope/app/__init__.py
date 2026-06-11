# -*- coding: utf-8 -*-
"""The FastAPI based agent service module, which contains all service-related
components and a configurable FastAPI app factory.
"""

from ._app import create_app
from ._types import SubAgentTemplate
from ._service import (
    ChatService,
    SessionService,
    get_toolkit,
    get_model,
    get_tts_model,
    get_embedding_model,
)

__all__ = [
    "create_app",
    "SubAgentTemplate",
    "ChatService",
    "SessionService",
    "get_toolkit",
    "get_model",
    "get_tts_model",
    "get_embedding_model",
]
