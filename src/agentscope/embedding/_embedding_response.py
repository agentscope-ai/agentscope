# -*- coding: utf-8 -*-
"""The embedding response class."""
from dataclasses import dataclass, field
from typing import Literal, List

from ._embedding_usage import EmbeddingUsage
from .._utils._common import _get_timestamp
from .._utils._mixin import DictMixin
from ..types import Embedding


@dataclass
class EmbeddingResponse(DictMixin):
    """The embedding response class."""

    embeddings: List[Embedding]
    """The embedding data"""

    id: str = field(default_factory=lambda: _get_timestamp(True))
    """The identity of the embedding response"""

    created_at: str = field(default_factory=_get_timestamp)
    """The timestamp of the embedding response creation"""

    type: Literal["embedding"] = field(default_factory=lambda: "embedding")
    """The type of the response, must be `embedding`."""

    usage: EmbeddingUsage | None = field(default_factory=lambda: None)
    """The usage of the embedding model API invocation, if available."""

    source: Literal["cache", "api"] = field(default_factory=lambda: "api")
    """If the response comes from the cache or the API."""
