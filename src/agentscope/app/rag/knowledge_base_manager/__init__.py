# -*- coding: utf-8 -*-
"""Knowledge base manager classes.

The manager owns the lifecycle of knowledge bases:

- creation / deletion / listing of :class:`KnowledgeBaseRecord` rows,
- allocation / drop of the matching vector store storage,
- construction of :class:`~agentscope.rag.KnowledgeBase` runtime handles
  used by both the HTTP service and the agent runtime.

The MVP ships a single isolation strategy
(:class:`CollectionPerKbManager`); future strategies will live
alongside it.
"""

from ._base import KnowledgeBaseManagerBase
from ._collection_per_kb import CollectionPerKbManager
from ._dimension_policy import DimensionPolicy, DimensionPolicyKind
from ._errors import (
    DimensionPolicyError,
    KnowledgeBaseError,
    KnowledgeBaseNotFoundError,
)

__all__ = [
    "CollectionPerKbManager",
    "DimensionPolicy",
    "DimensionPolicyError",
    "DimensionPolicyKind",
    "KnowledgeBaseError",
    "KnowledgeBaseManagerBase",
    "KnowledgeBaseNotFoundError",
]
