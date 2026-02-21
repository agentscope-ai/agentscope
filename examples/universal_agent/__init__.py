# -*- coding: utf-8 -*-
"""UniversalAgent - A comprehensive agent that integrates all AgentScope capabilities."""

from .config import UniversalAgentConfig
from .agent import UniversalAgent
from .intent_analyzer import IntentAnalyzer
from .model_selector import ModelSelector

__all__ = [
    "UniversalAgent",
    "UniversalAgentConfig", 
    "IntentAnalyzer",
    "ModelSelector",
]