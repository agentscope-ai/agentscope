# -*- coding: utf-8 -*-
"""The exception module in agentscope."""

from ._exception_base import AgentOrientedExceptionBase
from ._tool import (
    ToolInterruptedError,
    ToolNotFoundError,
    ToolInvalidArgumentsError,
)
from ._rag import (
    RAGExceptionBase,
    DatabaseConnectionError,
    GraphQueryError,
    EntityExtractionError,
    IndexNotFoundError,
)

__all__ = [
    "AgentOrientedExceptionBase",
    "ToolInterruptedError",
    "ToolNotFoundError",
    "ToolInvalidArgumentsError",
    "RAGExceptionBase",
    "DatabaseConnectionError",
    "GraphQueryError",
    "EntityExtractionError",
    "IndexNotFoundError",
]
