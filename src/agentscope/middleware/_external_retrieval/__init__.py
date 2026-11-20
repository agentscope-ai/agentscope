# -*- coding: utf-8 -*-
"""External retrieval middleware and backends."""

from ._backend import RetrievalBackend, RetrievalResult
from ._middleware import ExternalRetrievalMiddleware
from ._ragflow import RAGFlowRetrievalBackend

__all__ = [
    "ExternalRetrievalMiddleware",
    "RetrievalBackend",
    "RetrievalResult",
    "RAGFlowRetrievalBackend",
]
