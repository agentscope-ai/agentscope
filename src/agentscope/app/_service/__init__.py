# -*- coding: utf-8 -*-
"""Service layer for the AgentScope app."""
from ._chat import ChatService
from ._model import get_model
from ._agent import get_agent

__all__ = ["ChatService", "get_model", "get_agent"]
