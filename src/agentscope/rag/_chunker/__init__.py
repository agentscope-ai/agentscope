# -*- coding: utf-8 -*-
"""Chunker implementations for the RAG indexing pipeline."""

from typing import Any

from ._approx_token_chunker import ApproxTokenChunker
from ._base import ChunkerBase

__all__ = [
    "ApproxTokenChunker",
    "ChunkerBase",
    "get_chunker_registry",
    "create_chunker_from_config",
]

# --------------- chunker registry ---------------

_CHUNKER_REGISTRY: dict[str, type[ChunkerBase]] = {}


def _register(cls: type[ChunkerBase]) -> None:
    _CHUNKER_REGISTRY[cls.chunker_type] = cls


def get_chunker_registry() -> dict[str, type[ChunkerBase]]:
    """Return a snapshot of all registered chunker classes."""
    return dict(_CHUNKER_REGISTRY)


def create_chunker_from_config(
    chunker_type: str,
    parameters: dict[str, Any] | None = None,
) -> ChunkerBase:
    """Instantiate a chunker from a persisted type + parameters pair.

    Args:
        chunker_type (`str`):
            The ``chunker_type`` identifier (e.g. ``"approx_token"``).
        parameters (`dict[str, Any] | None`, optional):
            Keyword arguments forwarded to
            :meth:`ChunkerBase.from_config`.

    Returns:
        `ChunkerBase`:
            A configured chunker instance.

    Raises:
        `KeyError`:
            When ``chunker_type`` is not in the registry.
    """
    cls = _CHUNKER_REGISTRY.get(chunker_type)
    if cls is None:
        raise KeyError(
            f"Unknown chunker type {chunker_type!r}. "
            f"Registered types: {sorted(_CHUNKER_REGISTRY)}",
        )
    return cls.from_config(**(parameters or {}))


# Register built-in chunkers
_register(ApproxTokenChunker)
