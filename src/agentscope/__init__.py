# -*- coding: utf-8 -*-
"""The agentscope serialization module"""
import warnings

from . import exception
from . import message
from . import model
from . import tool
from . import formatter
from . import agent
from . import embedding
from . import tracing
from ._logging import (
    logger,
    setup_logger,
)
from ._version import __version__

# Raise each warning only once
warnings.filterwarnings("once", category=DeprecationWarning)


__all__ = [
    # modules
    "exception",
    "message",
    "model",
    "tool",
    "formatter",
    "agent",
    "logger",
    "embedding",
    "tracing",
    # functions
    "setup_logger",
    "__version__",
]
