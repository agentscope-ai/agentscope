# -*- coding: utf-8 -*-
"""Tracing public API with lazy decorator imports to avoid circular deps."""

from __future__ import annotations

from types import ModuleType
from typing import Any

from ._setup import setup_tracing

__all__ = [
    "setup_tracing",
    "trace",
    "trace_llm",
    "trace_reply",
    "trace_format",
    "trace_toolkit",
    "trace_embedding",
]


_TRACE_EXPORTS = {
    "trace",
    "trace_llm",
    "trace_reply",
    "trace_format",
    "trace_toolkit",
    "trace_embedding",
}

_trace_module: ModuleType | None = None


def __getattr__(name: str) -> Any:
    """Lazily import decorators to break tracing↔model circular imports."""
    global _trace_module  # noqa: PLW0603
    if name not in _TRACE_EXPORTS:
        raise AttributeError(f"module {__name__} has no attribute {name}")

    if _trace_module is None:
        from . import _trace as trace_mod

        _trace_module = trace_mod

    return getattr(_trace_module, name)
